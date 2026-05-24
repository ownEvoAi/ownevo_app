"""A minimal OpenLLMetry-instrumented agent for the integration test.

This is the "sample app" the 13.0.1 acceptance calls for: a real agent
instrumented with `traceloop-sdk` (OpenLLMetry) whose telemetry flows
through the genuine OpenTelemetry export pipeline. We deliberately keep
it tiny and offline:

  * The LLM call goes through the real `openai` client, which traceloop
    auto-instruments — so the emitted `gen_ai.*` chat span is exactly
    what a production OpenLLMetry deployment would send. The network is
    mocked at the httpx transport, so no API key or connectivity is
    needed and the run is deterministic.

  * The tool step is emitted as a `gen_ai.operation.name=execute_tool`
    span with an ERROR status, through the same tracer traceloop
    configured. (A fully auto-instrumented tool span would require a
    framework like LangChain; the execute_tool convention is emitted
    directly to keep the dependency surface to just openai + traceloop.)

Both spans share one trace, so the decoded AgentEvent stream is a
single trace carrying a chat ContentDelta plus a failed tool call —
the broken-tool signal the failure-clustering extractor consumes.
"""

from __future__ import annotations

import httpx

# These imports require the `otel-integration` + `agent` extras; the
# test module guards on importability before using this helper.
import openai
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.common._internal.trace_encoder import (
    encode_spans,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import Status, StatusCode
from traceloop.sdk import Traceloop

_TRACELOOP_READY = False

# The canned chat completion the mocked transport returns. Shaped like a
# real OpenAI chat response so traceloop's instrumentor populates the
# gen_ai.* attributes from it.
_CANNED_COMPLETION = {
    "id": "chatcmpl-sample",
    "object": "chat.completion",
    "created": 1_700_000_000,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Let me run the forecast tool."},
            "finish_reason": "tool_calls",
        },
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
}


def init_traceloop(exporter: InMemorySpanExporter) -> None:
    """Initialise traceloop once, exporting to the given in-memory sink.

    `Traceloop.init` is process-global; calling it twice is wasteful and
    can warn, so we guard with a module flag. Tests share one exporter
    and clear it between runs.
    """
    global _TRACELOOP_READY
    if _TRACELOOP_READY:
        return
    Traceloop.init(
        exporter=exporter,
        disable_batch=True,
        telemetry_enabled=False,
    )
    _TRACELOOP_READY = True


def run_broken_agent(exporter: InMemorySpanExporter) -> bytes:
    """Run one agent turn and return the captured spans as OTLP protobuf.

    Clears the exporter first so the returned bytes contain only this
    run's spans (one trace: a chat ContentDelta + a failed tool call).
    """
    exporter.clear()
    tracer = trace.get_tracer("openllmetry-sample")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_CANNED_COMPLETION)

    client = openai.OpenAI(
        api_key="sk-test",
        http_client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )

    with tracer.start_as_current_span("agent.run"):
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "What's the demand forecast?"}],
        )
        with tracer.start_as_current_span("execute_tool") as tool_span:
            tool_span.set_attribute("gen_ai.operation.name", "execute_tool")
            tool_span.set_attribute("gen_ai.tool.name", "forecast")
            tool_span.set_attribute("gen_ai.tool.call.id", "call_forecast_1")
            tool_span.set_status(
                Status(StatusCode.ERROR, "NaN in input demand series"),
            )

    trace.get_tracer_provider().force_flush()
    return encode_spans(exporter.get_finished_spans()).SerializeToString()
