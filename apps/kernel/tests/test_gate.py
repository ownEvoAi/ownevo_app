"""run_gate — pure unit tests over SyntheticBenchmarkRunner.

The gate is a pure async function over `BenchmarkRunner`; these tests
pin every branch of the 3-step contract without hitting Docker, the
DB, or an LLM.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.benchmark import (
    BenchmarkResult,
    SyntheticBenchmarkRunner,
    SyntheticTask,
)
from ownevo_kernel.gate import GateDecision, run_gate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doubler_tasks(n: int) -> tuple[SyntheticTask, ...]:
    return tuple(
        SyntheticTask(id=str(i), input=i, expected=i * 2) for i in range(n)
    )


# ---------------------------------------------------------------------------
# Bootstrap (Day-1) — empty prior suite + best_ever=None
# ---------------------------------------------------------------------------


async def test_bootstrap_passes_with_perfect_skill():
    """Day-1 baseline: no prior cases, no best-ever. Any successful
    run becomes the new baseline (PASS)."""
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(3), skill=lambda x: x * 2)
    result = await run_gate(runner)
    assert result.decision == GateDecision.PASS
    assert result.passed
    assert result.val_score == 1.0
    assert result.best_ever_score_before is None
    assert result.best_ever_score_after == 1.0


async def test_bootstrap_passes_even_with_imperfect_skill():
    """Bootstrap rule per PLAN W2.2: only val-score-must-beat-best-ever
    applies on Day 1, and best_ever=None skips even that. Even a partial
    skill seeds the baseline — the day-1 baseline pipeline is responsible
    for not handing the gate a zero baseline."""
    runner = SyntheticBenchmarkRunner(
        tasks=_doubler_tasks(4),
        skill=lambda x: x * 2 if x % 2 == 0 else x,
    )
    result = await run_gate(runner)
    assert result.decision == GateDecision.PASS
    assert result.val_score == pytest.approx(0.5)
    assert result.best_ever_score_after == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Step 1 — prior-suite regression check
# ---------------------------------------------------------------------------


async def test_step1_blocks_when_prior_task_breaks():
    """The classic regression: candidate improves overall but breaks
    a previously-passing case. Gate must reject."""
    runner = SyntheticBenchmarkRunner(
        tasks=_doubler_tasks(4),
        # Task "0" was passing before; this skill breaks it (returns 99).
        # Tasks 1-3 still pass.
        skill=lambda x: 99 if x == 0 else x * 2,
    )
    result = await run_gate(
        runner,
        prior_eval_task_ids=["0"],  # task "0" was promoted to the suite
        best_ever_score=0.5,
    )
    assert result.decision == GateDecision.FAIL_REGRESSION
    assert not result.passed
    assert result.failed_prior_task_ids == ("0",)
    assert result.best_ever_score_before == 0.5
    assert result.best_ever_score_after == 0.5  # never advances on rejection


async def test_step1_passes_when_all_prior_still_pass():
    """Prior tasks all still passing AND val_score improves → PASS."""
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(4), skill=lambda x: x * 2)
    result = await run_gate(
        runner,
        prior_eval_task_ids=["0", "1"],
        best_ever_score=0.5,
    )
    assert result.decision == GateDecision.PASS
    assert result.failed_prior_task_ids == ()


async def test_step1_skipped_when_prior_suite_empty():
    """Empty prior suite is the bootstrap-rule case from PLAN W2.2:
    step 1 is skipped, only step 2 (improvement) gates."""
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(2), skill=lambda x: x * 2)
    result = await run_gate(runner, best_ever_score=0.5)
    assert result.decision == GateDecision.PASS


async def test_step1_missing_prior_task_counts_as_failure():
    """If a previously-promoted task is no longer in the runner's full
    set, the gate rejects conservatively — the protective contract
    is broken."""
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(2), skill=lambda x: x * 2)
    result = await run_gate(runner, prior_eval_task_ids=["0", "ghost"])
    assert result.decision == GateDecision.FAIL_REGRESSION
    assert "ghost" in result.failed_prior_task_ids


async def test_step1_lists_failed_tasks_in_order():
    """The rationale should be human-readable; failed task IDs
    preserve `prior_eval_task_ids` order so the rejection message
    reads predictably."""
    runner = SyntheticBenchmarkRunner(
        tasks=_doubler_tasks(4),
        skill=lambda x: 99,  # everything fails
    )
    result = await run_gate(runner, prior_eval_task_ids=["2", "0", "1"])
    assert result.decision == GateDecision.FAIL_REGRESSION
    assert result.failed_prior_task_ids == ("2", "0", "1")


# ---------------------------------------------------------------------------
# Step 2 — improvement check
# ---------------------------------------------------------------------------


async def test_step2_blocks_when_val_score_does_not_improve():
    """Same val_score as best-ever → FAIL_NO_IMPROVEMENT (strict beat)."""
    runner = SyntheticBenchmarkRunner(
        tasks=_doubler_tasks(4),
        skill=lambda x: x * 2 if x < 2 else x,  # 0.5 val_score
    )
    result = await run_gate(runner, best_ever_score=0.5)
    assert result.decision == GateDecision.FAIL_NO_IMPROVEMENT
    assert result.val_score == pytest.approx(0.5)
    assert result.best_ever_score_after == 0.5


async def test_step2_blocks_when_val_score_regresses():
    # Start inputs at 1 so `lambda x: x` is wrong on every task
    # (input=0 → expected=0, which would coincidentally match).
    tasks = tuple(
        SyntheticTask(id=str(i), input=i, expected=i * 2) for i in range(1, 5)
    )
    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=lambda x: x)
    result = await run_gate(runner, best_ever_score=0.5)
    assert result.decision == GateDecision.FAIL_NO_IMPROVEMENT
    assert result.val_score == 0.0
    assert result.best_ever_score_after == 0.5


async def test_step2_passes_when_val_score_strictly_better():
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(4), skill=lambda x: x * 2)
    result = await run_gate(runner, best_ever_score=0.5)
    assert result.decision == GateDecision.PASS
    assert result.val_score == 1.0
    assert result.best_ever_score_after == 1.0


async def test_step2_improvement_epsilon_blocks_marginal_gains():
    """epsilon=0.05 means a +0.04 lift is treated as noise, not progress."""
    runner = SyntheticBenchmarkRunner(
        tasks=_doubler_tasks(100),
        skill=lambda x: x * 2 if x < 54 else x,  # val_score = 0.54
    )
    result = await run_gate(runner, best_ever_score=0.5, improvement_epsilon=0.05)
    assert result.decision == GateDecision.FAIL_NO_IMPROVEMENT


async def test_step2_improvement_epsilon_admits_real_gains():
    runner = SyntheticBenchmarkRunner(
        tasks=_doubler_tasks(100),
        skill=lambda x: x * 2 if x < 60 else x,  # val_score = 0.60
    )
    result = await run_gate(runner, best_ever_score=0.5, improvement_epsilon=0.05)
    assert result.decision == GateDecision.PASS


# ---------------------------------------------------------------------------
# regression_tolerance
# ---------------------------------------------------------------------------


async def test_regression_tolerance_admits_partial_credit():
    """A custom judge that returns partial credit (0.95) — with
    regression_tolerance=0.1 the gate accepts; with 0.0 it rejects."""

    # SyntheticBenchmarkRunner only emits 0.0 or 1.0; emulate partial
    # credit by mixing pass/fail and using regression_tolerance to
    # control acceptance — but here we test directly with a fresh
    # custom runner that yields 0.95 via a one-off scoring stub.
    class _PartialRunner:
        async def run(self, task_ids=None):
            return BenchmarkResult(rewards={"a": 0.95, "b": 1.0})

    # Strict: 0.95 < 1.0 threshold → fail
    strict = await run_gate(_PartialRunner(), prior_eval_task_ids=["a"])
    assert strict.decision == GateDecision.FAIL_REGRESSION

    # tolerant: 0.95 >= 1.0 - 0.1 = 0.9 → pass step 1
    tolerant = await run_gate(
        _PartialRunner(),
        prior_eval_task_ids=["a"],
        regression_tolerance=0.1,
    )
    assert tolerant.decision == GateDecision.PASS


async def test_regression_tolerance_validated():
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(1), skill=lambda x: x * 2)
    with pytest.raises(ValueError, match="regression_tolerance"):
        await run_gate(runner, regression_tolerance=1.5)
    with pytest.raises(ValueError, match="regression_tolerance"):
        await run_gate(runner, regression_tolerance=-0.1)


async def test_improvement_epsilon_validated():
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(1), skill=lambda x: x * 2)
    with pytest.raises(ValueError, match="improvement_epsilon"):
        await run_gate(runner, improvement_epsilon=-0.01)
    with pytest.raises(ValueError, match="improvement_epsilon"):
        await run_gate(runner, improvement_epsilon=float("nan"))
    with pytest.raises(ValueError, match="improvement_epsilon"):
        await run_gate(runner, improvement_epsilon=float("inf"))


async def test_best_ever_score_validated():
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(1), skill=lambda x: x * 2)
    with pytest.raises(ValueError, match="best_ever_score"):
        await run_gate(runner, best_ever_score=float("nan"))
    with pytest.raises(ValueError, match="best_ever_score"):
        await run_gate(runner, best_ever_score=float("inf"))
    with pytest.raises(ValueError, match="best_ever_score"):
        await run_gate(runner, best_ever_score=float("-inf"))


# ---------------------------------------------------------------------------
# Step 3 — newly-passing tasks become promotable
# ---------------------------------------------------------------------------


async def test_step3_promotes_passing_tasks_not_in_prior():
    """Tasks that pass under the candidate AND aren't already in the
    prior suite become promotable. The caller (W3 wiring) writes them
    to eval_cases."""
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(5), skill=lambda x: x * 2)
    result = await run_gate(
        runner,
        prior_eval_task_ids=["0"],  # only task 0 was promoted before
        best_ever_score=0.2,
    )
    assert result.decision == GateDecision.PASS
    # 1, 2, 3, 4 newly passing → promotable.
    assert set(result.promotable_task_ids) == {"1", "2", "3", "4"}


async def test_step3_no_promotion_on_failure():
    """Promotion happens only on PASS — rejected gates surface no
    promotable task IDs."""
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(3), skill=lambda x: 99)
    result = await run_gate(runner, prior_eval_task_ids=["0"])
    assert result.decision == GateDecision.FAIL_REGRESSION
    assert result.promotable_task_ids == ()


async def test_step3_skips_failed_tasks_in_promotion():
    """Only tasks at threshold are promotable — failures stay candidate."""
    runner = SyntheticBenchmarkRunner(
        tasks=_doubler_tasks(4),
        skill=lambda x: x * 2 if x < 2 else x,  # 0,1 pass; 2,3 fail
    )
    result = await run_gate(runner)
    assert result.decision == GateDecision.PASS
    assert set(result.promotable_task_ids) == {"0", "1"}


# ---------------------------------------------------------------------------
# Sandbox-error short-circuit (D3)
# ---------------------------------------------------------------------------


async def test_sandbox_error_short_circuits_with_none_rewards():
    """Any None reward poisons the gate — D3 forbids advancing
    best-ever under sandbox errors."""

    class _FlakyRunner:
        async def run(self, task_ids=None):
            return BenchmarkResult(rewards={"a": 1.0, "b": None, "c": 1.0})

    result = await run_gate(_FlakyRunner(), best_ever_score=0.0)
    assert result.decision == GateDecision.SANDBOX_ERROR
    assert not result.passed
    assert result.val_score is None  # don't trust val_score under errors
    assert result.best_ever_score_after == 0.0  # never advances


async def test_sandbox_error_overrides_step1_check():
    """Even if step 1 would fail anyway, sandbox-error takes precedence
    — the gate's surfaced cause should be the actual problem."""

    class _FlakyRunner:
        async def run(self, task_ids=None):
            return BenchmarkResult(rewards={"prior_task": None, "other": 1.0})

    result = await run_gate(_FlakyRunner(), prior_eval_task_ids=["prior_task"])
    assert result.decision == GateDecision.SANDBOX_ERROR
    assert result.failed_prior_task_ids == ()


