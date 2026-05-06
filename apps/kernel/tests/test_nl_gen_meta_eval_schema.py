"""Tests for `nl_gen.meta_eval.judgment` (A4.6).

Pins:
  * `MetaEvalJudgment` round-trips identity through model_dump_json.
  * `extra='forbid'` rejects unknown fields (both at the top level and
    inside nested `MetaEvalDimension`).
  * `frozen=True` rejects mutation.
  * Required-fields contract.
  * `dimension_score` mapping pass=1.0, partial=0.5, fail=0.0.
  * `aggregate_score` arithmetic.
  * Pattern + length constraints on `workflow_spec_id` and rationales.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ownevo_kernel.nl_gen.meta_eval.judgment import (
    SCHEMA_VERSION,
    MetaEvalDimension,
    MetaEvalJudgment,
    dimension_score,
)


def _good_judgment_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_spec_id": "demand-prediction",
        "sim_coverage": {
            "verdict": "pass",
            "rationale": "All entities (SKUs, stores, weeks) appear in the sim.",
        },
        "eval_case_coverage": {
            "verdict": "partial",
            "rationale": "Past-miss 'winter footwear' covered; 'cold-weather spike' is not.",
        },
        "metric_alignment": {
            "verdict": "pass",
            "rationale": "Recall family matches 'we missed the spike' past-miss.",
        },
        "overall_verdict": "good",
        "overall_rationale": (
            "Bundle is mostly aligned with the description; one past-miss is "
            "missed in the eval set but the metric and sim cover the load-bearing "
            "behaviors. Safe to feed to the agent loop."
        ),
    }


# ---------------------------------------------------------------------------
# Round-trip + schema-version
# ---------------------------------------------------------------------------


def test_round_trip_identity():
    payload = _good_judgment_payload()
    j = MetaEvalJudgment.model_validate(payload)
    again = MetaEvalJudgment.model_validate_json(j.model_dump_json())
    assert again == j


def test_schema_version_pinned():
    payload = _good_judgment_payload()
    payload["schema_version"] = "9.9"
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


def test_schema_version_constant_value():
    assert SCHEMA_VERSION == "0.1"


# ---------------------------------------------------------------------------
# extra='forbid'
# ---------------------------------------------------------------------------


def test_extra_field_top_level_rejected():
    payload = _good_judgment_payload()
    payload["bonus"] = "claude invented this"
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


def test_extra_field_in_dimension_rejected():
    payload = _good_judgment_payload()
    payload["sim_coverage"]["confidence"] = 0.7
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


# ---------------------------------------------------------------------------
# frozen=True
# ---------------------------------------------------------------------------


def test_frozen_judgment_rejects_mutation():
    j = MetaEvalJudgment.model_validate(_good_judgment_payload())
    with pytest.raises(ValidationError):
        j.overall_verdict = "bad"  # type: ignore[misc]


def test_frozen_dimension_rejects_mutation():
    j = MetaEvalJudgment.model_validate(_good_judgment_payload())
    with pytest.raises(ValidationError):
        j.sim_coverage.verdict = "fail"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "drop_field",
    [
        "workflow_spec_id",
        "sim_coverage",
        "eval_case_coverage",
        "metric_alignment",
        "overall_verdict",
        "overall_rationale",
    ],
)
def test_missing_required_field_rejected(drop_field: str):
    payload = _good_judgment_payload()
    payload.pop(drop_field)
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


@pytest.mark.parametrize("drop_field", ["verdict", "rationale"])
def test_dimension_missing_required_field_rejected(drop_field: str):
    payload = _good_judgment_payload()
    payload["sim_coverage"].pop(drop_field)
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


# ---------------------------------------------------------------------------
# Verdict closed-set
# ---------------------------------------------------------------------------


def test_invalid_dimension_verdict_rejected():
    payload = _good_judgment_payload()
    payload["sim_coverage"]["verdict"] = "MOSTLY_OK"
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


def test_invalid_overall_verdict_rejected():
    payload = _good_judgment_payload()
    payload["overall_verdict"] = "indifferent"
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


# ---------------------------------------------------------------------------
# Pattern / length
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["", "Has-Capitals", "trailing-", "-leading", "under_score"],
)
def test_workflow_spec_id_pattern_rejects_bad_ids(bad_id: str):
    payload = _good_judgment_payload()
    payload["workflow_spec_id"] = bad_id
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


def test_empty_rationale_rejected():
    payload = _good_judgment_payload()
    payload["sim_coverage"]["rationale"] = ""
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


def test_overlong_dimension_rationale_rejected():
    payload = _good_judgment_payload()
    payload["sim_coverage"]["rationale"] = "x" * 601  # max_length=600
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


def test_overlong_overall_rationale_rejected():
    payload = _good_judgment_payload()
    payload["overall_rationale"] = "x" * 801  # max_length=800
    with pytest.raises(ValidationError):
        MetaEvalJudgment.model_validate(payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_dimension_score_mapping():
    assert dimension_score("pass") == 1.0
    assert dimension_score("partial") == 0.5
    assert dimension_score("fail") == 0.0


def test_aggregate_score_all_pass_is_one():
    payload = _good_judgment_payload()
    for d in ("sim_coverage", "eval_case_coverage", "metric_alignment"):
        payload[d]["verdict"] = "pass"
    j = MetaEvalJudgment.model_validate(payload)
    assert j.aggregate_score() == pytest.approx(1.0)


def test_aggregate_score_all_fail_is_zero():
    payload = _good_judgment_payload()
    for d in ("sim_coverage", "eval_case_coverage", "metric_alignment"):
        payload[d]["verdict"] = "fail"
    j = MetaEvalJudgment.model_validate(payload)
    assert j.aggregate_score() == pytest.approx(0.0)


def test_aggregate_score_mixed():
    """pass + partial + fail = (1.0 + 0.5 + 0.0) / 3 = 0.5."""
    payload = _good_judgment_payload()
    payload["sim_coverage"]["verdict"] = "pass"
    payload["eval_case_coverage"]["verdict"] = "partial"
    payload["metric_alignment"]["verdict"] = "fail"
    j = MetaEvalJudgment.model_validate(payload)
    assert j.aggregate_score() == pytest.approx(0.5)


def test_meta_eval_dimension_standalone():
    """MetaEvalDimension is usable on its own (no parent judgment required)."""
    d = MetaEvalDimension.model_validate(
        {"verdict": "partial", "rationale": "Half-covered."}
    )
    assert d.verdict == "partial"
    assert d.rationale == "Half-covered."
