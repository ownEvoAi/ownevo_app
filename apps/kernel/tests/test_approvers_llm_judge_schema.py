"""Schema tests for `approvers.llm_judge.judgment` (W5.2)."""

from __future__ import annotations

import pytest
from ownevo_kernel.approvers.llm_judge.judgment import (
    SCHEMA_VERSION,
    LLMJudgeApprovalJudgment,
    StructuralElement,
    verdict_score,
)
from pydantic import ValidationError


def _good_dict() -> dict:
    return {
        "schema_version": "0.1",
        "proposal_id": "struct-01-weekend-snack",
        "cluster_referenced": {
            "present": True,
            "quote": "the CA snack weekend under-forecast cluster",
        },
        "change_named": {
            "present": True,
            "quote": "12-week trailing weekend-only mean",
        },
        "metric_direction_stated": {
            "present": True,
            "quote": "recall ... go up",
        },
        "verdict": "admit",
        "rationale": "All three elements present and consistent.",
    }


def test_schema_version_is_pinned():
    assert SCHEMA_VERSION == "0.1"


def test_round_trip_identity():
    payload = _good_dict()
    j = LLMJudgeApprovalJudgment.model_validate(payload)
    assert j.model_dump(mode="json") == payload


def test_extra_fields_forbidden():
    payload = _good_dict()
    payload["extra"] = "no"
    with pytest.raises(ValidationError):
        LLMJudgeApprovalJudgment.model_validate(payload)


def test_extra_fields_forbidden_on_element():
    payload = _good_dict()
    payload["cluster_referenced"]["extra"] = "no"
    with pytest.raises(ValidationError):
        LLMJudgeApprovalJudgment.model_validate(payload)


def test_judgment_is_frozen():
    j = LLMJudgeApprovalJudgment.model_validate(_good_dict())
    with pytest.raises(ValidationError):
        j.verdict = "reject"  # type: ignore[misc]


def test_structural_element_is_frozen():
    el = StructuralElement(present=True, quote="x")
    with pytest.raises(ValidationError):
        el.present = False  # type: ignore[misc]


def test_proposal_id_min_length():
    payload = _good_dict()
    payload["proposal_id"] = ""
    with pytest.raises(ValidationError):
        LLMJudgeApprovalJudgment.model_validate(payload)


def test_rationale_min_length():
    payload = _good_dict()
    payload["rationale"] = ""
    with pytest.raises(ValidationError):
        LLMJudgeApprovalJudgment.model_validate(payload)


def test_rationale_max_length():
    payload = _good_dict()
    payload["rationale"] = "x" * 601
    with pytest.raises(ValidationError):
        LLMJudgeApprovalJudgment.model_validate(payload)


def test_quote_max_length():
    payload = _good_dict()
    payload["cluster_referenced"]["quote"] = "x" * 401
    with pytest.raises(ValidationError):
        LLMJudgeApprovalJudgment.model_validate(payload)


@pytest.mark.parametrize("verdict", ["admit", "reject"])
def test_verdict_accepts_canonical_values(verdict: str):
    payload = _good_dict()
    payload["verdict"] = verdict
    j = LLMJudgeApprovalJudgment.model_validate(payload)
    assert j.verdict == verdict


def test_verdict_rejects_unknown_value():
    payload = _good_dict()
    payload["verdict"] = "maybe"
    with pytest.raises(ValidationError):
        LLMJudgeApprovalJudgment.model_validate(payload)


def test_verdict_score_admit_is_one():
    assert verdict_score("admit") == 1.0


def test_verdict_score_reject_is_zero():
    assert verdict_score("reject") == 0.0


def test_schema_version_must_be_literal():
    payload = _good_dict()
    payload["schema_version"] = "0.2"
    with pytest.raises(ValidationError):
        LLMJudgeApprovalJudgment.model_validate(payload)


def test_quote_can_be_empty_when_not_present():
    # quote="" is valid only when present=False; use reject so the
    # cross-field validator doesn't fire on the other elements.
    payload = _good_dict()
    payload["verdict"] = "reject"
    payload["cluster_referenced"] = {"present": False, "quote": ""}
    j = LLMJudgeApprovalJudgment.model_validate(payload)
    assert j.cluster_referenced.present is False
    assert j.cluster_referenced.quote == ""


def test_admit_requires_all_elements_present():
    payload = _good_dict()
    payload["cluster_referenced"] = {"present": False, "quote": ""}
    with pytest.raises(ValidationError):
        LLMJudgeApprovalJudgment.model_validate(payload)


def test_present_true_requires_non_empty_quote():
    from ownevo_kernel.approvers.llm_judge.judgment import StructuralElement
    with pytest.raises(ValidationError):
        StructuralElement.model_validate({"present": True, "quote": ""})