# ---------------------------------------------------------------------------
# Composite scenario from PLAN.md validation block
# ---------------------------------------------------------------------------


async def test_change_that_improves_new_but_breaks_old_is_rejected():
    """PLAN.md W2.2 validation: 'write change that improves on new case
    but breaks an old case → gate rejects'."""
    tasks = (
        SyntheticTask(id="old_passing", input=10, expected=20),
        SyntheticTask(id="new_failing_now_fixed", input=5, expected=42),
    )

    # Candidate: returns 42 for input=5 (fixes new), returns 99 for
    # input=10 (breaks old).
    def candidate(x: int) -> int:
        return 42 if x == 5 else 99

    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=candidate)
    result = await run_gate(
        runner,
        prior_eval_task_ids=["old_passing"],
        best_ever_score=0.5,
    )
    assert result.decision == GateDecision.FAIL_REGRESSION
    assert result.failed_prior_task_ids == ("old_passing",)


async def test_change_that_improves_both_is_accepted_and_promotes():
    """PLAN.md W2.2 validation: 'write change that improves both →
    gate accepts and promotes new cases'."""
    tasks = (
        SyntheticTask(id="old_passing", input=10, expected=20),
        SyntheticTask(id="new_failing_now_fixed", input=5, expected=42),
    )

    # Candidate: handles both correctly.
    def candidate(x: int) -> int:
        return 42 if x == 5 else x * 2

    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=candidate)
    result = await run_gate(
        runner,
        prior_eval_task_ids=["old_passing"],
        best_ever_score=0.5,
    )
    assert result.decision == GateDecision.PASS
    assert result.val_score == 1.0
    assert result.best_ever_score_after == 1.0
    assert "new_failing_now_fixed" in result.promotable_task_ids


