# OTel GenAI Semantic Conventions → AgentEvent mapping

**Status:** draft for the first OTel ingest slice. Pinned to OpenTelemetry GenAI
Semantic Conventions **v1.27+** (the current published surface as of
2026-05; the spec is still labelled `Development` upstream so this
mapping has to evolve as the spec stabilises).

**Authoritative source for AgentEvent:** `packages/trace-format/SPEC.md`
(v1.0, frozen). All shape decisions below defer to that spec.

**Authoritative source for OTel field names:**
<https://opentelemetry.io/docs/specs/semconv/gen-ai/> (the `gen_ai.*`
attribute and event namespace).

## Scope of the receiver

This receiver accepts OTLP-JSON payloads over HTTP (one OTel `ResourceSpans`
batch per request) and decodes them into a stream of typed `AgentEvent`
objects. gRPC + protobuf-OTLP are not implemented in this slice — the
JSON-over-HTTP path is sufficient for the LangSmith / langsmith-collector-proxy
dry-run loop and for the round-trip replay test against existing M5 traces.

The mapping is intentionally lossy: OTel carries operational metadata
(durations, timestamps, model usage) that AgentEvent compresses or
re-encodes. The round-trip replay test
(`tests/middleware/otel_receiver/test_round_trip.py`) pins the
load-bearing fields — anything not pinned there is best-effort.

## Trace / span IDs

| OTel field              | AgentEvent field         | Notes |
|-------------------------|--------------------------|-------|
| `trace_id` (16-byte hex)| `trace_id` (UUIDv4)      | Re-encoded as UUID; OTel uses 128-bit ids, same width as UUID. |
| `span_id` (8-byte hex)  | `event_id` (UUIDv4)      | Padded to 16 bytes (UUID width) before re-encoding. |
| `parent_span_id`        | `parent_span_id`         | Same padding rule. `None` if root span. |
| `start_time_unix_nano`  | `timestamp` (ISO 8601)   | Nanoseconds → datetime with timezone (UTC). |

`iteration_id` is always `None` for OTel-ingested traces — it is reserved
for benchmark-replay traces emitted by ownEvo's own loop runner, not for
customer agents arriving through the ingest path.

## Span-kind → AgentEvent variant

The receiver inspects `gen_ai.operation.name` (with a fallback to span
name) to pick the variant.

| `gen_ai.operation.name`                | AgentEvent variant      | Notes |
|----------------------------------------|-------------------------|-------|
| `chat` / `text_completion` / `generate_content` | `ContentDelta` (+ `ReasoningDelta` when reasoning tokens are present) | One span → one ContentDelta with `cumulative_text` populated. Streaming chunks are collapsed because OTel completes one span per request, not per delta. |
| `execute_tool`                          | `ToolCallStart` + `ToolCallResult` pair | Single OTel tool span maps to a start/result pair; the start carries `args`, the result carries `output` + `duration_ms`. |
| `invoke_agent` / `create_agent`         | — (root span carries `trace_id` for nested children; no AgentEvent emitted) | The root agent span is consumed to anchor the trace; no AgentEvent variant exists for an "agent run started". |
| `invoke_workflow`                       | — (same as `invoke_agent`) | |
| `embeddings` / `retrieval`              | `Citation` (when retrieval returns referenceable docs) | Best-effort; many emitters omit the doc list. Skipped when `gen_ai.retrieval.documents` is missing. |

Spans with no recognised `gen_ai.operation.name` are skipped with a
debug-level log entry (they may carry vendor-internal telemetry the
loop does not need). The receiver does NOT raise on unknown operations
— silently dropping non-GenAI spans is required for langsmith-collector-proxy
output, which interleaves application spans with GenAI spans.

## Attribute mapping (per variant)

### `ContentDelta`

| OTel attribute / event              | AgentEvent field        |
|-------------------------------------|-------------------------|
| `gen_ai.response.model` (fallback `gen_ai.request.model`) | `model` |
| `gen_ai.output.messages[*].parts[*].content` for `role=assistant`, `type=text` | concatenated into `text` and `cumulative_text` |
| span `start_time_unix_nano`         | `timestamp`             |

### `ReasoningDelta`

| OTel attribute / event              | AgentEvent field        |
|-------------------------------------|-------------------------|
| `gen_ai.response.model`             | `model`                 |
| `gen_ai.output.messages[*].parts[*].content` for `type=thinking` (or `gen_ai.usage.reasoning.output_tokens` > 0 with raw text in extension attribute) | `text` |

