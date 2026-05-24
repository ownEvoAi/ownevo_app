"""Tests for `eval_runner.run_with_mock_agent` (Track 9.0.2 Slice A).

The wrapper's job: (a) cross-check the quartet against spec via
`check_against_spec`, (b) delegate prediction to `solve_with_mock_agent`,
(c) pack the results into an `EvalRunReport` with correct is_test_fold
propagation, n_pass/n_total counts, and report.value equal to the mock
config's target accuracy (modulo rounding).

This mirrors `test_eval_runner_run_with_agent.py`'s structure.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.eval_runner import EvalRunReport, run_with_mock_agent
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.nl_gen.fixtures.eval_case_sets import (
    DEMAND_PREDICTION_EVAL_CASE_SET,
)
from ownevo_kernel.sim_tier import MockSimConfig


# ---------------------------------------------------------------------------
# Report shape and accuracy contract
# ---------------------------------------------------------------------------


async def test_run_with_mock_agent_returns_eval_run_report() -> None:
    """The wrapper must return an EvalRunReport — same shape as run_with_agent."""
    case_set = DEMAND_PREDICTION_EVAL_CASE_SET
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]

    config = MockSimConfig(default_accuracy=0.75, seed=42)
    report = await run_with_mock_agent(
        case_set, plan, spec, metric,
        mock_config=config,
        iteration_index=0,
    )

    assert isinstance(report, EvalRunReport)
    assert report.n_total == len(case_set.cases)
    assert 0 <= report.n_pass <= report.n_total


async def test_run_with_mock_agent_value_matches_target_accuracy() -> None:
    """report.value should equal the mock target accuracy (modulo rounding)."""
    case_set = DEMAND_PREDICTION_EVAL_CASE_SET
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]
    n_cases = len(case_set.cases)

    config = MockSimConfig(default_accuracy=0.8, seed=0)
    report = await run_with_mock_agent(
        case_set, plan, spec, metric,
        mock_config=config,
        iteration_index=0,
    )

    expected_n_pass = round(n_cases * 0.8)
    assert report.n_pass == expected_n_pass


# ---------------------------------------------------------------------------
# check_against_spec cross-check parity
# ---------------------------------------------------------------------------


async def test_run_with_mock_agent_raises_on_metric_spec_mismatch() -> None:
    """check_against_spec fires before the mock solver — a mis-stitched
    quartet fails loudly in mock tier the same way it does in real tier."""
    case_set = DEMAND_PREDICTION_EVAL_CASE_SET
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    wrong_metric = METRIC_FIXTURES["credit-risk"]  # different workflow_spec_id

    config = MockSimConfig()
    with pytest.raises(ValueError):
        await run_with_mock_agent(
            case_set, plan, spec, wrong_metric,
            mock_config=config,
            iteration_index=0,
        )


# ---------------------------------------------------------------------------
# is_test_fold propagation
# ---------------------------------------------------------------------------


async def test_run_with_mock_agent_propagates_is_test_fold() -> None:
    """Outcomes must carry the correct is_test_fold flag from the case set."""
    case_set = DEMAND_PREDICTION_EVAL_CASE_SET
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]

    config = MockSimConfig(default_accuracy=1.0, seed=0)  # all correct
    report = await run_with_mock_agent(
        case_set, plan, spec, metric,
        mock_config=config,
        iteration_index=0,
    )

    is_test_fold_by_id = {c.case_id: c.is_test_fold for c in case_set.cases}
    for outcome in report.outcomes:
        assert outcome.is_test_fold == is_test_fold_by_id[outcome.case_id], (
            f"is_test_fold mismatch for case {outcome.case_id}"
        )
