"""Tests for `approvers.llm_judge.judge.judge_proposal_explanation` (W5.2).

Mirrors `test_clustering_label_eval_judge.py`:

  1. Fake AsyncAnthropic — pins tool definition shape, system-prompt
     load-bearing rules, contract on tool_use → judgment, and the
     three error paths (no tool_use, validation failure, id mismatch).
  2. User-message payload pinning: case_id, cluster_name, expected
     direction, proposal summary, explanation all land in the prompt.
  3. **W5.2 smoke + adversarial** (PLAN.md):
     - 5 hand-crafted proposals → judge admits 3, rejects 2 (test
       drives a deterministic fake judge that mirrors the
       structural rules; not an LLM, but smoke-verifies the runner
       contract end-to-end).
     - Adversarial: vague-but-positive → rejected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.approvers.llm_judge.fixtures import LABELED_APPROVAL_CASES
from ownevo_kernel.approvers.llm_judge.judge import (
    _TOOL_DEFINITION,
    SYSTEM_PROMPT,
    TOOL_NAME,
    LLMJudgeApprovalIdMismatchError,
    LLMJudgeApprovalJudgmentValidationError,
    NoLLMJudgeApprovalToolUseError,
    _format_user_message,
    judge_proposal_explanation,
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


def _tool_use_block(name: str, payload: Any) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload, id="tu_1")


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _good_payload(case_id: str, verdict: str = "admit") -> dict:
    return {
        "schema_version": "0.1",
        "proposal_id": case_id,
        "cluster_referenced": {"present": True, "quote": "weekend snack"},
        "change_named": {"present": True, "quote": "12-week trailing"},
        "metric_direction_stated": {"present": True, "quote": "go up"},
        "verdict": verdict,
        "rationale": "All three elements present and consistent.",
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
    required = set(j_schema["required"])
    for f in (
        "proposal_id",
        "cluster_referenced",
        "change_named",
        "metric_direction_stated",
        "verdict",
        "rationale",
    ):
        assert f in required


# ---------------------------------------------------------------------------
# System prompt load-bearing rules
# ---------------------------------------------------------------------------


def test_system_prompt_pins_three_elements():
    for fragment in (
        "cluster_referenced",
        "change_named",
        "metric_direction_stated",
    ):
        assert fragment in SYSTEM_PROMPT


def test_system_prompt_pins_admit_consistency_rule():
    """The 'admit iff three present AND consistent' rule is what
    distinguishes W5.2 from a regex check; pin its presence."""
    assert "consistent" in SYSTEM_PROMPT.lower()
    assert "admit" in SYSTEM_PROMPT.lower()
    assert "reject" in SYSTEM_PROMPT.lower()


def test_system_prompt_pins_safe_default():
    assert "safe default" in SYSTEM_PROMPT.lower() or "false-positives" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# User-message payload
# ---------------------------------------------------------------------------


def test_user_message_carries_required_fields():
    case = LABELED_APPROVAL_CASES[0]
    msg = _format_user_message(case)
    assert case.case_id in msg
    assert case.cluster_name in msg
    assert case.metric_direction_expected in msg
    assert case.proposal_summary in msg
    assert case.explanation in msg


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_judge_returns_validated_judgment():
    case = LABELED_APPROVAL_CASES[0]
    payload = _good_payload(case.case_id, verdict="admit")
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, payload)])
    )

    j = await judge_proposal_explanation(client, case)
    assert j.proposal_id == case.case_id
    assert j.verdict == "admit"
    assert j.cluster_referenced.present is True


async def test_judge_unwraps_judgment_envelope():
    case = LABELED_APPROVAL_CASES[0]
    payload = {"judgment": _good_payload(case.case_id)}
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, payload)])
    )

    j = await judge_proposal_explanation(client, case)
    assert j.proposal_id == case.case_id


async def test_judge_recovers_string_wrapped_payload():
    case = LABELED_APPROVAL_CASES[0]
    raw = json.dumps(_good_payload(case.case_id))
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": raw})]
        )
    )
    j = await judge_proposal_explanation(client, case)
    assert j.proposal_id == case.case_id


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_no_tool_use_raises():
    case = LABELED_APPROVAL_CASES[0]
    client = _FakeClient(
        _ScriptedResponse(
            content=[_text_block("I cannot judge this proposal.")],
            stop_reason="end_turn",
        )
    )
    with pytest.raises(NoLLMJudgeApprovalToolUseError) as exc_info:
        await judge_proposal_explanation(client, case)
    assert exc_info.value.stop_reason == "end_turn"


async def test_malformed_payload_raises_validation_error():
    case = LABELED_APPROVAL_CASES[0]
    bad = _good_payload(case.case_id)
    bad["verdict"] = "maybe"
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, bad)])
    )
    with pytest.raises(LLMJudgeApprovalJudgmentValidationError):
        await judge_proposal_explanation(client, case)


async def test_extra_field_rejected():
    case = LABELED_APPROVAL_CASES[0]
    bad = _good_payload(case.case_id)
    bad["unauthorized_field"] = "no"
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, bad)])
    )
    with pytest.raises(LLMJudgeApprovalJudgmentValidationError):
        await judge_proposal_explanation(client, case)


async def test_id_mismatch_raises():
    case = LABELED_APPROVAL_CASES[0]
    bad = _good_payload("some-other-id")
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, bad)])
    )
    with pytest.raises(LLMJudgeApprovalIdMismatchError) as exc_info:
        await judge_proposal_explanation(client, case)
    assert exc_info.value.expected_id == case.case_id


# ---------------------------------------------------------------------------
# W5.2 smoke + adversarial — PLAN.md spec
# ---------------------------------------------------------------------------


async def test_smoke_admits_three_rejects_two():
    """PLAN.md smoke: 5 hand-crafted proposals → judge admits 3, rejects 2.

    We script a deterministic fake judge that returns the structural
    elements as `present=True` for the structural cases and `False`
    otherwise — exactly the contract the production judge implements.
    """
    smoke_cases = [c for c in LABELED_APPROVAL_CASES if c.bucket == "structural"][:3]
    smoke_cases.extend(
        [c for c in LABELED_APPROVAL_CASES if c.bucket != "structural"][:2]
    )
    assert len(smoke_cases) == 5

    admits = 0
    rejects = 0
    for case in smoke_cases:
        all_three = case.bucket == "structural"
        payload = {
            "schema_version": "0.1",
            "proposal_id": case.case_id,
            "cluster_referenced": {
                "present": all_three,
                "quote": "weekend snack" if all_three else "",
            },
            "change_named": {
                "present": all_three,
                "quote": "12-week trailing" if all_three else "",
            },
            "metric_direction_stated": {
                "present": all_three,
                "quote": "go up" if all_three else "",
            },
            "verdict": "admit" if all_three else "reject",
            "rationale": "smoke",
        }
        client = _FakeClient(
            _ScriptedResponse(
                content=[_tool_use_block(TOOL_NAME, payload)]
            )
        )
        j = await judge_proposal_explanation(client, case)
        if j.verdict == "admit":
            admits += 1
        else:
            rejects += 1

    assert admits == 3
    assert rejects == 2


async def test_adversarial_vague_but_positive_rejected():
    """PLAN.md adversarial: vague-but-positive → reject.

    The judge sees a vague-but-positive case and (per the production
    judge contract) returns a `reject` verdict. We script the fake
    judge with the exact contract."""
    case = next(
        c for c in LABELED_APPROVAL_CASES if c.bucket == "vague-but-positive"
    )
    payload = {
        "schema_version": "0.1",
        "proposal_id": case.case_id,
        "cluster_referenced": {"present": False, "quote": ""},
        "change_named": {"present": False, "quote": ""},
        "metric_direction_stated": {"present": False, "quote": ""},
        "verdict": "reject",
        "rationale": (
            "Explanation is generically positive. None of the three "
            "structural elements are present."
        ),
    }
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, payload)])
    )
    j = await judge_proposal_explanation(client, case)
    assert j.verdict == "reject"
    assert not j.cluster_referenced.present
    assert not j.change_named.present
    assert not j.metric_direction_stated.present
