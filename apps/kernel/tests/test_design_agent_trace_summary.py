"""Unit tests for the trace-import summary builder.

Pure function over decoded `AgentEvent` dicts — no DB, no LLM. Covers
the rollup logic (tool counts, error modes, sample outputs/text) and
the rendered prompt block both generators and the interviewer consume.
"""

from __future__ import annotations

from uuid import uuid4

from ownevo_kernel.design_agent.trace_summary import (
    TraceSummary,
    summarize_events,
)


def _trace(events: list[dict]) -> tuple:
    return (uuid4(), events)


def test_empty_traces_summary_is_empty():
    summary = summarize_events([])
    assert isinstance(summary, TraceSummary)
    assert summary.is_empty
    assert summary.trace_count == 0
    assert "purpose as unknown" in summary.as_prompt_text()


def test_tool_calls_rollup_with_counts_and_errors():
    events = [
        {"type": "tool_call_start", "name": "forecast_demand", "args": {"sku": "A1"}},
        {"type": "tool_call_result", "name": "forecast_demand", "status": "ok",
         "output": {"units": 120}},
        {"type": "tool_call_start", "name": "forecast_demand", "args": {"sku": "B2"}},
        {"type": "tool_call_result", "name": "forecast_demand", "status": "error",
         "error_class": "Timeout", "error": "model server timed out"},
        {"type": "tool_call_start", "name": "flag_stockout", "args": {"threshold": 0.2}},
        {"type": "tool_call_result", "name": "flag_stockout", "status": "ok",
         "output": "flagged 3 SKUs"},
    ]
    summary = summarize_events([_trace(events)])

    assert summary.trace_count == 1
    assert summary.event_count == 6
    by_name = {t.name: t for t in summary.tools}
    assert by_name["forecast_demand"].call_count == 2
    assert by_name["forecast_demand"].error_count == 1
    assert by_name["flag_stockout"].call_count == 1
    assert by_name["flag_stockout"].error_count == 0
    # First non-empty args captured as the sample.
    assert by_name["forecast_demand"].sample_args == {"sku": "A1"}

    # Error mode surfaces with class + tool + message.
    assert any("Timeout" in m and "forecast_demand" in m for m in summary.error_modes)
    # Sample outputs collected from ok results.
    assert any("flagged 3 SKUs" in s for s in summary.sample_outputs)


def test_prompt_text_renders_observational_sections():
    events = [
        {"type": "content_delta", "cumulative_text": "Analysing demand signals."},
        {"type": "tool_call_start", "name": "forecast_demand", "args": {"sku": "A1"}},
        {"type": "tool_call_result", "name": "forecast_demand", "status": "ok",
         "output": {"units": 120}},
    ]
    text = summarize_events([_trace(events)]).as_prompt_text()
    assert "Imported agent — observed behaviour" in text
    assert "Tools the agent calls" in text
    assert "forecast_demand" in text
    assert "Sample assistant text" in text
    assert "Analysing demand signals." in text


def test_result_without_matching_start_still_registers_tool():
    # OTel-ingested traces may carry a result without a paired start.
    events = [
        {"type": "tool_call_result", "name": "lookup_account", "status": "ok",
         "output": "ok"},
    ]
    summary = summarize_events([_trace(events)])
    by_name = {t.name: t for t in summary.tools}
    assert "lookup_account" in by_name
    assert by_name["lookup_account"].call_count == 0  # no start observed
    assert by_name["lookup_account"].error_count == 0


def test_dedupes_repeated_outputs_and_errors():
    events = [
        {"type": "tool_call_result", "name": "t", "status": "ok", "output": "same"},
        {"type": "tool_call_result", "name": "t", "status": "ok", "output": "same"},
        {"type": "tool_call_result", "name": "t", "status": "error",
         "error_class": "Crash", "error": "boom"},
        {"type": "tool_call_result", "name": "t", "status": "error",
         "error_class": "Crash", "error": "boom"},
    ]
    summary = summarize_events([_trace(events)])
    assert sum(1 for s in summary.sample_outputs if "same" in s) == 1
    assert sum(1 for m in summary.error_modes if "boom" in m) == 1
