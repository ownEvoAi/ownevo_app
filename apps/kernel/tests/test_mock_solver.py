"""Tests for `eval_runner/mock_solver.py` — Track 9.0.2 Slice A.

What we pin:

  1. Accuracy contract — for `accuracy_for(N) = a` and `n_cases = n`,
     the observed accuracy equals `round(n × a) / n` exactly. Pinned
     via the fraction-correct count, not via val_score (which depends
     on the metric — that's covered in test_run_with_mock_agent_*).
  2. Determinism — same seed + same iteration_index + same case_set
     produce byte-identical predictions across calls.
  3. Iteration sensitivity — different iteration_index produces a
     different correct-set (otherwise every iteration would have the
     same true/false case partition, defeating the point).
  4. Curve fallback — past the end of accuracy_per_iteration, the
     default kicks in.
  5. Cross-check parity — workflow_spec_id disagreement raises
     ValueError the same way `solve_with_agent` does.
  6. Empty case set is a no-op (matches the real-tier behaviour).

The DB-backed end-to-end test (mock config flowing through
iteration_runner) lives in test_iteration_runner_mock_sim.py (Slice A
integration).
"""

from __future__ import annotations

import pytest
from ownevo_kernel.eval_runner.mock_solver import solve_with_mock_agent
from ownevo_kernel.nl_gen.fixtures import (
    DEMAND_PREDICTION_METRIC,
    DEMAND_PREDICTION_SIM_PLAN,
    DEMAND_PREDICTION_SPEC,
)
from ownevo_kernel.nl_gen.fixtures.eval_case_sets import (
    DEMAND_PREDICTION_EVAL_CASE_SET,
)
from ownevo_kernel.sim_tier import MockSimConfig

# ---------------------------------------------------------------------------
# Accuracy contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("accuracy", [0.0, 0.25, 0.5, 0.75, 1.0])
async def test_accuracy_matches_target_exactly(accuracy: float) -> None:
    case_set = DEMAND_PREDICTION_EVAL_CASE_SET
    n_cases = len(case_set.cases)
    expected_n_correct = round(n_cases * accuracy)

    config = MockSimConfig(default_accuracy=accuracy, seed=42)
    results = await solve_with_mock_agent(
        case_set,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        mock_config=config,
        iteration_index=0,
    )

    n_correct = sum(1 for r in results if r.passed)
    assert n_correct == expected_n_correct, (
        f"target accuracy={accuracy} on {len(case_set.cases)} cases "
        f"expected {expected_n_correct} correct, got {n_correct}"
    )
    assert len(results) == len(case_set.cases)


# ---------------------------------------------------------------------------
# Determinism + iteration sensitivity
# ---------------------------------------------------------------------------


async def test_determinism_same_seed_same_iter_byte_identical() -> None:
    config = MockSimConfig(default_accuracy=0.6, seed=42)
    first = await solve_with_mock_agent(
        DEMAND_PREDICTION_EVAL_CASE_SET,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        mock_config=config,
        iteration_index=3,
    )
    second = await solve_with_mock_agent(
        DEMAND_PREDICTION_EVAL_CASE_SET,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        mock_config=config,
        iteration_index=3,
    )
    assert [(r.case_id, r.actual_value) for r in first] == [
        (r.case_id, r.actual_value) for r in second
    ], "same seed + same iter must produce identical predictions"


async def test_different_iteration_picks_different_correct_set() -> None:
    """Without iteration-sensitivity, every iteration would mark the
    same N cases correct — that defeats the point of a per-iteration
    curve (the loop would never see varying failure clusters)."""
    config = MockSimConfig(default_accuracy=0.5, seed=42)
    iter0 = await solve_with_mock_agent(
        DEMAND_PREDICTION_EVAL_CASE_SET,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        mock_config=config,
        iteration_index=0,
    )
    iter1 = await solve_with_mock_agent(
        DEMAND_PREDICTION_EVAL_CASE_SET,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        mock_config=config,
        iteration_index=1,
    )
    correct0 = {r.case_id for r in iter0 if r.passed}
    correct1 = {r.case_id for r in iter1 if r.passed}
    # Same count (same accuracy), different membership.
    assert len(correct0) == len(correct1)
    assert correct0 != correct1, (
        "different iteration_index must shuffle the correct-set; "
        "iteration determinism comes from the seed XOR iteration_index"
    )


# ---------------------------------------------------------------------------
# Curve fallback
# ---------------------------------------------------------------------------


async def test_default_accuracy_kicks_in_past_curve_end() -> None:
    config = MockSimConfig(
        accuracy_per_iteration=[0.5, 0.7],
        default_accuracy=0.9,
        seed=42,
    )
    n_cases = len(DEMAND_PREDICTION_EVAL_CASE_SET.cases)
    # iter 2 is past the curve → should use default 0.9.
    results = await solve_with_mock_agent(
        DEMAND_PREDICTION_EVAL_CASE_SET,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        mock_config=config,
        iteration_index=2,
    )
    n_correct = sum(1 for r in results if r.passed)
    assert n_correct == round(n_cases * 0.9)


# ---------------------------------------------------------------------------
# Cross-check parity with solve_with_agent
# ---------------------------------------------------------------------------


async def test_mismatched_workflow_spec_id_raises_value_error() -> None:
    """Same shape as solve_with_agent's xref checks — a mis-stitched
    trio should fail loudly on mock tier too, not silently produce
    a meaningless EvalRunReport.

    EvalCaseSet's Pydantic validator enforces min_length=10 + label
    balance + back-pointer agreement, so we can't construct a small
    mismatched fixture directly. Use model_copy(update=...) to
    selectively override workflow_spec_id while keeping everything
    else valid.
    """
    mismatched = DEMAND_PREDICTION_EVAL_CASE_SET.model_copy(
        update={
            "workflow_spec_id": "not-the-real-spec-id",
            "simulation_plan_workflow_id": "not-the-real-spec-id",
        },
    )
    with pytest.raises(ValueError, match="case_set.workflow_spec_id"):
        await solve_with_mock_agent(
            mismatched,
            DEMAND_PREDICTION_SIM_PLAN,
            DEMAND_PREDICTION_SPEC,
            DEMAND_PREDICTION_METRIC,
            mock_config=MockSimConfig(),
            iteration_index=0,
        )


# ---------------------------------------------------------------------------
# Empty case set edge case
# ---------------------------------------------------------------------------


async def test_empty_case_set_returns_empty_list() -> None:
    """The n=0 short-circuit guards against ZeroDivisionError in
    `round(n_cases * target_accuracy)`. EvalCaseSet's validator rejects
    empty inputs, so production never hits this branch — but the guard
    has to stay defensive in case a future refactor relaxes the
    validator. Construct then mutate to bypass the constructor."""
    fixture = DEMAND_PREDICTION_EVAL_CASE_SET.model_copy(deep=True)
    fixture.cases.clear()

    results = await solve_with_mock_agent(
        fixture,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        mock_config=MockSimConfig(),
        iteration_index=0,
    )
    assert results == []


# ---------------------------------------------------------------------------
# Rationale tag
# ---------------------------------------------------------------------------


async def test_rationale_carries_mock_marker() -> None:
    """The trace audit needs to make the mock origin obvious; a
    reviewer reading the case outputs should not mistake a mock
    prediction for a real LLM call."""
    results = await solve_with_mock_agent(
        DEMAND_PREDICTION_EVAL_CASE_SET,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        mock_config=MockSimConfig(),
        iteration_index=0,
    )
    assert all(r.rationale and "[mock]" in r.rationale for r in results)
