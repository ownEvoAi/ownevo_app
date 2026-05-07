"""Tests for `approvers.llm_judge.judge_proposal` (W5.2).

Mirrors `test_nl_gen_meta_eval_judge.py`:
  1. Fake AsyncAnthropic — pins tool definition shape, system-prompt
     load-bearing rules, the contract on tool_use → ApprovalJudgment,
     and the four error paths (no tool_use, malformed input, extra
     field, proposal-id mismatch).
  2. User-message payload — verifies the six context fields land in
     the prompt and the explanation is fenced verbatim.
  3. ProposalContext validation — the dataclass `__post_init__` checks
     trip on empty / bad inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.approvers.judgment import ApprovalJudgment
from ownevo_kernel.approvers.llm_judge import (
    _TOOL_DEFINITION,
    SYSTEM_PROMPT,
    TOOL_NAME,
    JudgeProposalIdMismatchError,
    JudgmentValidationError,
    NoJudgeToolUseError,
    ProposalContext,
    judge_proposal,
)

# ---------------------------------------------------------------------------
# Fake AsyncAnthropic
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedResponse:
    content: list[Any]
    stop_reason: str = "tool_use"


class _FakeMessages:
    def __init__(self, response: _ScriptedResponse) -> None:
        self._response = response
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            content=self._response.content,
            stop_reason=self._response.stop_reason,
        )


@dataclass
class _FakeClient:
    response: _ScriptedResponse
    messages: _FakeMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _FakeMessages(self.response)


def _tool_use_block(name: str, payload: dict | str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload, id="tu_1")


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _good_context() -> ProposalContext:
    return ProposalContext(
        proposal_id="00000000-0000-0000-0000-000000000001",
        cluster_label="weekend-spike-under-forecast",
        cluster_summary="Weekend snack demand under-predicted.",
        skill_id="feature_engineer",
        metric_name="RMSSE",
        metric_improvement_axis="lower-is-better",
        explanation=(
            "Cluster `weekend-spike-under-forecast` — added is_weekend × "
            "snack interaction. Expect RMSSE to drop ~5%."
        ),
    )


def _good_judgment_payload(proposal_id: str) -> dict:
    return {
        "schema_version": "0.1",
        "proposal_id": proposal_id,
        "references_cluster": {
            "verdict": "pass",
            "rationale": "Cluster named verbatim.",
        },
        "names_change": {
            "verdict": "pass",
            "rationale": "Specific change: added interaction term.",
        },
        "states_direction": {
            "verdict": "pass",
            "rationale": "Reduce-RMSSE on lower-is-better — direction OK.",
        },
        "overall_rationale": (
            "All three structural elements present and direction is "
            "consistent with the metric's improvement axis."
        ),
    }


# ---------------------------------------------------------------------------
# Tool-definition pinning
# ---------------------------------------------------------------------------


def test_tool_definition_shape():
    td = _TOOL_DEFINITION
    assert td["name"] == TOOL_NAME
    assert td["description"]
    schema = td["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["judgment"]
    j_schema = schema["properties"]["judgment"]
    assert j_schema["type"] == "object"
    required = set(j_schema["required"])
    for f in (
        "proposal_id",
        "references_cluster",
        "names_change",
        "states_direction",
        "overall_rationale",
    ):
        assert f in required


def test_system_prompt_pins_load_bearing_rules():
    """A future edit can't silently drop the structural-check contract."""
    p = SYSTEM_PROMPT
    # Three checks named
    assert "references_cluster" in p
    assert "names_change" in p
    assert "states_direction" in p
    # Binary verdict scale (no `partial`)
    assert "pass" in p
    assert "fail" in p
    # Improvement-axis framing — load-bearing for catching wrong-direction
    assert "improvement axis" in p or "improvement direction" in p
    # Wrong-direction adversarial case is called out
    assert "wrong direction" in p.lower() or "WRONG direction" in p
    # Calibration warning
    assert "calibrated" in p or "calibrat" in p
    # Schema-version field is top-level only — observed model quirk
    assert "Do NOT add a `schema_version`" in p


