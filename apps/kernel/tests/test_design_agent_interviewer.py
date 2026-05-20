"""Unit tests for the LLM-driven design-agent interviewer.

Anthropic is mocked end-to-end; these tests assert that
`pick_next_question`:
  * Returns None when every dimension is covered.
  * Constructs a user message that names the open dimensions.
  * Validates the LLM response against the schema and rejects bad
    dimension picks / out-of-range recommendation indexes.
  * Maps a hardcoded `DiscoveryQuestion` to the new brief shape via
    the route helper (round-trip sanity).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from ownevo_kernel.design_agent import (
    DIMENSION_SPECS,
    InterviewerError,
    PriorAnswer,
    QuestionBrief,
    dimensions_remaining,
    pick_next_question,
)


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
                ),
            ]
        )


class _FakeClient:
    def __init__(self, response_input: dict[str, Any]):
        self.messages = _FakeMessages(response_input)


_VALID_BRIEF_INPUT: dict[str, Any] = {
    "dimension": "goal_and_scope",
    "question": "What outcome ships in week one?",
    "eli": (
        "We need the smallest first slice the supply-chain VP would call "
        "useful — narrow beats broad."
    ),
    "stakes": (
        "Picking too broad lets NL-gen mint dozens of eval cases the team "
        "cannot agree to label."
    ),
    "options": [
        {
            "label": "Markdown alerts only",
            "pro": "Narrowest wedge — fits one operator role end to end.",
            "con": "Leaves promo lift work for a later workflow.",
        },
        {
            "label": "Markdown + promo lift forecasting",
            "pro": "Covers more of the planner's day-to-day decision.",
            "con": "Doubles the eval surface in week one.",
        },
    ],
    "recommendation_index": 0,
    "rationale": (
        "The description names markdown timing as the past-miss area; "
        "winning that one inch first beats a wider but shallower spec."
    ),
}


_DESC = (
    "Forecast weekly demand at SKU-store level for our 8,400 SKU catalog "
    "across 142 stores. Flag SKUs likely to need markdown within four weeks."
)


async def test_returns_none_when_all_dimensions_covered():
    prior = [
        PriorAnswer(
            dimension=d.key,
            question="(placeholder)",
            chosen_option="A",
            free_text=None,
        )
        for d in DIMENSION_SPECS
    ]
    client = _FakeClient(_VALID_BRIEF_INPUT)
    out = await pick_next_question(
        description=_DESC, template_id=None, prior_answers=prior, client=client
    )
    assert out is None
    # And the LLM was never invoked.
    assert client.messages.last_call_kwargs == {}


async def test_returns_brief_on_first_call():
    client = _FakeClient(_VALID_BRIEF_INPUT)
    out = await pick_next_question(
        description=_DESC, template_id=None, prior_answers=[], client=client
    )
    assert isinstance(out, QuestionBrief)
    assert out.dimension == "goal_and_scope"
    assert out.recommendation_index == 0
    assert len(out.options) == 2


async def test_user_message_lists_open_dimensions():
    client = _FakeClient(_VALID_BRIEF_INPUT)
    await pick_next_question(
        description=_DESC,
        template_id="retail-demand-planning",
        prior_answers=[],
        client=client,
    )
    kwargs = client.messages.last_call_kwargs
    user_content = kwargs["messages"][0]["content"]
    for d in DIMENSION_SPECS:
        assert d.key in user_content, f"open dimension {d.key!r} missing from user msg"
    # Template id surfaces too so the LLM can adapt the question.
    assert "retail-demand-planning" in user_content


async def test_rejects_closed_dimension_pick():
    """LLM tries to target a dimension that's already covered."""
    bad = dict(_VALID_BRIEF_INPUT)
    bad["dimension"] = "success_metric"  # we mark this as already covered
    client = _FakeClient(bad)
    prior = [
        PriorAnswer(
            dimension="success_metric",
            question="Metric?",
            chosen_option="balanced_accuracy",
            free_text=None,
        )
    ]
    with pytest.raises(InterviewerError, match="closed/unknown dimension"):
        await pick_next_question(
            description=_DESC, template_id=None, prior_answers=prior, client=client
        )


async def test_rejects_out_of_range_recommendation():
    bad = dict(_VALID_BRIEF_INPUT)
    bad["recommendation_index"] = 99
    client = _FakeClient(bad)
    with pytest.raises(InterviewerError, match="out of range"):
        await pick_next_question(
            description=_DESC, template_id=None, prior_answers=[], client=client
        )


async def test_rejects_response_without_tool_use():
    class _NoToolClient:
        class messages:
            @staticmethod
            async def create(**_):
                return _FakeMessage(content=[], stop_reason="end_turn")

    with pytest.raises(InterviewerError, match="did not call ask_question"):
        await pick_next_question(
            description=_DESC,
            template_id=None,
            prior_answers=[],
            client=_NoToolClient(),
        )


async def test_skips_prior_answers_without_dimension():
    """Legacy clients may send answers without a dimension; those don't count
    toward coverage but also don't raise."""
    client = _FakeClient(_VALID_BRIEF_INPUT)
    prior = [
        PriorAnswer(dimension="", question="legacy", chosen_option="A", free_text=None),
    ]
    out = await pick_next_question(
        description=_DESC, template_id=None, prior_answers=prior, client=client
    )
    assert out is not None
    # All seven dimensions are still considered open.
    assert len(dimensions_remaining({""})) == len(DIMENSION_SPECS)


async def test_client_required():
    with pytest.raises(InterviewerError, match="No Anthropic client"):
        await pick_next_question(
            description=_DESC, template_id=None, prior_answers=[], client=None
        )