# ---------------------------------------------------------------------------
# Decision values map to IterationState
# ---------------------------------------------------------------------------


def test_gate_decision_values_match_iteration_state():
    """GateDecision values are wire-compatible with IterationState so
    the PR #8 wrapper can write `iterations.state` directly without
    a translation table. Documented in result.py."""
    from ownevo_kernel.types import IterationState

    expected = {
        GateDecision.PASS: IterationState.GATE_PASS,
        GateDecision.FAIL_REGRESSION: IterationState.GATE_BLOCKED_REGRESSION,
        GateDecision.FAIL_NO_IMPROVEMENT: IterationState.GATE_BLOCKED_NO_IMPROVEMENT,
        GateDecision.SANDBOX_ERROR: IterationState.SANDBOX_ERROR,
    }
    for gate_dec, iter_state in expected.items():
        assert gate_dec.value == iter_state.value


# ---------------------------------------------------------------------------
# Rationale messages — readable
# ---------------------------------------------------------------------------


async def test_rationale_includes_failed_task_preview():
    """Rationale should be loggable / surfaceable to the user without
    digging into the structured fields."""
    runner = SyntheticBenchmarkRunner(
        tasks=_doubler_tasks(5),
        skill=lambda x: 99,
    )
    result = await run_gate(runner, prior_eval_task_ids=["0", "1", "2", "3", "4"])
    assert result.decision == GateDecision.FAIL_REGRESSION
    # First three failing tasks are previewed inline.
    assert "0" in result.rationale
    assert "+2 more" in result.rationale