# ---------------------------------------------------------------------------
# Pass: tool_use → ApprovalJudgment
# ---------------------------------------------------------------------------


async def test_tool_use_returning_valid_judgment_round_trips():
    ctx = _good_context()
    payload = _good_judgment_payload(ctx.proposal_id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    result = await judge_proposal(client, ctx)
    assert isinstance(result, ApprovalJudgment)
    assert result.proposal_id == ctx.proposal_id
    assert result.admits is True


async def test_flat_tool_input_without_wrapper_round_trips():
    """Some models emit the judgment un-wrapped — accept either shape."""
    ctx = _good_context()
    payload = _good_judgment_payload(ctx.proposal_id)
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, payload)])
    )
    result = await judge_proposal(client, ctx)
    assert result.proposal_id == ctx.proposal_id


async def test_user_message_carries_context_and_explanation():
    ctx = _good_context()
    payload = _good_judgment_payload(ctx.proposal_id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    await judge_proposal(client, ctx)
    kw = client.messages.last_kwargs
    assert kw["system"] == SYSTEM_PROMPT
    assert kw["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert kw["tools"][0]["name"] == TOOL_NAME
    user_content = kw["messages"][0]["content"]
    # All six context fields appear
    assert ctx.proposal_id in user_content
    assert ctx.cluster_label in user_content
    assert ctx.cluster_summary in user_content
    assert ctx.skill_id in user_content
    assert ctx.metric_name in user_content
    assert ctx.metric_improvement_axis in user_content
    # Explanation appears verbatim and is fenced
    assert ctx.explanation in user_content
    assert "```" in user_content


async def test_judge_accepts_model_and_max_tokens_overrides():
    ctx = _good_context()
    payload = _good_judgment_payload(ctx.proposal_id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    await judge_proposal(
        client,
        ctx,
        model="claude-haiku-4-5",
        max_tokens=2_000,
    )
    kw = client.messages.last_kwargs
    assert kw["model"] == "claude-haiku-4-5"
    assert kw["max_tokens"] == 2_000


# ---------------------------------------------------------------------------
# Fail: no tool_use
# ---------------------------------------------------------------------------


async def test_text_only_response_raises_no_tool_use():
    ctx = _good_context()
    client = _FakeClient(
        _ScriptedResponse(
            content=[_text_block("I think this proposal looks reasonable.")],
            stop_reason="end_turn",
        )
    )
    with pytest.raises(NoJudgeToolUseError) as exc_info:
        await judge_proposal(client, ctx)
    assert exc_info.value.stop_reason == "end_turn"
    assert "looks reasonable" in exc_info.value.content_preview


async def test_wrong_tool_name_raises_no_tool_use():
    ctx = _good_context()
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block("not_the_judge_tool", {"x": 1})],
            stop_reason="tool_use",
        )
    )
    with pytest.raises(NoJudgeToolUseError):
        await judge_proposal(client, ctx)


# ---------------------------------------------------------------------------
# Fail: tool_use with malformed input
# ---------------------------------------------------------------------------


async def test_invalid_tool_input_raises_validation_error():
    ctx = _good_context()
    bad = {"proposal_id": ctx.proposal_id}  # missing checks + rationale
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, {"judgment": bad})])
    )
    with pytest.raises(JudgmentValidationError) as exc_info:
        await judge_proposal(client, ctx)
    assert exc_info.value.raw_input == {"judgment": bad}
    assert exc_info.value.pydantic_error.error_count() > 0


async def test_extra_field_in_tool_input_raises_validation_error():
    ctx = _good_context()
    payload = _good_judgment_payload(ctx.proposal_id)
    payload["bonus_check"] = {"verdict": "pass", "rationale": "x"}
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    with pytest.raises(JudgmentValidationError):
        await judge_proposal(client, ctx)


# ---------------------------------------------------------------------------
# Fail: proposal-id mismatch (cross-context invariant)
# ---------------------------------------------------------------------------


