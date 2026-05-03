"""BenchmarkResult + SyntheticBenchmarkRunner tests — pure unit, no DB.

Pins the contract the gate (W2.2) and gate self-test (W2.2a) consume:

  * val_score = mean reward, None counts as 0.0 (denominator is total
    tasks, not non-None — agent can't game by causing dropouts)
  * Protocol shape: `await runner.run(task_ids=)` returns BenchmarkResult
  * Determinism: same skill + same tasks → identical result
  * Skill exceptions score as 0.0 (definite failure, not missing data)
"""

from __future__ import annotations

import pytest
from ownevo_kernel.benchmark import (
    BenchmarkResult,
    BenchmarkRunner,
    SyntheticBenchmarkRunner,
    SyntheticTask,
)

# ---------------------------------------------------------------------------
# BenchmarkResult contract
# ---------------------------------------------------------------------------


def test_val_score_empty_is_zero():
    assert BenchmarkResult().val_score == 0.0


def test_val_score_arithmetic_mean():
    r = BenchmarkResult(rewards={"a": 1.0, "b": 0.5, "c": 0.0})
    assert r.val_score == pytest.approx(0.5)


def test_val_score_treats_none_as_zero():
    """None is a no-result — counted as 0.0 in the denominator. The agent
    can't game val_score by causing dropouts."""
    r = BenchmarkResult(rewards={"a": 1.0, "b": None, "c": 1.0})
    assert r.val_score == pytest.approx(2 / 3)


def test_n_passed_counts_full_credit():
    r = BenchmarkResult(rewards={"a": 1.0, "b": 0.99, "c": 1.0, "d": None})
    assert r.n_passed == 2


def test_n_no_result_counts_none_only():
    r = BenchmarkResult(rewards={"a": 1.0, "b": None, "c": 0.0, "d": None})
    assert r.n_no_result == 2


def test_n_tasks_total():
    r = BenchmarkResult(rewards={"a": 1.0, "b": None, "c": 0.0})
    assert r.n_tasks == 3


# ---------------------------------------------------------------------------
# SyntheticBenchmarkRunner — happy path
# ---------------------------------------------------------------------------


async def test_runs_all_tasks_when_task_ids_none():
    tasks = tuple(
        SyntheticTask(id=str(i), input=i, expected=i * 2) for i in range(3)
    )

    def doubler(x: int) -> int:
        return x * 2

    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=doubler)
    result = await runner.run()
    assert result.n_tasks == 3
    assert result.val_score == 1.0
    assert all(r == 1.0 for r in result.rewards.values())


async def test_runs_only_specified_subset():
    """Subset runs are how the gate's regression-suite step works."""
    tasks = tuple(
        SyntheticTask(id=str(i), input=i, expected=i * 2) for i in range(5)
    )
    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=lambda x: x * 2)
    result = await runner.run(task_ids=["0", "2"])
    assert list(result.rewards.keys()) == ["0", "2"]
    assert result.n_tasks == 2


async def test_failing_skill_scores_zero():
    tasks = (SyntheticTask(id="t", input=5, expected=10),)
    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=lambda x: x + 1)
    result = await runner.run()
    assert result.rewards["t"] == 0.0
    assert result.val_score == 0.0


async def test_partial_pass():
    tasks = tuple(
        SyntheticTask(id=str(i), input=i, expected=i * 2) for i in range(4)
    )

    # Skill that's right for evens, wrong for odds.
    def half_right(x: int) -> int:
        return x * 2 if x % 2 == 0 else x

    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=half_right)
    result = await runner.run()
    assert result.val_score == pytest.approx(0.5)
    assert result.rewards["0"] == 1.0
    assert result.rewards["1"] == 0.0
    assert result.rewards["2"] == 1.0
    assert result.rewards["3"] == 0.0


# ---------------------------------------------------------------------------
# Skill exceptions
# ---------------------------------------------------------------------------


async def test_skill_exception_scores_zero():
    """Uncaught exception in the skill is a definite failure (0.0), not
    a missing measurement (None). Mirrors real-benchmark semantics."""
    tasks = (SyntheticTask(id="t", input=0, expected=1),)

    def boom(x: int) -> int:
        raise RuntimeError("intentional")

    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=boom)
    result = await runner.run()
    assert result.rewards["t"] == 0.0


async def test_skill_systemexit_also_scores_zero():
    """sys.exit / SystemExit / KeyboardInterrupt — all caught."""
    tasks = (SyntheticTask(id="t", input=0, expected=1),)

    def bail(x: int) -> int:
        raise SystemExit(2)

    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=bail)
    result = await runner.run()
    assert result.rewards["t"] == 0.0


# ---------------------------------------------------------------------------
# Custom judge
# ---------------------------------------------------------------------------


async def test_custom_judge_for_tolerance():
    """Numeric tolerance: outputs within epsilon score full credit."""
    def almost(out: float, exp: float) -> bool:
        return abs(out - exp) < 0.01

    tasks = (
        SyntheticTask(id="close", input=0, expected=1.0, judge=almost),
        SyntheticTask(id="far", input=0, expected=1.0, judge=almost),
    )

    def jittery(_: int) -> float:
        # First call returns 1.005 (within tol), second returns 1.5 (out).
        # Use a closure for state.
        ...

    # Easier: use a stateful skill via a nonlocal counter.
    counter = {"n": 0}

    def skill(x: int) -> float:
        counter["n"] += 1
        return 1.005 if counter["n"] == 1 else 1.5

    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=skill)
    result = await runner.run()
    assert result.rewards["close"] == 1.0
    assert result.rewards["far"] == 0.0


# ---------------------------------------------------------------------------
# Determinism + invariants
# ---------------------------------------------------------------------------


async def test_run_is_deterministic():
    """Same skill + same tasks → byte-identical result. Required for
    gate reproducibility."""
    tasks = tuple(
        SyntheticTask(id=str(i), input=i, expected=i * 2) for i in range(5)
    )
    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=lambda x: x * 2)
    r1 = await runner.run()
    r2 = await runner.run()
    assert r1 == r2


async def test_unknown_task_id_raises():
    tasks = (SyntheticTask(id="exists", input=0, expected=0),)
    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=lambda x: x)
    with pytest.raises(KeyError, match="ghost"):
        await runner.run(task_ids=["exists", "ghost"])


def test_duplicate_task_ids_rejected_at_construction():
    with pytest.raises(ValueError, match="Duplicate task IDs"):
        SyntheticBenchmarkRunner(
            tasks=(
                SyntheticTask(id="dup", input=0, expected=0),
                SyntheticTask(id="dup", input=1, expected=1),
            ),
            skill=lambda x: x,
        )


def test_runner_satisfies_protocol():
    """Runtime check that SyntheticBenchmarkRunner is structurally a
    BenchmarkRunner. Fails loudly if either side drifts."""
    runner = SyntheticBenchmarkRunner(
        tasks=(SyntheticTask(id="t", input=0, expected=0),),
        skill=lambda x: x,
    )
    assert isinstance(runner, BenchmarkRunner)