async def test_rationale_explains_no_improvement():
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(2), skill=lambda x: x)
    result = await run_gate(runner, best_ever_score=0.5)
    assert result.decision == GateDecision.FAIL_NO_IMPROVEMENT
    assert "did not beat" in result.rationale
    assert "0.5000" in result.rationale  # best_ever


async def test_rationale_explains_pass():
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(3), skill=lambda x: x * 2)
    result = await run_gate(runner, best_ever_score=0.5)
    assert result.decision == GateDecision.PASS
    assert "Gate passed" in result.rationale
    assert "promotable" in result.rationale


# ---------------------------------------------------------------------------
# Reward trust boundary — NaN / out-of-range rewards (D1)
# ---------------------------------------------------------------------------


async def test_nan_reward_treated_as_sandbox_error():
    """NaN in rewards bypasses `is None` check and `NaN < threshold` → False,
    so without explicit validation the gate would silently PASS. Validated."""

    class _NaNRunner:
        async def run(self, task_ids=None):
            return BenchmarkResult(rewards={"a": float("nan"), "b": 1.0})

    result = await run_gate(_NaNRunner(), best_ever_score=0.5)
    assert result.decision == GateDecision.SANDBOX_ERROR
    assert result.val_score is None
    assert result.best_ever_score_after == 0.5  # never advances


