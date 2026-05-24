"""Google Agent Development Kit (ADK) ingest adapter.

ADK is OpenTelemetry-native and emits GenAI Semantic Conventions
(`gen_ai.*`) spans the existing OTel receiver in
`middleware/otel_receiver/` already understands — with two
exceptions called out in the GenAI semconv mapping that this module
bridges:

  1. ADK puts tool-call argument and result payloads under the
     vendor-prefixed keys `gcp.vertex.agent.tool_call_args` and
     `gcp.vertex.agent.tool_response`, not under the standard
     `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result`
     attribute names. The receiver mapper reads the standard names;
     this translator rewrites the vendor names onto the standard ones
     when the standard ones are absent.

  2. ADK orchestration spans use `gen_ai.operation.name = "invoke_agent"`
     (sometimes `"create_agent"` for setup). Those are already silently
     dropped by the receiver mapper as agent-root spans — no
     translation needed.

LLM-call spans inside an ADK trace are emitted by the underlying
`google-genai` SDK, not by ADK itself, and those follow the GenAI
semantic conventions natively (`gen_ai.operation.name = "chat"` or
`generate_content`, `gen_ai.response.model`, etc.). The translator
leaves them untouched.

Public surface:

  translate_otlp_json_for_adk(payload) -> dict
      Pure function over a parsed OTLP-JSON dict. Returns a new dict
      with the ADK vendor keys rewritten onto the standard semconv
      keys. Does NOT mutate the input. Safe to pass into the existing
      `otel_receiver.decode_otlp_payload` afterwards.

  readable_spans_to_otlp_json(spans) -> dict
      Encode a list of `opentelemetry.sdk.trace.ReadableSpan` objects
      (as captured by `InMemorySpanExporter` in tests) into an
      OTLP-JSON `resourceSpans` envelope. Mirror of what an OTel
      OTLP-HTTP/JSON exporter would put on the wire. Lives here so
      the integration test can stitch ADK → translator → receiver
      mapper without spinning up an HTTP transport.

What this module deliberately does not do:

  * No subprocess fork to run the ADK SDK — the integration test
    drives ADK directly when `google-adk` is installed and a
    `GOOGLE_API_KEY` is present.
  * No protocol bridge to OTLP-protobuf-over-HTTP. The receiver only
    accepts OTLP-JSON; the test path is in-process via the helpers
    above; production ADK customers wire their OTel exporter to ownEvo
    via the standard `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/json`
    setting on the customer side.
  * No multi-tenant attestation. The single-tenant deferral that
    applies to the OTel receiver applies here too.
"""

from .translator import (
    readable_spans_to_otlp_json,
    translate_otlp_json_for_adk,
)

__all__ = [
    "readable_spans_to_otlp_json",
    "translate_otlp_json_for_adk",
]
