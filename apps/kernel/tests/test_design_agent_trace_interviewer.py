"""Unit tests for the trace-import LLM interviewer.

Anthropic is mocked end-to-end. Asserts `pick_next_question_from_traces`:
  * Returns None when every dimension is covered.
  * Builds a user message grounded in the trace summary + agent definition.
  * Validates the LLM response and rejects bad dimension picks /
    out-of-range recommendation indexes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from ownevo_kernel.design_agent import (
    DIMENSION_SPECS,
    InterviewerError,
    PriorAnswer,
    QuestionBrief,
)
from ownevo_kernel.design_agent.trace_interviewer import (
    pick_next_question_from_traces,
)
from ownevo_kernel.design_agent.trace_summary import summarize_events


@dataclass
class _FakeToolBlock:
    type: str
    name: str
    input: dict


@dataclass
class _FakeMessage:
    content: list[_FakeToolBlock]
    stop_reason: str = "tool_use"


class _FakeMessages:
    def __init__(self, response_input: dict[str, Any]):
        self._response_input = response_input
        self.last_call_kwargs: dict[str, Any] = {}

    async def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        return _FakeMessage(
            content=[
                _FakeToolBlock(
                    type="tool_use", name="ask_question", input=self._response_input
                )
            ]
        )


class _FakeClient:
    def __init__(self, response_input: dict[str, Any]):
        self.messages = _FakeMessages(response_input)


_VALID_BRIEF_INPUT: dict[str, Any] = {
    "dimension": "success_metric",
    "question": "Your traces show forecast_demand drives a flag step — what counts as success?",
    "eli": (
        "We need to know whether catching every at-risk SKU or avoiding false "
        "flags matters more for this imported agent."
    ),
    "stakes": (
        "Pick the wrong direction and the gate rejects every genuine "
        "improvement to the agent."
    ),
    "options": [
        {
            "label": "Recall — catch every at-risk SKU",
            "pro": "Misses are the costly failure for stockout-flagging agents.",
            "con": "Tolerates more false flags the planner must triage.",
        },
        {
            "label": "Precision — keep false flags low",
            "pro": "Protects planner trust in the flag queue.",
            "con": "Lets some genuine stockouts slip through.",
        },
    ],
    "recommendation_index": 0,
    "rationale": (
        "The traces show flag_stockout firing after forecast_demand; missing a "
        "real stockout is the failure this agent already produced."
    ),
}


def _summary():
    events = [
        {"type": "tool_call_start", "name": "forecast_demand", "args": {"sku": "A1"}},
        {"type": "tool_call_result", "name": "forecast_demand", "status": "ok",
         "output": {"units": 120}},
        {"type": "tool_call_start", "name": "flag_stockout", "args": {"threshold": 0.2}},
    ]
    return summarize_events([(uuid4(), events)])


async def test_returns_none_when_all_dimensions_covered():
    prior = [
        PriorAnswer(dimension=d.key, question="(p)", chosen_option="A", free_text=None)
        for d in DIMENSION_SPECS
    ]
    client = _FakeClient(_VALID_BRIEF_INPUT)
    out = await pick_next_question_from_traces(
        summary=_summary(), agent_definition=None, prior_answers=prior, client=client
    )
    assert out is None
    assert client.messages.last_call_kwargs == {}


async def test_returns_brief_on_first_call():
    client = _FakeClient(_VALID_BRIEF_INPUT)
    out = await pick_next_question_from_traces(
        summary=_summary(), agent_definition=None, prior_answers=[], client=client
    )
    assert isinstance(out, QuestionBrief)
    assert out.dimension == "success_metric"
    assert len(out.options) == 2


async def test_user_message_grounds_in_trace_summary_and_definition():
    client = _FakeClient(_VALID_BRIEF_INPUT)
    await pick_next_question_from_traces(
        summary=_summary(),
        agent_definition="You are a demand-planning assistant. Flag risky SKUs.",
        prior_answers=[],
        client=client,
    )
    user_content = client.messages.last_call_kwargs["messages"][0]["content"]
    # Observed tool names from the summary surface in the prompt.
    assert "forecast_demand" in user_content
    assert "flag_stockout" in user_content
    # Agent definition is inlined as stated intent.
    assert "demand-planning assistant" in user_content
    # Open dimensions still listed for the LLM to pick from.
    for d in DIMENSION_SPECS:
        assert d.key in user_content


async def test_rejects_closed_dimension_pick():
    bad = dict(_VALID_BRIEF_INPUT)
    bad["dimension"] = "goal_and_scope"
    client = _FakeClient(bad)
    prior = [
        PriorAnswer(
            dimension="goal_and_scope", question="?", chosen_option="x", free_text=None
        )
    ]
    with pytest.raises(InterviewerError, match="closed/unknown dimension"):
        await pick_next_question_from_traces(
            summary=_summary(), agent_definition=None, prior_answers=prior, client=client
        )


async def test_rejects_out_of_range_recommendation():
    bad = dict(_VALID_BRIEF_INPUT)
    bad["recommendation_index"] = 99
    client = _FakeClient(bad)
    with pytest.raises(InterviewerError, match="out of range"):
        await pick_next_question_from_traces(
            summary=_summary(), agent_definition=None, prior_answers=[], client=client
        )


async def test_client_required():
    with pytest.raises(InterviewerError, match="No Anthropic client"):
        await pick_next_question_from_traces(
            summary=_summary(), agent_definition=None, prior_answers=[], client=None
        )