async def test_proposal_id_mismatch_raises_dedicated_error():
    """Judge copies the wrong proposal_id — surface loudly because
    downstream audit-log joins on that id would silently break."""
    ctx = _good_context()
    payload = _good_judgment_payload("00000000-0000-0000-0000-deadbeefcafe")
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    with pytest.raises(JudgeProposalIdMismatchError) as exc_info:
        await judge_proposal(client, ctx)
    assert exc_info.value.expected_id == ctx.proposal_id
    assert exc_info.value.judgment.proposal_id == "00000000-0000-0000-0000-deadbeefcafe"


# ---------------------------------------------------------------------------
# Defensive parsing — opus 4.7 quirks observed in the A4.6 live smoke
# ---------------------------------------------------------------------------


async def test_json_encoded_string_payload_is_parsed():
    """Opus 4.7 sometimes returns the wrapped value as a JSON-encoded
    string instead of a dict (observed in the A4.6 live smoke
    2026-05-06). The judge json.loads it and proceeds."""
    ctx = _good_context()
    payload = _good_judgment_payload(ctx.proposal_id)
    json_string = json.dumps(payload)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": json_string})]
        )
    )
    result = await judge_proposal(client, ctx)
    assert isinstance(result, ApprovalJudgment)
    assert result.proposal_id == ctx.proposal_id


async def test_non_json_string_payload_still_raises():
    """The string-decode fallback is best-effort — non-JSON strings
    still fail with the typed validation error so a real model
    regression doesn't slip through silently."""
    ctx = _good_context()
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": "not json at all"})]
        )
    )
    with pytest.raises(JudgmentValidationError):
        await judge_proposal(client, ctx)


async def test_check_schema_version_field_is_stripped():
    """Opus 4.7 sometimes propagates the top-level `schema_version` into
    each per-check sub-object. `StructuralCheck` is `extra='forbid'`,
    so the judge strips the spurious key before model_validate."""
    ctx = _good_context()
    payload = _good_judgment_payload(ctx.proposal_id)
    for check_key in ("references_cluster", "names_change", "states_direction"):
        payload[check_key]["schema_version"] = "0.1"
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    result = await judge_proposal(client, ctx)
    assert isinstance(result, ApprovalJudgment)
    assert result.references_cluster.verdict == "pass"


async def test_other_extra_fields_in_check_still_fail():
    """The strip is targeted to `schema_version` only — every other
    unexpected per-check field still triggers a typed validation error."""
    ctx = _good_context()
    payload = _good_judgment_payload(ctx.proposal_id)
    payload["references_cluster"]["confidence"] = 0.7  # Not schema_version
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    with pytest.raises(JudgmentValidationError):
        await judge_proposal(client, ctx)


# ---------------------------------------------------------------------------
# ProposalContext validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "proposal_id",
        "cluster_label",
        "cluster_summary",
        "skill_id",
        "metric_name",
        "explanation",
    ],
)
def test_proposal_context_rejects_empty_field(field_name: str):
    kwargs = {
        "proposal_id": "p1",
        "cluster_label": "weekend-spike",
        "cluster_summary": "summary",
        "skill_id": "feat",
        "metric_name": "RMSSE",
        "metric_improvement_axis": "lower-is-better",
        "explanation": "a real explanation",
    }
    kwargs[field_name] = ""
    with pytest.raises(ValueError, match=field_name):
        ProposalContext(**kwargs)


def test_proposal_context_rejects_invalid_axis():
    with pytest.raises(ValueError, match="metric_improvement_axis"):
        ProposalContext(
            proposal_id="p1",
            cluster_label="c",
            cluster_summary="s",
            skill_id="sk",
            metric_name="m",
            metric_improvement_axis="middle-is-best",  # type: ignore[arg-type]
            explanation="e",
        )


@pytest.mark.parametrize("axis", ["lower-is-better", "higher-is-better"])
def test_proposal_context_accepts_both_axes(axis: str):
    ctx = ProposalContext(
        proposal_id="p1",
        cluster_label="c",
        cluster_summary="s",
        skill_id="sk",
        metric_name="m",
        metric_improvement_axis=axis,  # type: ignore[arg-type]
        explanation="e",
    )
    assert ctx.metric_improvement_axis == axis
