# AgentEvent — canonical spec

**Version:** 1.0 (frozen 2026-05-04 per schema-freeze deliverable; tag `v1.0-frozen-2026-`)
**Status:** Locked 2026-05-03 by eng review. Implementations in conform to this doc.
**Stability:** structural changes are caught by `tests/test_schema_freeze.py` against the snapshot at `schemas/agent_event.v1.0.json`. Any diff requires an explicit version bump (1.x → 2.0 if breaking, 1.x → 1.y if additive) and a UI re-test before the snapshot is regenerated via `scripts/regen_schemas.py`.

## Conventions

- Discriminated union via `type:` field. Every event has a `type` literal that picks the variant.
- All fields are required unless explicitly marked optional or nullable.
- Timestamps are ISO 8601 with timezone (`2026-05-03T14:32:01.123Z`). Use UTC.
- IDs are UUIDv4 unless noted.
- Strings are UTF-8. No length limit on event content text (LLM streams are unbounded).
- Numeric durations are integers in milliseconds unless suffix says otherwise.

## Common base fields (every event)

```python
class AgentEventBase(BaseModel):
 type: str # discriminator — see variants below
 event_id: UUID # unique per event
 trace_id: UUID # groups events into a single agent run
 iteration_id: UUID | None # links to iterations.id when from a benchmark replay
 timestamp: datetime # ISO 8601 UTC
 parent_span_id: UUID | None # for nested events (e.g., tool_call_result references tool_call_start.event_id)
```

Rationale:
- `trace_id` is the canonical group. The `traces.events` JSONB array in the DB is one trace_id worth of events, ordered by `timestamp`.
- `parent_span_id` lets the UI nest tool_call_result under tool_call_start in the trace timeline view (.9).
- `iteration_id` is null for production traces (customer agent running outside the loop). Set for benchmark replays (M5, τ³).

## Variants

### `content_delta` — LLM streaming output

```python
class ContentDelta(AgentEventBase):
 type: Literal["content_delta"]
 text: str # incremental delta — append to prior cumulative_text
 model: str # e.g., "claude-opus-4-7"
 cumulative_text: str | None # full text so far (optional, populated when known)
```

OTel awareness: roughly maps to OTel Gen AI's `gen_ai.completion` span event with content streamed via `gen_ai.choice` events.

### `reasoning_delta` — model's reasoning tokens

```python
class ReasoningDelta(AgentEventBase):
 type: Literal["reasoning_delta"]
 text: str
 model: str
```

Captures Claude extended thinking, OpenAI o1-style reasoning. Stored separately from `content_delta` because reasoning tokens don't appear in customer-facing output and have different downstream uses (failure clustering can use them; the UI usually doesn't render them).

OTel awareness: not yet standardized in OTel Gen AI conventions. Closest analog is a custom span attribute.

### `tool_call_start`

```python
class ToolCallStart(AgentEventBase):
 type: Literal["tool_call_start"]
 call_id: str # provider's call id (e.g., "toolu_abc")
 name: str # tool name (e.g., "lookup_supplier", "write_skill")
 args: dict # JSON-serializable arguments
```

OTel awareness: maps to OTel Gen AI's tool-call span start.

### `tool_call_result`

```python
class ToolCallResult(AgentEventBase):
 type: Literal["tool_call_result"]
 call_id: str # matches a prior tool_call_start.call_id
 name: str
 status: Literal["ok", "error"]
 output: Any # JSON-serializable; may be large (truncate at storage layer if needed)
 duration_ms: int
 error: str | None # human-readable error message; non-null iff status="error"
 error_class: Literal["Timeout", "OOM", "Crash"] | None # D3 — sandbox failures
```

D3 enforcement: `error_class` is non-null when the tool call failed because the sandbox itself crashed/timed out/OOM-killed (as opposed to a logical failure inside the tool). The gate runner checks `error_class` to decide whether to advance `best_ever_score`:

| status | error_class | best_ever advance? | learnings.md entry? |
|---|---|---|---|
| `"ok"` | (n/a, must be null) | yes if val_score > best_ever | yes |
| `"error"` | null (logical error) | no | yes (the agent should learn from logical failures) |
| `"error"` | `"Timeout"` / `"OOM"` / `"Crash"` | no | yes (records the runtime issue) |

OTel awareness: tool-call span end with `gen_ai.tool.status` attribute. The `error_class` is ownEvo-specific.

### `skill_loaded`

```python
class SkillLoaded(AgentEventBase):
 type: Literal["skill_loaded"]
 skill_id: str
 version_seq: int
 retention_acknowledged: bool # agent confirmed it read the retention contract
```

Emitted when a skill enters the agent's context. The retention-violation eval class needs this — if a skill is loaded and `retention_acknowledged=false`, retention rules can't be tested for that turn.

OTel awareness: no OTel analog. ownEvo-specific.

### `citation`

```python
class Citation(AgentEventBase):
 type: Literal["citation"]
 ref: int # citation reference number in the agent's output (1, 2, 3...)
 source: str # source identifier (URL, document id, supplier doc reference)
 quote: str # the cited text
```

Used by the audit log (which decisions cited which sources) and by the retention eval class (verify the agent re-read the source rather than caching).

### `monitor_signal`

```python
class MonitorSignal(AgentEventBase):
 type: Literal["monitor_signal"]
 monitor: Literal["loop_detection", "redundancy", "context_near_limit"] # MVP set; extensible
 severity: Literal["info", "warn", "error"]
 details: dict | None # monitor-specific structured payload
```

The 3 programmatic monitors per MVP doc § OPTIONAL. Non-product, just signals into the loop. New monitors can be added by extending the `Literal` union — schema-freeze rule applies post-W3.

## Discriminated union (Python)

```python
AgentEvent = Annotated[
 Union[
 ContentDelta,
 ReasoningDelta,
 ToolCallStart,
 ToolCallResult,
 SkillLoaded,
 Citation,
 MonitorSignal,
 ],
 Field(discriminator="type"),
]
```

## Discriminated union (TS / Zod)

```typescript
export const AgentEvent = z.discriminatedUnion("type", [
 ContentDelta,
 ReasoningDelta,
 ToolCallStart,
 ToolCallResult,
 SkillLoaded,
 Citation,
 MonitorSignal,
]);

export type AgentEvent = z.infer<typeof AgentEvent>;

export const isToolCallResult = (e: AgentEvent): e is ToolCallResult =>
 e.type === "tool_call_result";
// (similar predicates per variant)
```

## Examples

### Single tool-call lifecycle

```jsonc
[
 {
 "type": "tool_call_start",
 "event_id": "f1...",
 "trace_id": "a3...",
 "iteration_id": "b2...",
 "timestamp": "2026-05-03T14:32:01.000Z",
 "parent_span_id": null,
 "call_id": "toolu_abc",
 "name": "lookup_supplier",
 "args": { "supplier_id": "S-7821" }
 },
 {
 "type": "tool_call_result",
 "event_id": "f2...",
 "trace_id": "a3...",
 "iteration_id": "b2...",
 "timestamp": "2026-05-03T14:32:01.420Z",
 "parent_span_id": "f1...",
 "call_id": "toolu_abc",
 "name": "lookup_supplier",
 "status": "ok",
 "output": { "lead_time_days": 14, "current_capacity": 0.82 },
 "error": null,
 "error_class": null,
 "duration_ms": 420
 }
]
```

### Sandbox-killed tool call (D3)

```jsonc
{
 "type": "tool_call_result",
 "event_id": "f3...",
 "trace_id": "a3...",
 "iteration_id": "b2...",
 "timestamp": "2026-05-03T14:33:11.000Z",
 "parent_span_id": "f0...",
 "call_id": "toolu_xyz",
 "name": "run_pipeline",
 "status": "error",
 "output": null,
 "error": "Sandbox timeout exceeded 600s",
 "error_class": "Timeout",
 "duration_ms": 600042
}
```

