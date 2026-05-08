"""Invariant tests for the W5.5 preview judgment fixtures.

The preview judgments are hand-authored stand-ins for live judge calls
on the production NL-gen fixtures. These tests pin:

  * One judgment per workflow_id (keys match `FIXTURES`).
  * `workflow_spec_id` on each judgment matches its dict key.
  * Every judgment passes the `MetaEvalJudgment` schema (validated at
    construction time, but pinned here so a future schema bump
    doesn't silently break the preview API).
  * `aggregate_score()` reflects the per-dimension verdicts.
  * Every judgment is `overall_verdict == "good"` — the production
    fixtures are known-good bundles, so a `bad` slipping in here
    means the fixture was edited without the judgment being updated.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.nl_gen.fixtures import FIXTURES
from ownevo_kernel.nl_gen.meta_eval import (
    PREVIEW_JUDGMENT_FIXTURES,
    MetaEvalJudgment,
)


def test_one_preview_judgment_per_fixture():
    assert set(PREVIEW_JUDGMENT_FIXTURES.keys()) == set(FIXTURES.keys())


@pytest.mark.parametrize("workflow_id", sorted(PREVIEW_JUDGMENT_FIXTURES))
def test_workflow_spec_id_matches_dict_key(workflow_id: str):
    judgment = PREVIEW_JUDGMENT_FIXTURES[workflow_id]
    assert judgment.workflow_spec_id == workflow_id


@pytest.mark.parametrize("workflow_id", sorted(PREVIEW_JUDGMENT_FIXTURES))
def test_judgment_round_trips_through_schema(workflow_id: str):
    judgment = PREVIEW_JUDGMENT_FIXTURES[workflow_id]
    blob = judgment.model_dump_json()
    restored = MetaEvalJudgment.model_validate_json(blob)
    assert restored == judgment


@pytest.mark.parametrize("workflow_id", sorted(PREVIEW_JUDGMENT_FIXTURES))
def test_aggregate_score_in_range(workflow_id: str):
    score = PREVIEW_JUDGMENT_FIXTURES[workflow_id].aggregate_score()
    assert 0.0 <= score <= 1.0


@pytest.mark.parametrize("workflow_id", sorted(PREVIEW_JUDGMENT_FIXTURES))
def test_overall_verdict_is_good(workflow_id: str):
    """Production fixtures are known-good. A `bad` here means the
    fixture changed without the judgment catching up."""
    assert (
        PREVIEW_JUDGMENT_FIXTURES[workflow_id].overall_verdict == "good"
    )


def test_demand_prediction_all_pass_aggregate_one():
    """Spot-check the calibration anchor: demand-prediction is the
    canonical good bundle. All-pass should aggregate to 1.0."""
    judgment = PREVIEW_JUDGMENT_FIXTURES["demand-prediction"]
    assert judgment.sim_coverage.verdict == "pass"
    assert judgment.eval_case_coverage.verdict == "pass"
    assert judgment.metric_alignment.verdict == "pass"
    assert judgment.aggregate_score() == pytest.approx(1.0)


def test_credit_risk_partial_eval_aggregates_below_one():
    """credit-risk has a deliberate partial verdict on eval coverage so
    the badge UI has a non-trivial mid-state to render — pin it."""
    judgment = PREVIEW_JUDGMENT_FIXTURES["credit-risk"]
    assert judgment.eval_case_coverage.verdict == "partial"
    assert judgment.aggregate_score() == pytest.approx((1.0 + 0.5 + 1.0) / 3.0)
