"""Tests for `readable_spans_to_otlp_json` — the in-process encoder.

The encoder turns OpenTelemetry `ReadableSpan` objects (as captured
by `InMemorySpanExporter`) into the OTLP-JSON `resourceSpans`
envelope the receiver mapper consumes. This is what lets the
integration test thread `ADK SDK → translator → receiver` without
an HTTP transport.

These tests build `ReadableSpan` objects directly through the OTel
SDK without involving ADK at all. They run when the OpenTelemetry
SDK is installed (which happens transitively via the `[adk]` extra)
and skip otherwise.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry")

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from ownevo_kernel.middleware.google_adk import (
    readable_spans_to_otlp_json,
    translate_otlp_json_for_adk,
)
from ownevo_kernel.middleware.otel_receiver import decode_otlp_payload


@pytest.fixture
def in_memory_tracer() -> tuple[trace.Tracer, InMemorySpanExporter]:
    """A throwaway TracerProvider feeding an InMemorySpanExporter.

    Returns the tracer and the exporter so the test can read finished
    spans without going through the global provider (the global one
    leaks across tests).
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.tracer")
    return tracer, exporter


def test_encodes_minimal_tool_span_into_decodable_envelope(
    in_memory_tracer: tuple[trace.Tracer, InMemorySpanExporter],
) -> None:
    """End-to-end: emit a synthetic ADK-shaped tool span via the OTel
    SDK, encode it, translate it, decode it. The receiver should land
    a ToolCallStart + ToolCallResult pair just like the unit-tested
    payloads do."""
    tracer, exporter = in_memory_tracer

    with tracer.start_as_current_span("execute_tool add") as span:
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.call.id", "call_xyz")
        span.set_attribute("gen_ai.tool.name", "add")
        span.set_attribute("gcp.vertex.agent.tool_call_args", '{"a":3,"b":4}')
        span.set_attribute("gcp.vertex.agent.tool_response", "7")
        span.set_status(StatusCode.OK)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1

    payload = readable_spans_to_otlp_json(spans)
    translated = translate_otlp_json_for_adk(payload)
    decoded = decode_otlp_payload(translated)

    starts = [e for e in decoded.events if e.type == "tool_call_start"]
    results = [e for e in decoded.events if e.type == "tool_call_result"]
    assert len(starts) == 1
    assert len(results) == 1
    assert starts[0].name == "add"
    assert starts[0].args == {"a": 3, "b": 4}
    assert results[0].output == 7
    assert results[0].status == "ok"


def test_encodes_error_status_as_error_event(
    in_memory_tracer: tuple[trace.Tracer, InMemorySpanExporter],
) -> None:
    """A tool span that the agent reported as failed should round-trip
    through the encoder + translator + decoder with status='error'."""
    tracer, exporter = in_memory_tracer

    with tracer.start_as_current_span("execute_tool broken") as span:
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.call.id", "call_err")
        span.set_attribute("gen_ai.tool.name", "broken")
        span.set_attribute("gcp.vertex.agent.tool_call_args", "{}")
        span.set_attribute("gcp.vertex.agent.tool_response", '"divide by zero"')
        span.set_status(StatusCode.ERROR, "tool raised")

    spans = exporter.get_finished_spans()
    payload = readable_spans_to_otlp_json(spans)
    translated = translate_otlp_json_for_adk(payload)
    decoded = decode_otlp_payload(translated)

    results = [e for e in decoded.events if e.type == "tool_call_result"]
    assert len(results) == 1
    assert results[0].status == "error"
    assert results[0].error == "tool raised"


def test_invoke_agent_root_span_emits_no_event(
    in_memory_tracer: tuple[trace.Tracer, InMemorySpanExporter],
) -> None:
    """ADK's `invoke_agent` orchestration span is consumed by the
    receiver mapper as a trace-anchor and emits no AgentEvent. Pin
    that the encoder doesn't accidentally turn it into a stray event,
    and that the standard-key invoke_agent op-name flows through
    cleanly without any translator rewrites needed."""
    tracer, exporter = in_memory_tracer

    with tracer.start_as_current_span("invoke_agent add_agent") as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.agent.name", "add_agent")
        span.set_status(StatusCode.OK)

    spans = exporter.get_finished_spans()
    payload = readable_spans_to_otlp_json(spans)
    translated = translate_otlp_json_for_adk(payload)
    decoded = decode_otlp_payload(translated)

    assert decoded.events == []
    # invoke_agent is silently consumed; no warning recorded either.
    assert decoded.warnings == []


def test_encodes_kind_correctly(
    in_memory_tracer: tuple[trace.Tracer, InMemorySpanExporter],
) -> None:
    """Span kind must round-trip — the receiver inspects status code
    but the kind is recorded for downstream observability. Test the
    INTERNAL default (kind=1)."""
    tracer, exporter = in_memory_tracer

    with tracer.start_as_current_span("internal_span") as span:
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.call.id", "k1")
        span.set_attribute("gen_ai.tool.name", "noop")
        span.set_attribute("gcp.vertex.agent.tool_call_args", "{}")
        span.set_attribute("gcp.vertex.agent.tool_response", "null")

    spans = exporter.get_finished_spans()
    payload = readable_spans_to_otlp_json(spans)
    encoded_span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert encoded_span["kind"] == 1  # SpanKind.INTERNAL → 1