The gate runner sees `error_class="Timeout"` and does NOT advance `best_ever_score`.

### Retention-acknowledging skill load

```jsonc
{
 "type": "skill_loaded",
 "event_id": "f4...",
 "trace_id": "a3...",
 "iteration_id": null,
 "timestamp": "2026-05-03T14:30:00.000Z",
 "parent_span_id": null,
 "skill_id": "supplier-negotiation",
 "version_seq": 7,
 "retention_acknowledged": true
}
```

## Workflow render views (, NL-gen output schema)

The NL-gen pipeline emits a workflow spec containing a `ui:` block declaring
which views to render. Same discriminated-union shape as AgentEvent. Lands
in `src/ownevo_format/ui_views.py` at end of alongside the NL-gen
schema freeze.

The 9 views from the two-layer view architecture:

```python
class MetricCards(BaseModel):
 type: Literal["MetricCards"]
 fields: list[str] # e.g., ["forecast_accuracy", "markdown_risk_count"]

class TimeSeriesChart(BaseModel):
 type: Literal["TimeSeriesChart"]
 x: str # x-axis source field (e.g., "week")
 y: list[str] # y-series source fields
 group_by: str | None # optional grouping (e.g., "region")

class TableView(BaseModel):
 type: Literal["TableView"]
 source: str # data source ID (e.g., "skus")
 columns: list[str] # column source fields

class AlertList(BaseModel):
 type: Literal["AlertList"]
 source: str # alerts source ID
 severity_field: str = "severity"
 title_field: str = "title"

class KanbanBoard(BaseModel):
 type: Literal["KanbanBoard"]
 source: str
 column_field: str # field that determines kanban column (e.g., "status")
 card_title_field: str

class ScheduleGrid(BaseModel):
 type: Literal["ScheduleGrid"]
 rows_source: str # e.g., "staff" — one row per resource
 cols_source: str # e.g., "days" — one column per time bucket
 cells_source: str # resource x time cells, each with a status badge

class ConversationView(BaseModel):
 type: Literal["ConversationView"]
 trace_source: str # field referencing trace_id

class SideBySideView(BaseModel):
 type: Literal["SideBySideView"]
 left_source: str # e.g., "contract_clause"
 right_source: str # e.g., "redline"
 diff_mode: Literal["text", "json"] = "text"

class DocumentReader(BaseModel):
 type: Literal["DocumentReader"]
 source: str # document source ID
 annotations_source: str | None # optional margin annotations

UIView = Annotated[
 Union[MetricCards, TimeSeriesChart, TableView, AlertList,
 KanbanBoard, ScheduleGrid, ConversationView, SideBySideView,
 DocumentReader],
 Field(discriminator="type"),
]
```

Each view's `source` / `fields` reference data structures defined elsewhere in the workflow spec (sim-emitted data + agent-emitted outputs). The web app's render layer reads the workflow spec, picks the right component per view, and feeds it the resolved data.

Long-tail escape hatch (per MVP doc): when no view fits, the coding agent emits a custom React component that lives in the workflow spec's `ui.custom_components` map. Same approval/diff/gate flow as a skill change. Custom components are NOT in `ui_views.py` — they're per-workflow JSX.

## Field truncation rules

`ContentDelta.text`, `ReasoningDelta.text`, and `ToolCallResult.output` can be
arbitrarily large (LLM outputs, big tool results). The schema does not impose a
size limit; the storage layer truncates at the `traces.events` JSONB write
boundary:

- Single field > 64KB: truncate to 64KB and append `__truncated_size_bytes` field
- Whole event > 256KB: keep `event_id`, `type`, `timestamp`, drop content fields, set `__truncated: true`

Truncation is recorded so the UI can show "(truncated)" badges. Implementations
add a `__meta` discriminator field at write time; readers must tolerate it.

## Versioning policy

- Adding a new event variant: minor bump (0.1 → 0.2).
- Adding an optional field to an existing variant: minor bump.
- Removing a field, renaming a field, or changing a field's type: major bump (1.0 → 2.0). Triggers -day-5 review.
- Changing the discriminator value of a variant: never. Add a new variant; deprecate the old.

