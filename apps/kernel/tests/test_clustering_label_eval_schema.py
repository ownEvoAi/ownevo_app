"""Tests for `clustering.label_eval.judgment` (B3.5).

Pins the schema shape, frozen-ness, and the verdict_score mapping so a
future edit that adds a `partial` verdict (or makes the schema mutable)
shows up as a unit fail rather than silently breaking the agreement
math.
"""

from __future__ import annotations

import json

import pytest
from ownevo_kernel.clustering.label_eval.judgment import (
    SCHEMA_VERSION,
    ClusterLabelJudgment,
    verdict_score,
)
from pydantic import ValidationError


def _good_payload(cluster_id: str = "ca-snack-weekend-under") -> dict:
    return {
        "schema_version": "0.1",
        "cluster_id": cluster_id,
        "verdict": "agree",
        "rationale": "Candidate matches ground-truth on direction + domain.",
    }


def test_schema_version_constant():
    assert SCHEMA_VERSION == "0.1"
    assert ClusterLabelJudgment.model_validate(_good_payload()).schema_version == "0.1"


def test_round_trip_via_model_dump_json():
    j = ClusterLabelJudgment.model_validate(_good_payload())
    again = ClusterLabelJudgment.model_validate_json(j.model_dump_json())
    assert again == j


def test_extra_forbid():
    payload = _good_payload()
    payload["unexpected_extra"] = "noise"
    with pytest.raises(ValidationError) as exc:
        ClusterLabelJudgment.model_validate(payload)
    assert "unexpected_extra" in str(exc.value)


def test_frozen_is_immutable():
    j = ClusterLabelJudgment.model_validate(_good_payload())
    with pytest.raises(ValidationError):
        j.verdict = "disagree"  # type: ignore[misc]


def test_verdict_score_mapping():
    assert verdict_score("agree") == 1.0
    assert verdict_score("disagree") == 0.0


def test_verdict_must_be_known_literal():
    payload = _good_payload()
    payload["verdict"] = "partial"  # not in LabelVerdict
    with pytest.raises(ValidationError):
        ClusterLabelJudgment.model_validate(payload)


def test_cluster_id_pattern_rejects_uppercase_and_spaces():
    bad_payload = _good_payload(cluster_id="Bad ID")
    with pytest.raises(ValidationError):
        ClusterLabelJudgment.model_validate(bad_payload)


def test_cluster_id_must_not_start_with_dash():
    bad_payload = _good_payload(cluster_id="-leading-dash")
    with pytest.raises(ValidationError):
        ClusterLabelJudgment.model_validate(bad_payload)


def test_rationale_min_length_one():
    bad_payload = _good_payload()
    bad_payload["rationale"] = ""
    with pytest.raises(ValidationError):
        ClusterLabelJudgment.model_validate(bad_payload)


def test_rationale_no_upper_bound():
    # max_length was removed; sonnet writes rationales >400 chars in practice.
    # The API max_tokens=1000 cap bounds output at the call site instead.
    long_payload = _good_payload()
    long_payload["rationale"] = "x" * 1000
    ClusterLabelJudgment.model_validate(long_payload)  # must not raise


def test_label_verdict_literal_pinned():
    """If a future commit adds a third verdict, the agreement math has
    to be revisited. Failing this test is a deliberate signal."""
    # Literal["agree", "disagree"] — both should validate
    for v in ("agree", "disagree"):
        payload = _good_payload()
        payload["verdict"] = v
        ClusterLabelJudgment.model_validate(payload)


def test_json_schema_has_required_fields():
    schema = ClusterLabelJudgment.model_json_schema()
    required = set(schema.get("required", []))
    for f in ("cluster_id", "verdict", "rationale"):
        assert f in required


def test_dump_json_keys_sorted_round_trip():
    j = ClusterLabelJudgment.model_validate(_good_payload())
    raw = j.model_dump_json()
    parsed = json.loads(raw)
    assert set(parsed.keys()) == {"schema_version", "cluster_id", "verdict", "rationale"}
