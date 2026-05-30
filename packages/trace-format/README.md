# `trace-format` — AgentEvent typed event schema

The seam between any agent runtime and ownEvo's improvement loop. Every agent
event (LLM tokens, tool calls, skill loads, citations, monitor signals) is
captured as a typed event in this schema. Trajectory replay, failure clustering,
regression gating, and the audit log all read from this format.

This package is the contract. Keep it small, dependency-free (stdlib + Pydantic
on Python, Zod on TS), and stable across the loop's downstream consumers.

**Source of truth:** [`SPEC.md`](./SPEC.md). This README orients; SPEC is canonical.

## Status (2026-05-03)

- **Spec written** at [`SPEC.md`](./SPEC.md). Pydantic + Zod implementations land in against this spec.
- **License:** not selected for MVP. Internal use within `ownevo_app/` only.
- **Public release:** no plan yet. Stays in this monorepo.
- **Package naming:** deferred. Current Python import path is `ownevo_format` (workspace-internal); npm/PyPI naming is decided when public release becomes relevant.
- **OTel Gen AI semantic conventions:** designed with awareness (event-type names roughly map to OTel concepts), no formal cross-walk, no compatibility commitment.

The decisions above are deliberately deferred. The MVP doc § Open-Core Line names
Apache 2 as the working assumption for if/when public release happens, but the
formal commitment + LICENSE + publication metadata are not blocking work.

Revisit when any of these trigger:
- A customer asks "what license is this under?"
- A second team or repo needs to depend on this package
- An OTel Gen AI working group asks to align (or vice versa)
- Strategic decision to publish (post-MVP, with first design partners)

The strategic surface (license, public-release timing, package naming) is tracked outside this repo.

## What's in this package

| Module | Purpose | Lands |
|---|---|---|
| `src/ownevo_format/agent_event.py` | Typed `AgentEvent` discriminated union (Pydantic) | |
| `src/ownevo_format/ui_views.py` | 9 workflow render views (Pydantic) | alongside NL-gen schema freeze |
| `src/ownevo_format/schemas/` | JSON Schema generated from Pydantic via `model_json_schema()` | + |
| (TS bindings) | Zod schemas + types — co-located with `apps/web/lib/api/` for now; can extract later | alongside web scaffold |
| [`SPEC.md`](./SPEC.md) | Canonical spec — what the implementations conform to | Locked 2026-05-03 |

## How implementations use it

Python (kernel):

```python
from ownevo_format import AgentEvent, ToolCallResult

event = ToolCallResult(
 type="tool_call_result",
 call_id="call_abc",
 name="lookup_supplier",
 status="error",
 error="Sandbox timeout exceeded 10s",
 error_class="Timeout",
 duration_ms=10042,
)
```

TypeScript (web):

```typescript
import { AgentEvent, isToolCallResult } from "../lib/api/agent-event";

function renderEvent(event: AgentEvent) {
 if (isToolCallResult(event) && event.status === "error") {
 return <ToolErrorCard event={event} />;
 }
}
```

## Versioning during MVP

`SPEC.md` carries a version line. While public release is deferred, the version
is internal — used to gate the NL-gen schema freeze and to detect drift in CI.

- `0.x` — pre- freeze. Schema can change freely.
- `1.0` — locked at end of per the schema-freeze deliverable . After this, any structural change is a major bump and requires the team's -day-5 schema review.

## Cross-references

- [`SPEC.md`](./SPEC.md) — canonical spec
- [`../../docs/SCHEMA.md`](../../docs/SCHEMA.md) — DB schema (where `events` JSONB lives in the `traces` table)
- [`../../docs/SKILL_FORMAT.md`](../../docs/SKILL_FORMAT.md) — skill file format with retention contracts
- [`../../docs/api/openapi.yaml`](../../docs/api/openapi.yaml) — REST + SSE API contract