Emitted only when reasoning content is present on the span. The spec
does not yet pin reasoning-content as a first-class field; ownEvo
accepts the `parts[*].type=thinking` shape used by Anthropic-emitting
collectors and an `ownevo.reasoning_text` vendor extension as fallback.

### `ToolCallStart`

| OTel attribute                      | AgentEvent field        |
|-------------------------------------|-------------------------|
| `gen_ai.tool.call.id`               | `call_id`               |
| `gen_ai.tool.name`                  | `name`                  |
| `gen_ai.tool.call.arguments` (JSON) | `args`                  |

### `ToolCallResult`

| OTel attribute                      | AgentEvent field         |
|-------------------------------------|--------------------------|
| `gen_ai.tool.call.id`               | `call_id`                |
| `gen_ai.tool.name`                  | `name`                   |
| span `status.code`                  | `status` (`STATUS_CODE_OK` → `"ok"`, `STATUS_CODE_ERROR` → `"error"`) |
| `gen_ai.tool.call.result`           | `output`                 |
| `end_time - start_time` (ms)        | `duration_ms`            |
| span `status.message`               | `error` (when `status=error`) |
| `ownevo.error_class` (vendor ext.)  | `error_class`            |

`error_class` (sandbox-runtime failure) is ownEvo-specific and has no
OTel-standard analog. When the source platform is not the ownEvo
sandbox, `error_class` is always `None`; a logical tool error from a
customer agent maps to `status="error"` + `error_class=None` (the
"logical error inside the tool" row in `SPEC.md`).

### `Citation`

| OTel attribute                      | AgentEvent field        |
|-------------------------------------|-------------------------|
| `gen_ai.retrieval.documents[*].id`  | `source`                |
| `gen_ai.retrieval.documents[*].content` (truncated) | `quote` |
| index in retrieved-docs array (1-based) | `ref`               |

### `SkillLoaded`

No OTel-standard equivalent. Skipped from OTel ingest; AgentEvents of
this kind only originate from ownEvo's own loop runner.

### `MonitorSignal`

No OTel-standard equivalent. Skipped from OTel ingest; produced by
ownEvo's own monitors.

## Error / validation envelope

The receiver returns HTTP responses in line with OTLP/HTTP conventions:

- `200 OK` with an empty JSON body — payload accepted (possibly with
  some spans skipped due to unrecognised operations; skips are logged
  but do not affect the response).
- `400 Bad Request` with `{ "error": "..." }` — JSON malformed,
  `ResourceSpans` field missing, or required ID fields absent.
- `413 Payload Too Large` — payload exceeds the 8 MiB size cap
  (hardcoded via `DEFAULT_MAX_BODY_BYTES` in `mapper.py`; per-instance
  configuration via env var is deferred to a later slice).
- `200 OK` with non-empty `warnings[]` — payload accepted but one or
  more spans were skipped (unknown operation name, missing required
  fields, schema validation failure). Callers must inspect `warnings[]`
  to detect partial acceptance; there is no 422 or 207 variant.
- `500 Internal Server Error` — unexpected bug in the receiver or
  mapper. Caller-driven bad data always produces 400 or a warning, not
  a 500.

## Tenant isolation (sketch — full impl in a later slice)

Track 13 ships single-tenant. OTel does not pin a tenant identifier;
when multi-tenant lands, the receiver will require either a
`Authorization: Bearer <workspace-token>` header or an
`ownevo.workspace_id` resource attribute and reject payloads that
carry neither. For now the receiver tags every decoded AgentEvent with
the workspace currently configured on the kernel.

## What this mapping deliberately does not do

- No protobuf / gRPC OTLP. JSON-over-HTTP only.
- No span-link traversal. OTel `links` are dropped.
- No span-event ingestion outside the `gen_ai.*` event namespace
  (e.g. the deprecated `gen_ai.user.message` event-shape is not
  consumed; the receiver targets the newer attribute-based shape).
- No streaming-aware chunk reconstruction. One span → one
  ContentDelta. The collector-side aggregation (LangSmith /
  OpenLLMetry) is expected to have already collapsed streamed chunks
  into a finalised span.
- No automatic upgrade across spec versions. When the upstream spec
  bumps a breaking field, this file gets updated in lockstep and the
  fixture set under `tests/middleware/otel_receiver/fixtures/` is
  re-recorded.
