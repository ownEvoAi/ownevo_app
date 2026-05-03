"""Gate self-test harness (W2.2a) — synthetic regression scenarios.

The gate is the trust mechanism. If it has a bug, every "approved
improvement" downstream is meaningless — bad changes drift the M5
lift the wrong direction, and the audit log records bogus deltas.

This harness pins two non-negotiable invariants:

  * **Known-good change is admitted.** A skill that fixes a previously-
    failing case without breaking anything passes the gate.
  * **Known-bad change is blocked.** A skill that breaks a previously-
    passing case is rejected with `FAIL_REGRESSION`, even if it
    happens to improve other metrics.

The harness is independent from M5 and τ³ on purpose — testing the
gate against the same benchmarks the gate runs is circular. We use
`SyntheticBenchmarkRunner` (in-process, no Docker, no DB, no LLM) so
the failure mode being detected is purely "the gate's logic is
broken," not "the substrate is flaky."

Per PLAN.md W2.2a: this is the gate-trust contract. Failing it fails
the build.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from ownevo_kernel.benchmark import (
    SyntheticBenchmarkRunner,
    SyntheticTask,
)
from ownevo_kernel.gate import GateDecision, run_gate

# ---------------------------------------------------------------------------
# Shared scenario: a tiny "supply forecast" surrogate
#
# The point of the synthetic surrogate is shape-fidelity, not realism:
# - There's a "prior eval suite" (cases that have been promoted before).
# - Each candidate skill is a deterministic Python callable.
# - The runner scores binary pass/fail per case.
#
# This shape is what M5BenchmarkRunner and Tau3BenchmarkRunner will
# implement; passing the self-test means the gate's runner-consumption
# path is correct under any compliant runner.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scenario:
    """A self-test scenario: tasks + prior eval suite + best-ever
    score + the candidate skill under test + the expected decision."""

    name: str
    tasks: tuple[SyntheticTask, ...]
    prior_eval_task_ids: tuple[str, ...]
    best_ever_score: float | None
    candidate: object  # SkillFn
    expected: GateDecision


def _baseline_tasks() -> tuple[SyntheticTask, ...]:
    """Five doubling tasks — id matches `i * 2` so a correct skill
    returns full credit on every one."""
    return tuple(
        SyntheticTask(id=f"item-{i}", input=i, expected=i * 2)
        for i in range(1, 6)
    )


# ---------------------------------------------------------------------------
# Known-good change: fixes a regression case, doesn't break the rest
# ---------------------------------------------------------------------------


async def test_known_good_change_is_admitted():
    """A candidate skill that fixes a previously-failing case AND
    keeps every prior-suite task passing is admitted."""

    # Parent skill: correct doubler EXCEPT on item-3 (the failure
    # cluster). Suppose the prior eval suite has items 1, 2, 4, 5
    # all passing under the parent. Item-3 is the new case the
    # candidate is trying to fix.
    def candidate(x: int) -> int:
        return x * 2  # correct on every input including item-3

    scenario = _Scenario(
        name="fixes-cluster-without-breaking-prior",
        tasks=_baseline_tasks(),
        prior_eval_task_ids=("item-1", "item-2", "item-4", "item-5"),
        best_ever_score=0.8,  # parent had 4/5 passing
        candidate=candidate,
        expected=GateDecision.PASS,
    )
    result = await _run_scenario(scenario)
    assert result.decision == scenario.expected, (
        f"{scenario.name}: expected {scenario.expected.value}, "
        f"got {result.decision.value} ({result.rationale})"
    )
    # The newly-fixed case should be promotable.
    assert "item-3" in result.promotable_task_ids
    # val_score is 1.0 (all 5 pass) > best_ever 0.8 — improvement
    # check is satisfied.
    assert result.val_score == 1.0
    assert result.best_ever_score_after == 1.0


# ---------------------------------------------------------------------------
# Known-bad change: breaks a previously-passing case
# ---------------------------------------------------------------------------


async def test_known_bad_change_is_blocked():
    """A candidate that fixes one case but breaks a previously-passing
    case is rejected — the gate's protective contract."""

    # Candidate: fixes item-3 BUT regresses item-1 (returns wrong
    # output for input=1).
    def candidate(x: int) -> int:
        if x == 1:
            return -999  # regression on item-1 (was passing under parent)
        return x * 2

    scenario = _Scenario(
        name="fixes-cluster-but-regresses-prior",
        tasks=_baseline_tasks(),
        prior_eval_task_ids=("item-1", "item-2", "item-4", "item-5"),
        best_ever_score=0.8,
        candidate=candidate,
        expected=GateDecision.FAIL_REGRESSION,
    )
    result = await _run_scenario(scenario)
    assert result.decision == scenario.expected, (
        f"{scenario.name}: expected {scenario.expected.value}, "
        f"got {result.decision.value} ({result.rationale})"
    )
    assert result.failed_prior_task_ids == ("item-1",)
    # D3: best-ever does not advance on a rejected gate.
    assert result.best_ever_score_after == 0.8
    # No promotion happens on a rejection.
    assert result.promotable_task_ids == ()


