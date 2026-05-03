"""GateDecision + GateResult — what `run_gate` returns.

The result is intentionally rich: the caller in PR #8 will need
`failed_prior_task_ids` to write a structured rejection rationale and
`promotable_task_ids` to drive `add_eval_case` writes. Keeping the
gate's internal evidence visible in the return value (instead of
hiding it in logs) makes the gate's decision auditable byte-for-byte
without re-running the benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..benchmark import BenchmarkResult


class GateDecision(StrEnum):
    """Maps 1:1 to `IterationState` values the caller writes — except
    `running`, which is pre-gate.

    Bound deliberately to the iteration_state enum so the wrapper in
    PR #8 can `IterationState(decision.value)` without a translation
    table.
    """

    PASS = "gate-pass"
    FAIL_REGRESSION = "gate-blocked-regression"
    FAIL_NO_IMPROVEMENT = "gate-blocked-no-improvement"
    SANDBOX_ERROR = "sandbox-error"


@dataclass(frozen=True)
class GateResult:
    """Outcome of one `run_gate` call.

    Field semantics:
      * `val_score` — `full_run.val_score` when the full run executed,
        else None. Set even on FAIL_NO_IMPROVEMENT (the score is the
        evidence the gate used to reject).
      * `best_ever_score_after` — equals
        `max(best_ever_score_before, val_score)` on PASS; equals
        `best_ever_score_before` on every other decision (D3: sandbox
        errors and failed gates do NOT advance best-ever).
      * `failed_prior_task_ids` — non-empty only on FAIL_REGRESSION.
        Listed in `prior_eval_task_ids` order so the caller's
        rejection message reads predictably.
      * `promotable_task_ids` — non-empty only on PASS. Tasks in the
        full run that passed at threshold AND were not part of
        `prior_eval_task_ids` — the cluster-derived cases the gate
        just admitted that the caller can promote into the prior suite
        (W2.2 spec step 3).
    """

    decision: GateDecision
    rationale: str

    val_score: float | None
    best_ever_score_before: float | None
    best_ever_score_after: float | None

    full_run: BenchmarkResult | None
    failed_prior_task_ids: tuple[str, ...]
    promotable_task_ids: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.decision == GateDecision.PASS