The schema-freeze ritual at end of stamps `1.0` and adds a CI check that
fails any subsequent structural change without an explicit version bump.

## OTel Gen AI awareness (no formal alignment)

The team designed AgentEvent with OTel Gen AI semantic conventions in mind so
that a future cross-walk (Phase 2 TODO) is bounded:

| AgentEvent variant | Closest OTel Gen AI concept |
|---|---|
| `content_delta` | `gen_ai.completion` span event with streamed `gen_ai.choice` |
| `reasoning_delta` | (no OTel standard yet — custom attribute) |
| `tool_call_start` | tool-call span start with `gen_ai.tool.name` |
| `tool_call_result` | tool-call span end with `gen_ai.tool.status` |
| `skill_loaded` | (ownEvo-specific) |
| `citation` | (ownEvo-specific; OTel is exploring source attribution) |
| `monitor_signal` | (ownEvo-specific) |

The cross-walk doc is **not** in MVP scope. When OTel Gen AI conventions stabilize and there's a customer with OTel-native traces who needs to ingest into ownEvo (Entry Point B per MVP doc), the cross-walk gets written. Schema is designed to make that mapping additive, not breaking.

## Python ↔ TypeScript cross-walk

`AgentEvent` lives in two languages. The contract is the JSON schema at
`schemas/agent_event.v1.0.json` (Pydantic-derived); both implementations
must round-trip against it.

| Concern | Python | TypeScript |
|---|---|---|
| Source of truth | `packages/trace-format/src/ownevo_format/agent_event.py` (Pydantic v2, `frozen=True`, `extra="forbid"`) | `apps/web/lib/api.ts` lines 684-746 (hand-written `interface` types, no Zod yet) |
| Schema snapshot | `packages/trace-format/schemas/agent_event.v1.0.json` | (same — TS reads from the JSON snapshot for type-narrowing helpers) |
| Conformance test | `packages/trace-format/tests/test_schema_freeze.py` — re-derives the schema from Pydantic and diff-asserts against the snapshot | (none today — TS types are reviewed by hand against the snapshot at PR time; **gap**) |
| UI views counterpart | `packages/trace-format/src/ownevo_format/ui_views.py` | (interface defs scattered in `apps/web/app/components/views/*.tsx`; **also a gap** — not consolidated to one types file) |

### How the TS side stays in sync today

Manually. The interface definitions in `api.ts` carry a comment
`// Matches packages/trace-format/SPEC.md v1.0`. The schema-freeze
test catches Python drift; **TS drift is on the reviewer**. If you add
a variant or field on the Python side:

1. Regenerate the snapshot: `python scripts/regen_schemas.py`.
2. Mirror the change in `apps/web/lib/api.ts` in the same PR.
3. Add the type to the `AgentEvent` discriminated union (line 746).
4. Update this cross-walk table if the locations changed.

### Adding a Zod conformance test (planned)

A TS-side conformance test is a known gap. Approach: ship a Zod schema
generated from the JSON snapshot and run `parseAsync` against the same
test fixtures the Python suite uses. That would let CI catch TS/Python
drift the way `test_schema_freeze.py` catches Python/snapshot drift.

Tracked as a doc-only TODO until a reviewer hits the gap in earnest.

## References

- [`README.md`](./README.md) — orientation + status
- [`../../docs/SCHEMA.md`](../../docs/SCHEMA.md) — DB schema; `traces.events` JSONB array contains AgentEvents
- [`../../docs/SKILL_FORMAT.md`](../../docs/SKILL_FORMAT.md) — skill format; `skill_loaded.skill_id` references `skills.id`
- [`../../docs/api/openapi.yaml`](../../docs/api/openapi.yaml) — REST + SSE; the SSE event types are a separate concern from AgentEvent (different schema)
- The typed AgentEvent schema is the contract between any customer agent and the improvement loop. Schema-freeze rituals bump this spec to a new version.