# ---------------------------------------------------------------------------
# Known-bad change variant: improves prior cases but no net improvement
# ---------------------------------------------------------------------------


async def test_known_bad_no_improvement_change_is_blocked():
    """A candidate that maintains every prior case but doesn't actually
    improve val_score is blocked under FAIL_NO_IMPROVEMENT — admitting
    it would inflate the audit log with no-op approvals."""

    # Identical to the parent: same val_score, no progress.
    def candidate(x: int) -> int:
        return x * 2 if x != 3 else x  # same as parent: 4/5 passing

    scenario = _Scenario(
        name="no-net-improvement",
        tasks=_baseline_tasks(),
        prior_eval_task_ids=("item-1", "item-2", "item-4", "item-5"),
        best_ever_score=0.8,
        candidate=candidate,
        expected=GateDecision.FAIL_NO_IMPROVEMENT,
    )
    result = await _run_scenario(scenario)
    assert result.decision == scenario.expected, (
        f"{scenario.name}: expected {scenario.expected.value}, "
        f"got {result.decision.value}"
    )


# ---------------------------------------------------------------------------
# Adversarial: known-bad change that LOOKS good on aggregate
# ---------------------------------------------------------------------------


async def test_known_bad_change_with_higher_aggregate_is_still_blocked():
    """The classic gate-failure mode: a candidate that improves enough
    new cases to lift val_score, BUT silently breaks a prior case.
    Without the regression-suite step, the gate would falsely admit
    this. The self-test asserts the regression-suite step catches it
    even when val_score is higher."""

    # 10 tasks total. Parent passes items 1-4 (val_score 0.4); fails 5-10.
    # Candidate fixes 5-10 (gains lift) but breaks item-1.
    tasks = tuple(
        SyntheticTask(id=f"item-{i}", input=i, expected=i * 2)
        for i in range(1, 11)
    )

    def candidate(x: int) -> int:
        if x == 1:
            return -999  # break the prior case
        return x * 2  # everything else now passes

    # val_score under candidate: 9/10 = 0.9, well above best_ever 0.4.
    # Without the regression check, this would PASS. It must FAIL.
    scenario = _Scenario(
        name="adversarial-aggregate-mask",
        tasks=tasks,
        prior_eval_task_ids=("item-1", "item-2", "item-3", "item-4"),
        best_ever_score=0.4,
        candidate=candidate,
        expected=GateDecision.FAIL_REGRESSION,
    )
    result = await _run_scenario(scenario)
    assert result.decision == scenario.expected
    assert result.failed_prior_task_ids == ("item-1",)
    # Note: val_score IS computed and would have looked great in
    # isolation — surfacing it on the rejection lets the audit log
    # show "the candidate scored 0.9 but regressed item-1, so we
    # blocked it." That's the protective contract working.
    assert result.val_score == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Sandbox-error short-circuit: trust-boundary case
# ---------------------------------------------------------------------------


async def test_sandbox_error_short_circuits():
    """A candidate that raises an exception (RuntimeError here) is scored
    0.0 by SyntheticBenchmarkRunner — `except BaseException` catches all
    exceptions and returns definite-failure, not None. Real sandbox-error
    short-circuit (None rewards from timeout/OOM/crash) is exercised in
    test_gate.py — here we pin the synthetic-runner behavior so the
    self-test surfaces exception-style failures as gate-blocked, not
    as a silent "0.0 was good enough" admission."""

    def crashing_candidate(x: int) -> int:
        raise RuntimeError("synthetic skill crash")

    scenario = _Scenario(
        name="crashing-skill",
        tasks=_baseline_tasks(),
        prior_eval_task_ids=("item-1",),
        best_ever_score=0.8,
        candidate=crashing_candidate,
        expected=GateDecision.FAIL_REGRESSION,
    )
    result = await _run_scenario(scenario)
    # All 5 tasks score 0.0; item-1 is in prior suite → step 1 fails.
    assert result.decision == scenario.expected
    assert result.val_score == 0.0


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _run_scenario(scenario: _Scenario):
    runner = SyntheticBenchmarkRunner(
        tasks=scenario.tasks,
        skill=scenario.candidate,  # type: ignore[arg-type]
    )
    return await run_gate(
        runner,
        prior_eval_task_ids=list(scenario.prior_eval_task_ids),
        best_ever_score=scenario.best_ever_score,
    )
