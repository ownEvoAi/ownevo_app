"""Three-step regression gate over `BenchmarkRunner` (W2.2).

The gate runs the candidate skill once over the runner's full task
set, then derives all three steps from that single result:

  1. **Prior-suite still passes.** Every task in `prior_eval_task_ids`
     must score at or above `1.0 - regression_tolerance`. Empty prior
     suite → step skipped (Day-1 bootstrap rule from PLAN W2.2).
  2. **Val-score beats best-ever.** Mean reward must exceed
     `best_ever_score + improvement_epsilon`. `best_ever_score=None`
     → step skipped (first run becomes the baseline; the day-1 baseline
     pipeline is responsible for not seeding a zero baseline).
  3. **Newly-passing failures are promotable.** Tasks in the full run
     that passed at threshold and were *not* in `prior_eval_task_ids`
     are returned as `promotable_task_ids` for the caller to wire into
     `add_eval_case`. The gate doesn't write to `eval_cases` itself —
     the cluster→eval-case lift is W3 work.

Sandbox errors short-circuit: if any task in the full run returns
None (Timeout / OOM / Crash via the runner), the gate emits
`SANDBOX_ERROR` without trusting `val_score`. D3 forbids advancing
best-ever in this state.

The gate executes `runner.run(None)` exactly once. Real M5 / τ³
runners can cache or parallelize internally; the gate stays
runner-agnostic.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..benchmark import BenchmarkRunner
from .result import GateDecision, GateResult


async def run_gate(
    runner: BenchmarkRunner,
    *,
    prior_eval_task_ids: Sequence[str] = (),
    best_ever_score: float | None = None,
    regression_tolerance: float = 0.0,
    improvement_epsilon: float = 0.0,
) -> GateResult:
    """Run the gate against `runner` and return a structured `GateResult`.

    Args:
      runner: any `BenchmarkRunner` — `SyntheticBenchmarkRunner` for
        the gate self-test, M5/τ³ runners in production.
      prior_eval_task_ids: tasks already promoted to the suite. Empty
        = bootstrap (step 1 skipped).
      best_ever_score: highest val_score seen for this workflow.
        None = bootstrap (step 2 skipped).
      regression_tolerance: per-task slip allowed in step 1. 0.0 =
        strict (full credit required). Mirrors the eval_cases.regression_tolerance
        column shape (in [0,1]).
      improvement_epsilon: minimum delta over best-ever that counts
        as improvement in step 2. 0.0 = any strict gain qualifies.

    The function is async because real runners (sandbox-backed) are
    async; the synthetic runner satisfies the same Protocol so testing
    reuses the same call shape.
    """
    if not 0.0 <= regression_tolerance <= 1.0:
        raise ValueError(
            f"regression_tolerance must be in [0,1]; got {regression_tolerance}",
        )
    if improvement_epsilon < 0.0:
        raise ValueError(
            f"improvement_epsilon must be >= 0; got {improvement_epsilon}",
        )

    full_run = await runner.run(None)

    # Sandbox-error short-circuit (D3): any None reward poisons the
    # whole gate run — we don't trust val_score, don't advance
    # best-ever, don't surface a regression list (the agent didn't
    # cause this).
    if full_run.n_no_result > 0:
        return GateResult(
            decision=GateDecision.SANDBOX_ERROR,
            rationale=(
                f"{full_run.n_no_result}/{full_run.n_tasks} task(s) returned no result "
                f"(Timeout/OOM/Crash); gate refuses to score under sandbox errors"
            ),
            val_score=None,
            best_ever_score_before=best_ever_score,
            best_ever_score_after=best_ever_score,
            full_run=full_run,
            failed_prior_task_ids=(),
            promotable_task_ids=(),
        )

    val_score = full_run.val_score
    threshold = 1.0 - regression_tolerance

    # Step 1: prior-suite regression check. A missing task is treated
    # as a failure — the runner's task universe shouldn't shrink under
    # the candidate; if it has, the gate's protective contract is
    # broken and we reject conservatively.
    failed_prior = tuple(
        t for t in prior_eval_task_ids
        if full_run.rewards.get(t) is None or full_run.rewards[t] < threshold
    )
    if failed_prior:
        preview = ", ".join(failed_prior[:3])
        more = f" (+{len(failed_prior) - 3} more)" if len(failed_prior) > 3 else ""
        return GateResult(
            decision=GateDecision.FAIL_REGRESSION,
            rationale=(
                f"Prior eval suite regressed on {len(failed_prior)} task(s): "
                f"{preview}{more}"
            ),
            val_score=val_score,
            best_ever_score_before=best_ever_score,
            best_ever_score_after=best_ever_score,
            full_run=full_run,
            failed_prior_task_ids=failed_prior,
            promotable_task_ids=(),
        )

    # Step 2: improvement check. Bootstrap (best_ever=None) skips this
    # step — the first passing gate run sets the baseline.
    if (
        best_ever_score is not None
        and val_score <= best_ever_score + improvement_epsilon
    ):
        return GateResult(
            decision=GateDecision.FAIL_NO_IMPROVEMENT,
            rationale=(
                f"val_score {val_score:.4f} did not beat "
                f"best_ever {best_ever_score:.4f} "
                f"(epsilon {improvement_epsilon:g})"
            ),
            val_score=val_score,
            best_ever_score_before=best_ever_score,
            best_ever_score_after=best_ever_score,
            full_run=full_run,
            failed_prior_task_ids=(),
            promotable_task_ids=(),
        )

    # Step 3: collect promotable task IDs — passed at threshold AND not
    # already in the prior suite.
    prior_set = set(prior_eval_task_ids)
    promotable = tuple(
        t for t, r in full_run.rewards.items()
        if t not in prior_set and r is not None and r >= threshold
    )

    new_best = (
        val_score if best_ever_score is None else max(best_ever_score, val_score)
    )

    if best_ever_score is None:
        improvement_clause = "(initial baseline)"
    else:
        improvement_clause = f"> best_ever {best_ever_score:.4f}"
    return GateResult(
        decision=GateDecision.PASS,
        rationale=(
            f"Gate passed: val_score {val_score:.4f} {improvement_clause}; "
            f"{len(promotable)} promotable task(s)"
        ),
        val_score=val_score,
        best_ever_score_before=best_ever_score,
        best_ever_score_after=new_best,
        full_run=full_run,
        failed_prior_task_ids=(),
        promotable_task_ids=promotable,
    )
