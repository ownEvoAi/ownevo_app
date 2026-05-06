"""Tests for `eval_runner.run_with_agent` (A4.4).

Mocks `solve_with_agent` to bypass the LLM — the orchestrator's job
is to (a) cross-check the trio + metric, (b) route results into
compute_metric, (c) pack EvalRunReport identical-shape to run_replay.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.eval_runner import EvalRunReport, run_with_agent
from ownevo_kernel.nl_gen.eval_replay import ReplayResult
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)


def _perfect_results(case_set):
    return [
        ReplayResult(
            case_id=c.case_id,
            passed=True,
            actual_value=c.expected_value,
            expected_value=c.expected_value,
        )
        for c in case_set.cases
    ]


def _all_wrong_results(case_set):
    return [
        ReplayResult(
            case_id=c.case_id,
            passed=False,
            actual_value=not c.expected_value,
            expected_value=c.expected_value,
        )
        for c in case_set.cases
    ]


class _StubClient:
    """Sentinel — solve_with_agent is monkeypatched so this is never hit."""


# ---------------------------------------------------------------------------
# Happy path × 3 fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
async def test_run_with_agent_perfect_predictions_meets_target(
    workflow_id, monkeypatch
):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    async def _stub_solve(client, cs, pl, sp, *, model, max_tokens):
        # Cross-checks already happened on the wrapper; the stub mirrors
        # the contract solve_with_agent satisfies.
        return _perfect_results(cs)

    monkeypatch.setattr(
        "ownevo_kernel.eval_runner.agent_solver.solve_with_agent", _stub_solve
    )

    report = await run_with_agent(
        case_set, plan, spec, metric, client=_StubClient()
    )
    assert isinstance(report, EvalRunReport)
    assert report.value == pytest.approx(1.0)
    assert report.meets_target is True
    assert report.degenerate is False


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
async def test_run_with_agent_all_wrong_misses_target(workflow_id, monkeypatch):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    async def _stub_solve(client, cs, pl, sp, *, model, max_tokens):
        return _all_wrong_results(cs)

    monkeypatch.setattr(
        "ownevo_kernel.eval_runner.agent_solver.solve_with_agent", _stub_solve
    )

    report = await run_with_agent(
        case_set, plan, spec, metric, client=_StubClient()
    )
    assert report.meets_target is False
    # All wrong → for these families, value collapses to 0.0
    assert report.value == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Outcomes carry agent values + is_test_fold
# ---------------------------------------------------------------------------


async def test_outcomes_carry_agent_actual_values(monkeypatch):
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    async def _stub_solve(client, cs, pl, sp, *, model, max_tokens):
        # Always predict True regardless.
        return [
            ReplayResult(
                case_id=c.case_id,
                passed=(c.expected_value is True),
                actual_value=True,
                expected_value=c.expected_value,
            )
            for c in cs.cases
        ]

    monkeypatch.setattr(
        "ownevo_kernel.eval_runner.agent_solver.solve_with_agent", _stub_solve
    )

    report = await run_with_agent(
        case_set, plan, spec, metric, client=_StubClient()
    )
    for outcome in report.outcomes:
        assert outcome.actual_value is True


async def test_is_test_fold_propagates_through_run_with_agent(monkeypatch):
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    async def _stub_solve(client, cs, pl, sp, *, model, max_tokens):
        return _perfect_results(cs)

    monkeypatch.setattr(
        "ownevo_kernel.eval_runner.agent_solver.solve_with_agent", _stub_solve
    )

    report = await run_with_agent(
        case_set, plan, spec, metric, client=_StubClient()
    )
    by_id = {o.case_id: o.is_test_fold for o in report.outcomes}
    for c in case_set.cases:
        assert by_id[c.case_id] == c.is_test_fold


# ---------------------------------------------------------------------------
# Cross-check failures fire BEFORE any solver call
# ---------------------------------------------------------------------------


async def test_metric_workflow_id_mismatch_raises_before_solver(monkeypatch):
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["credit-risk"]

    called = {"yes": False}

    async def _stub_solve(client, cs, pl, sp, *, model, max_tokens):
        called["yes"] = True
        return _perfect_results(cs)

    monkeypatch.setattr(
        "ownevo_kernel.eval_runner.agent_solver.solve_with_agent", _stub_solve
    )

    with pytest.raises(ValueError, match="workflow_spec_id"):
        await run_with_agent(
            case_set, plan, spec, metric, client=_StubClient()
        )
    assert called["yes"] is False


async def test_metric_direction_mismatch_raises_before_solver(monkeypatch):
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"].model_copy(
        update={"direction": "minimize"}
    )

    called = {"yes": False}

    async def _stub_solve(client, cs, pl, sp, *, model, max_tokens):
        called["yes"] = True
        return _perfect_results(cs)

    monkeypatch.setattr(
        "ownevo_kernel.eval_runner.agent_solver.solve_with_agent", _stub_solve
    )

    with pytest.raises(ValueError, match="direction"):
        await run_with_agent(
            case_set, plan, spec, metric, client=_StubClient()
        )
    assert called["yes"] is False
