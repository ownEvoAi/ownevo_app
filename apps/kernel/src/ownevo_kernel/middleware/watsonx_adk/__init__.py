"""IBM watsonx Orchestrate ADK ingest adapter.

watsonx Orchestrate's Agent Development Kit emits OpenTelemetry traces
through the Traceloop / OpenLLMetry SDK. LLM-call spans inside a
watsonx trace follow the standard GenAI Semantic Conventions
(`gen_ai.operation.name = "chat"`, `gen_ai.response.model`, etc.) so
the existing receiver mapper in `middleware/otel_receiver/` consumes
them unchanged. Tool, workflow, agent, and task spans are emitted
under Traceloop's vendor-prefixed namespace and need to be bridged:

  1. Tool spans carry `traceloop.span.kind = "tool"` instead of
     `gen_ai.operation.name = "execute_tool"`. The translator
     synthesises the standard operation name when only the Traceloop
     kind is present.

  2. Tool name lives at `traceloop.entity.name`, not `gen_ai.tool.name`.

  3. Tool arguments are JSON-encoded under `traceloop.entity.input`,
     not `gen_ai.tool.call.arguments`. The receiver mapper reads the
     standard key as a JSON string and parses it via `json.loads`, so
     the value transfers verbatim.

  4. Tool results are JSON-encoded under `traceloop.entity.output`,
     not `gen_ai.tool.call.result`. Same verbatim transfer as args.

  5. Traceloop does not emit a `gen_ai.tool.call.id`. The translator
     synthesises one from the span's own `spanId` (16 hex chars,
     deterministic, stable across replays of the same trace) when the
     standard key is absent.

  6. Workflow / agent / task spans carry
     `traceloop.span.kind = "workflow" | "agent" | "task"` with no
     `gen_ai.operation.name`. The translator maps them onto
     `invoke_workflow` / `invoke_agent` / `invoke_workflow` so the
     receiver mapper's silent-drop branch handles them as orchestration
     anchors without producing AgentEvents.

The rewrites are conditional and additive — present standard keys win
over the Traceloop equivalents, so an OpenLLMetry version that ships
standard semconv natively in the future becomes a no-op rather than a
double-write.

Public surface mirrors the Google ADK adapter for symmetry:

  translate_otlp_json_for_watsonx(payload) -> dict
      Pure function over a parsed OTLP-JSON dict. Returns a new dict
      with the Traceloop vendor keys rewritten onto the standard
      semconv keys. Does NOT mutate the input.

What this module deliberately does not do:

  * No live integration test. watsonx Orchestrate ADK requires an IBM
    Cloud account + an Orchestrate environment that cannot be stood up
    inside CI in this slice. The translator is exercised against
    hand-crafted OTLP-JSON fixtures shaped to match what the Traceloop
    SDK emits per its semantic-conventions spec; a live test against a
    real watsonx environment lands when a design-partner deployment is
    available.
  * No subprocess fork to run watsonx Orchestrate. Production
    customers wire their OTel exporter to the kernel's
    `/api/otel/v1/traces` endpoint via standard
    `OTEL_EXPORTER_OTLP_TRACES_*` env vars on the watsonx side.
  * No multi-tenant attestation. Single-tenant deferral applies
    here too (see `middleware/otel_receiver/MAPPING.md`).

The translator is not watsonx-specific in implementation — it bridges
any OpenLLMetry-shaped trace and would work for raw Traceloop SDK
emissions too. It lives under `watsonx_adk` because watsonx is the
named delivery surface in the Phase 4 cross-stack foundations slice;
factoring it into a shared `openllmetry/` module is fine when a second
OpenLLMetry-emitting framework lands.
"""

from .translator import translate_otlp_json_for_watsonx

__all__ = [
    "translate_otlp_json_for_watsonx",
]
