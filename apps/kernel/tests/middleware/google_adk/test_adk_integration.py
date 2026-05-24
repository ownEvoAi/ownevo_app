"""Live integration test — Google ADK agent → OTel → receiver → AgentEvent.

What this test verifies that the unit-level translator/encoder tests
cannot:

  * That `google-adk` and the OpenTelemetry SDK, configured against a
    real Gemini call, actually emit the spans this module's translator
    is built to bridge.
  * That an ADK `execute_tool` span carries the ADK vendor keys the
    translator rewrites (i.e. that the translator's premise hasn't
    been invalidated by an ADK release that ships standard semconv
    names natively — in which case the translator becomes a no-op,
    which is fine).
  * That the resulting AgentEvent stream has the shape the
    downstream clustering pipeline consumes — at minimum the tool-call
    pair (`ToolCallStart` + `ToolCallResult`) tied to the assistant's
    decision to invoke the tool.

Skip semantics (in order — first matching condition wins):

  * `google-adk` not importable → skip (the `[adk]` extra is not
    installed; the unit tests cover the translator without ADK).
  * `GOOGLE_API_KEY` not set → skip (the test makes a real billable
    Gemini call and refuses to silently no-op).
  * `GOOGLE_GENAI_USE_VERTEXAI` truthy → skip (the test path is the
    AI Studio key flow, not Vertex; Vertex needs ADC + project +
    location and is out of scope here).

The test is intentionally narrow: one tool, one prompt, one turn.
Once a customer's production agent is wired through `[adk]`-extra
plumbing, the broader assertions (cluster shapes, regression-gate
behaviour) belong in the surrounding harness, not here.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("GOOGLE_API_KEY") is None,
        reason="GOOGLE_API_KEY not set; live ADK integration test skipped",
    ),
    pytest.mark.skipif(
        os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1", "yes"),
        reason="GOOGLE_GENAI_USE_VERTEXAI is truthy; this test targets the AI Studio key flow",
    ),
]

# Skip the whole module if ADK or the OTel SDK aren't installed —
# importorskip emits a clean pytest skip message and avoids a bare
# ImportError at collection time.
pytest.importorskip("google.adk", reason="google-adk not installed; pass --extra adk to uv sync")
pytest.importorskip("opentelemetry", reason="opentelemetry-sdk not installed")

# Imports must come after the importorskip calls so collection survives
# when the deps are missing.
from google.adk.agents.llm_agent import Agent  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from ownevo_kernel.middleware.google_adk import (  # noqa: E402
    readable_spans_to_otlp_json,
    translate_otlp_json_for_adk,
)
from ownevo_kernel.middleware.otel_receiver import decode_otlp_payload  # noqa: E402

_MODEL_ENV = "OWNEVO_ADK_TEST_MODEL"
_DEFAULT_MODEL = "gemini-2.5-flash"


def _add(a: int, b: int) -> int:
    """Add two integers. Deterministic. ADK exposes this as a tool the
    agent can call."""
    return a + b


@pytest.fixture
def in_memory_tracer() -> InMemorySpanExporter:
    """Replace the global TracerProvider so ADK's auto-instrumentation
    emits into an in-memory exporter we can drain after the run.

    Uses `SimpleSpanProcessor` rather than `BatchSpanProcessor` so the
    test doesn't have to coordinate a flush window — every span is
    handed to the exporter the instant the span ends.

    The original provider is restored in teardown so the replacement
    does not leak into subsequent tests in the same process.
    """
    original = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    try:
        yield exporter
    finally:
        trace.set_tracer_provider(original)


async def test_minimal_adk_agent_emits_decodable_tool_call(
    in_memory_tracer: InMemorySpanExporter,
) -> None:
    """End-to-end: build an ADK Agent with one tool, run one turn,
    capture the OTel spans, route them through the translator, decode
    them via the OTel receiver, assert the AgentEvent stream contains
    the tool call the agent made."""
    exporter = in_memory_tracer

    model = os.environ.get(_MODEL_ENV, _DEFAULT_MODEL)
    agent = Agent(
        name="add_agent",
        model=model,
        description="Adds two integers using the add tool.",
        instruction=(
            "When the user asks for arithmetic, call the `add` tool. "
            "Do not compute the answer yourself."
        ),
        tools=[_add],
    )

    runner = InMemoryRunner(agent=agent)
    try:
        await runner.run_debug("What is 17 + 25? Use the tool.")
    finally:
        await runner.close()

    # Drain spans synchronously (SimpleSpanProcessor flushes on
    # span-end, but force_flush is idempotent and cheap insurance).
    active_provider = trace.get_tracer_provider()
    if hasattr(active_provider, "force_flush"):
        active_provider.force_flush(timeout_millis=5000)

    spans = exporter.get_finished_spans()
    assert spans, "ADK produced no OTel spans — instrumentation not wired"

    payload = readable_spans_to_otlp_json(spans)
    translated = translate_otlp_json_for_adk(payload)
    decoded = decode_otlp_payload(translated)

    # The agent might call the tool more than once depending on the
    # model's planning; assert at least one start + matching result.
    starts = [e for e in decoded.events if e.type == "tool_call_start"]
    results = [e for e in decoded.events if e.type == "tool_call_result"]
    assert starts, "no ToolCallStart events emitted — check ADK is exporting execute_tool spans"
    assert results, "no ToolCallResult events emitted — check ADK tool span status / response keys"

    add_starts = [s for s in starts if s.name == "add"]
    assert add_starts, f"no tool-call to `add` found (got: {[s.name for s in starts]})"

    # The arguments should round-trip as a dict matching the prompt.
    args: dict[str, Any] = add_starts[0].args or {}
    assert {"a", "b"} <= set(args.keys()), f"add args missing a/b: {args!r}"
    assert int(args["a"]) + int(args["b"]) == 42, (
        f"agent invoked add with unexpected args: {args!r}"
    )

    # The matching result should report status ok with output 42.
    add_results = [r for r in results if r.name == "add"]
    assert add_results, "ToolCallResult for `add` missing"
    assert add_results[0].status == "ok"
    # output might be wrapped in a dict by ADK (e.g. `{"result": 42}`) —
    # accept either the bare value or any dict containing 42 in values.
    output = add_results[0].output
    if isinstance(output, dict):
        assert 42 in output.values(), f"add result dict missing 42: {output!r}"
    else:
        assert output == 42, f"add result not 42: {output!r}"
