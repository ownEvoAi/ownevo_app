"""Unit tests for the trace-import reverse-discovery summary.

Covers the deterministic fallback render and the LLM path (Anthropic
mocked end-to-end). Asserts `generate_reverse_discovery_summary`:
  * Returns the model's text, capped, with the right basis.
  * Grounds the prompt in the trace summary + agent definition.
  * Raises `InterviewerError` on empty text / LLM failure / no client,
    so the route can fall back.
And `fallback_reverse_discovery_summary`:
  * Names observed tools and surfaces error counts.
  * Handles the empty-trace case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from ownevo_kernel.design_agent import InterviewerError
from ownevo_kernel.design_agent.reverse_discovery import (
    ReverseDiscoverySummary,
    fallback_reverse_discovery_summary,
    generate_reverse_discovery_summary,
)
from ownevo_kernel.design_agent.trace_summary import TraceSummary, summarize_events


@dataclass
class _FakeTextBlock:
    type: str
    text: str


@dataclass
class _FakeMessage:
    content: list[_FakeTextBlock]
    stop_reason: str = "end_turn"


class _FakeMessages:
    def __init__(self, text: str):
        self._text = text
        self.last_call_kwargs: dict[str, Any] = {}

    async def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        return _FakeMessage(content=[_FakeTextBlock(type="text", text=self._text)])


class _FakeClient:
    def __init__(self, text: str):
        self.messages = _FakeMessages(text)


class _RaisingMessages:
    async def create(self, **kwargs):
        raise RuntimeError("boom")


class _RaisingClient:
    def __init__(self):
        self.messages = _RaisingMessages()


def _summary_with_tools() -> TraceSummary:
    events = [
        {"type": "tool_call_start", "name": "forecast_demand", "args": {"sku": "A1"}},
        {"type": "tool_call_result", "name": "forecast_demand", "status": "ok",
         "output": {"units": 120}},
        {"type": "tool_call_start", "name": "flag_stockout", "args": {"threshold": 0.2}},
        {"type": "tool_call_result", "name": "flag_stockout", "status": "error",
         "error_class": "Timeout", "error": "downstream timed out"},
    ]
    return summarize_events([(uuid4(), events)])


# --- LLM path ---------------------------------------------------------------


async def test_llm_returns_capped_text_with_basis():
    client = _FakeClient("This agent forecasts next-week demand and flags stockout risk.")
    out = await generate_reverse_discovery_summary(
        summary=_summary_with_tools(), agent_definition=None, client=client
    )
    assert isinstance(out, ReverseDiscoverySummary)
    assert out.is_fallback is False
    assert out.basis == "traces"
    assert "forecasts next-week demand" in out.summary


async def test_basis_reflects_definition_present():
    client = _FakeClient("Does forecasting.")
    out = await generate_reverse_discovery_summary(
        summary=_summary_with_tools(),
        agent_definition="You are a demand-planning assistant.",
        client=client,
    )
    assert out.basis == "definition+traces"


async def test_prompt_grounds_in_summary_and_definition():
    client = _FakeClient("Does forecasting.")
    await generate_reverse_discovery_summary(
        summary=_summary_with_tools(),
        agent_definition="You are a demand-planning assistant.",
        client=client,
    )
    user_content = client.messages.last_call_kwargs["messages"][0]["content"]
    assert "forecast_demand" in user_content
    assert "demand-planning assistant" in user_content
    # Plain-text completion — no tool forcing.
    assert "tools" not in client.messages.last_call_kwargs


async def test_long_text_is_capped():
    client = _FakeClient("x" * 5000)
    out = await generate_reverse_discovery_summary(
        summary=_summary_with_tools(), agent_definition=None, client=client
    )
    assert len(out.summary) <= 600


async def test_empty_text_raises():
    client = _FakeClient("   ")
    with pytest.raises(InterviewerError, match="no text"):
        await generate_reverse_discovery_summary(
            summary=_summary_with_tools(), agent_definition=None, client=client
        )


async def test_llm_failure_raises():
    with pytest.raises(InterviewerError, match="LLM call failed"):
        await generate_reverse_discovery_summary(
            summary=_summary_with_tools(),
            agent_definition=None,
            client=_RaisingClient(),
        )


async def test_no_client_raises():
    with pytest.raises(InterviewerError, match="No Anthropic client"):
        await generate_reverse_discovery_summary(
            summary=_summary_with_tools(), agent_definition=None, client=None
        )


# --- deterministic fallback -------------------------------------------------


def test_fallback_names_tools_and_errors():
    out = fallback_reverse_discovery_summary(_summary_with_tools())
    assert out.is_fallback is True
    assert out.basis == "traces"
    assert "forecast_demand" in out.summary
    assert "flag_stockout" in out.summary
    assert "errored" in out.summary


def test_fallback_collapses_extra_tools():
    events = [
        {"type": "tool_call_start", "name": f"tool_{i}", "args": {}}
        for i in range(7)
    ]
    summary = summarize_events([(uuid4(), events)])
    out = fallback_reverse_discovery_summary(summary)
    assert "3 other tools" in out.summary


def test_fallback_empty_summary():
    out = fallback_reverse_discovery_summary(TraceSummary())
    assert out.is_fallback is True
    assert "could not be inferred" in out.summary


def test_fallback_basis_with_definition():
    out = fallback_reverse_discovery_summary(
        _summary_with_tools(), agent_definition="stated intent"
    )
    assert out.basis == "definition+traces"