async def test_out_of_range_reward_treated_as_sandbox_error():
    """Reward > 1.0 inflates val_score and would trivially beat best_ever;
    treat as a trust-boundary error."""

    class _CheatRunner:
        async def run(self, task_ids=None):
            return BenchmarkResult(rewards={"a": 2.5, "b": 1.0})

    result = await run_gate(_CheatRunner(), best_ever_score=0.5)
    assert result.decision == GateDecision.SANDBOX_ERROR


# ---------------------------------------------------------------------------
# Runner exception → SANDBOX_ERROR (D2)
# ---------------------------------------------------------------------------


async def test_runner_exception_returns_sandbox_error():
    """If the runner raises (Docker not running, network failure), the
    gate returns SANDBOX_ERROR instead of propagating a raw exception."""

    class _CrashingRunner:
        async def run(self, task_ids=None):
            raise RuntimeError("Docker daemon not running")

    result = await run_gate(_CrashingRunner(), best_ever_score=0.5)
    assert result.decision == GateDecision.SANDBOX_ERROR
    assert "RuntimeError" in result.rationale
    assert result.val_score is None
    assert result.best_ever_score_after == 0.5  # never advances on error


# ---------------------------------------------------------------------------
# Promotable ordering is deterministic (D3)
# ---------------------------------------------------------------------------


async def test_promotable_task_ids_are_sorted():
    """promotable_task_ids is sorted so the audit log is identical for
    the same candidate regardless of runner parallelism."""
    runner = SyntheticBenchmarkRunner(tasks=_doubler_tasks(5), skill=lambda x: x * 2)
    result = await run_gate(runner)
    assert result.promotable_task_ids == tuple(sorted(result.promotable_task_ids))


# ---------------------------------------------------------------------------
# Zero-scoring tasks not promotable even at regression_tolerance=1.0 (D4)
# ---------------------------------------------------------------------------


async def test_zero_scoring_tasks_not_promoted_at_max_tolerance():
    """regression_tolerance=1.0 → threshold=0.0. Without the r > 0.0
    guard, 0.0-scoring (definitively failing) tasks would be promoted
    into the prior suite and silently never trigger regression."""
    tasks = tuple(
        SyntheticTask(id=str(i), input=i, expected=i * 2) for i in range(4)
    )
    # Skill passes task "0" (reward=1.0), fails tasks 1-3 (reward=0.0).
    runner = SyntheticBenchmarkRunner(tasks=tasks, skill=lambda x: 0 if x == 0 else x)
    result = await run_gate(runner, regression_tolerance=1.0)
    assert result.decision == GateDecision.PASS
    # Only the passing task is promotable — the 0.0-scoring ones must not be.
    assert "0" in result.promotable_task_ids
    assert "1" not in result.promotable_task_ids
    assert "2" not in result.promotable_task_ids
    assert "3" not in result.promotable_task_ids
