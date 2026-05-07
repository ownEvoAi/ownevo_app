"""Tests for `approvers.judgment` (W5.2).

Pins:
  * `ApprovalJudgment` round-trips identity through model_dump_json.
  * `extra='forbid'` rejects unknown fields (top level + per-check).
  * `frozen=True` rejects mutation.
  * Required-fields contract.
  * Verdict closed-set (`pass` / `fail` only — no `partial`).
  * Pattern + length constraints.
  * `admits` property — admit iff all three checks pass.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.approvers.judgment import (
    SCHEMA_VERSION,
    ApprovalJudgment,
    StructuralCheck,
)
from pydantic import ValidationError


def _good_judgment_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "proposal_id": "00000000-0000-0000-0000-000000000001",
        "references_cluster": {
            "verdict": "pass",
            "rationale": "Cluster `weekend-spike-under-forecast` named verbatim.",
        },
        "names_change": {
            "verdict": "pass",
            "rationale": "Specific change: added is_weekend × snack interaction.",
        },
        "states_direction": {
            "verdict": "pass",
            "rationale": "Says 'reduce RMSSE' on lower-is-better metric — direction OK.",
        },
        "overall_rationale": (
            "All three structural elements present and the stated direction "
            "is consistent with the metric's improvement axis."
        ),
    }


# ---------------------------------------------------------------------------
# Round-trip + schema-version
# ---------------------------------------------------------------------------


def test_round_trip_identity():
    payload = _good_judgment_payload()
    j = ApprovalJudgment.model_validate(payload)
    again = ApprovalJudgment.model_validate_json(j.model_dump_json())
    assert again == j


def test_schema_version_pinned():
    payload = _good_judgment_payload()
    payload["schema_version"] = "9.9"
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


def test_schema_version_constant_value():
    assert SCHEMA_VERSION == "0.1"


# ---------------------------------------------------------------------------
# extra='forbid'
# ---------------------------------------------------------------------------


def test_extra_field_top_level_rejected():
    payload = _good_judgment_payload()
    payload["bonus"] = "claude invented this"
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


def test_extra_field_in_check_rejected():
    payload = _good_judgment_payload()
    payload["references_cluster"]["confidence"] = 0.7
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


# ---------------------------------------------------------------------------
# frozen=True
# ---------------------------------------------------------------------------


def test_frozen_judgment_rejects_mutation():
    j = ApprovalJudgment.model_validate(_good_judgment_payload())
    with pytest.raises(ValidationError):
        j.overall_rationale = "different rationale"  # type: ignore[misc]


def test_frozen_check_rejects_mutation():
    j = ApprovalJudgment.model_validate(_good_judgment_payload())
    with pytest.raises(ValidationError):
        j.references_cluster.verdict = "fail"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "drop_field",
    [
        "proposal_id",
        "references_cluster",
        "names_change",
        "states_direction",
        "overall_rationale",
    ],
)
def test_missing_required_field_rejected(drop_field: str):
    payload = _good_judgment_payload()
    payload.pop(drop_field)
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


@pytest.mark.parametrize("drop_field", ["verdict", "rationale"])
def test_check_missing_required_field_rejected(drop_field: str):
    payload = _good_judgment_payload()
    payload["references_cluster"].pop(drop_field)
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


# ---------------------------------------------------------------------------
# Verdict closed-set — only `pass` / `fail`, no `partial`
# ---------------------------------------------------------------------------


def test_invalid_check_verdict_rejected():
    payload = _good_judgment_payload()
    payload["references_cluster"]["verdict"] = "MOSTLY_OK"
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


def test_partial_verdict_rejected():
    """The W5.2 schema is binary — `partial` is not a valid verdict."""
    payload = _good_judgment_payload()
    payload["references_cluster"]["verdict"] = "partial"
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


# ---------------------------------------------------------------------------
# Length constraints
# ---------------------------------------------------------------------------


def test_empty_proposal_id_rejected():
    payload = _good_judgment_payload()
    payload["proposal_id"] = ""
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


def test_overlong_proposal_id_rejected():
    payload = _good_judgment_payload()
    payload["proposal_id"] = "x" * 129  # max_length=128
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


def test_empty_rationale_rejected():
    payload = _good_judgment_payload()
    payload["references_cluster"]["rationale"] = ""
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


def test_overlong_check_rationale_rejected():
    payload = _good_judgment_payload()
    payload["references_cluster"]["rationale"] = "x" * 401  # max_length=400
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


def test_overlong_overall_rationale_rejected():
    payload = _good_judgment_payload()
    payload["overall_rationale"] = "x" * 601  # max_length=600
    with pytest.raises(ValidationError):
        ApprovalJudgment.model_validate(payload)


# ---------------------------------------------------------------------------
# admits property — mechanical rule
# ---------------------------------------------------------------------------


def test_admits_all_three_pass():
    payload = _good_judgment_payload()
    j = ApprovalJudgment.model_validate(payload)
    assert j.admits is True


@pytest.mark.parametrize(
    "fail_check",
    ["references_cluster", "names_change", "states_direction"],
)
def test_admits_false_when_any_check_fails(fail_check: str):
    payload = _good_judgment_payload()
    payload[fail_check]["verdict"] = "fail"
    j = ApprovalJudgment.model_validate(payload)
    assert j.admits is False


def test_admits_false_when_all_three_fail():
    payload = _good_judgment_payload()
    for c in ("references_cluster", "names_change", "states_direction"):
        payload[c]["verdict"] = "fail"
    j = ApprovalJudgment.model_validate(payload)
    assert j.admits is False


# ---------------------------------------------------------------------------
# StructuralCheck standalone
# ---------------------------------------------------------------------------


def test_structural_check_standalone():
    """StructuralCheck is usable on its own (no parent judgment required)."""
    c = StructuralCheck.model_validate(
        {"verdict": "fail", "rationale": "No cluster reference."}
    )
    assert c.verdict == "fail"
    assert c.rationale == "No cluster reference."
