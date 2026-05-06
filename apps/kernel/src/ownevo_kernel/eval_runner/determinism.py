"""Determinism guardrail for deterministic eval replay (A4.5).

PLAN.md A4.5: "nondeterministic eval failures flagged as bugs.
Repeat eval-replay → identical score (within numeric tolerance)."

The deterministic `run_replay` path is deterministic by construction:
the rendered sim builds a fresh `random.Random(seed)` per call and
returns a trajectory that's a pure function of (seed, n_steps). Any
divergence between two `run_replay` calls on the same inputs is a bug —
either in the renderer (it carried hidden module-level state across
exec'd namespaces), in the case generator (it produced a case whose
`expected_value` was sampled from sim entropy that the seed doesn't
control), or in `compute_metric` (float-FP non-associativity, which
should never happen for our metric families but we guard anyway).

`verify_determinism` runs `run_replay` twice with identical inputs and
asserts:

  * Per-case `actual_value` matches byte-for-byte across runs.
  * Per-case `passed` matches.
  * Confusion-matrix counts match.
  * Metric value is within `1e-9` (defensive against IEEE-754
    associativity quirks; in practice differences are 0).

On the first divergent observation we raise `NondeterminismError` with
the case_id and both observed values, so the surface is exactly the
"flagged as bug" PLAN wording.

Why two runs (not three or N): two is enough to catch divergence; N
is over-engineering. The cost is one duplicate replay (in-process,
single sim namespace per run) — fast enough to leave on by default
in CI but kept opt-in via `--check-determinism` to avoid doubling
local dev iteration.

Why `1e-9`: the metric values we compute are simple ratios of small
counts (TP/(TP+FN) for recall, etc.), bounded in [0, 1]. They should
be byte-equal across runs. `1e-9` is the smallest tolerance that's
larger than typical IEEE-754 reordering noise for these expressions
without being so generous it would mask a real bug.
"""

from __future__ import annotations

import math

from .runner import EvalRunReport, EvalRunnerError, run_replay

# Local imports to avoid a circular import: this module is imported by
# `eval_runner/__init__.py`, and `runner.py` is also imported there.
# Keep the imports below the dataclass shapes.
from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet
from ownevo_kernel.nl_gen.metric_def import MetricDefinition
from ownevo_kernel.nl_gen.sim_plan import SimulationPlan
from ownevo_kernel.nl_gen.spec import WorkflowSpec


METRIC_VALUE_TOLERANCE = 1e-9
"""Float tolerance for the metric value across two replays.

Per-case `actual_value` is a bool — equality is exact. Confusion counts
are ints — equality is exact. The metric value is a float ratio that
should be byte-equal across runs; the tolerance is defensive against
IEEE-754 associativity, not a license for nondeterminism."""


class NondeterminismError(EvalRunnerError):
    """Two replays of the same eval set disagreed.

    Raised the moment any divergence is observed (per-case `actual_value`,
    per-case `passed`, confusion counts, or metric value). Carries the
    first divergent observation so the operator can drill in without
    rerunning. Subclass of `EvalRunnerError` for symmetry with the rest
    of the runner's typed-error surface.
    """

    def __init__(
        self,
        message: str,
        *,
        case_id: str | None,
        run1_value: object,
        run2_value: object,
        kind: str,
    ) -> None:
        super().__init__(message)
        self.case_id = case_id
        self.run1_value = run1_value
        self.run2_value = run2_value
        self.kind = kind


def compare_reports(
    run1: EvalRunReport, run2: EvalRunReport
) -> None:
    """Raise `NondeterminismError` on the first divergence.

    Order matters: we check per-case outcomes first because that's the
    most common bug surface (sim entropy not seeded, namespace state
    leaking). Confusion counts and metric value drop out of the
    outcomes; mismatches there imply a bug in `compute_metric` itself.
    """
    if len(run1.outcomes) != len(run2.outcomes):
        raise NondeterminismError(
            f"replay 1 produced {len(run1.outcomes)} outcomes; "
            f"replay 2 produced {len(run2.outcomes)}",
            case_id=None,
            run1_value=len(run1.outcomes),
            run2_value=len(run2.outcomes),
            kind="outcome_count",
        )

    for o1, o2 in zip(run1.outcomes, run2.outcomes):
        if o1.case_id != o2.case_id:
            raise NondeterminismError(
                f"replay outcome order diverged: replay 1 saw "
                f"{o1.case_id!r}, replay 2 saw {o2.case_id!r}",
                case_id=o1.case_id,
                run1_value=o1.case_id,
                run2_value=o2.case_id,
                kind="case_id_order",
            )
        if o1.actual_value != o2.actual_value:
            raise NondeterminismError(
                f"case {o1.case_id!r}: replay 1 actual={o1.actual_value!r}, "
                f"replay 2 actual={o2.actual_value!r} — sim is not "
                f"deterministic for this case",
                case_id=o1.case_id,
                run1_value=o1.actual_value,
                run2_value=o2.actual_value,
                kind="actual_value",
            )
        if o1.passed != o2.passed:
            # Should be unreachable if actual_value matches, but pin it
            # so a future change in the pass predicate trips the test.
            raise NondeterminismError(
                f"case {o1.case_id!r}: replay 1 passed={o1.passed}, "
                f"replay 2 passed={o2.passed} — pass predicate diverged "
                f"despite identical actual_value",
                case_id=o1.case_id,
                run1_value=o1.passed,
                run2_value=o2.passed,
                kind="passed",
            )

    for field_name in ("tp", "tn", "fp", "fn", "n_total", "n_pass"):
        v1 = getattr(run1, field_name)
        v2 = getattr(run2, field_name)
        if v1 != v2:
            raise NondeterminismError(
                f"confusion-matrix count {field_name!r} diverged: "
                f"replay 1={v1}, replay 2={v2}",
                case_id=None,
                run1_value=v1,
                run2_value=v2,
                kind=f"count:{field_name}",
            )

    delta = abs(run1.value - run2.value)
    if math.isnan(delta) or delta > METRIC_VALUE_TOLERANCE:
        raise NondeterminismError(
            f"metric value diverged beyond tolerance: "
            f"replay 1={run1.value!r}, replay 2={run2.value!r}, "
            f"delta={delta!r} > {METRIC_VALUE_TOLERANCE!r}",
            case_id=None,
            run1_value=run1.value,
            run2_value=run2.value,
            kind="metric_value",
        )


def verify_determinism(
    case_set: EvalCaseSet,
    plan: SimulationPlan,
    spec: WorkflowSpec,
    metric: MetricDefinition,
) -> EvalRunReport:
    """Run `run_replay` twice; assert identical outcomes; return run 1.

    The check fires sequentially in-process. The two runs render + exec
    the sim module independently — that's load-bearing: it catches
    module-level state leaks that an A4.1-style namespace reuse would
    paper over.

    Args:
        case_set / plan / spec / metric: same as `run_replay`.

    Returns:
        The first `EvalRunReport` (the two reports are equal modulo
        the `1e-9` metric-value tolerance). Returning a report keeps
        `verify_determinism` a drop-in replacement for `run_replay` in
        the CLI.

    Raises:
        NondeterminismError: any per-case or aggregate divergence.
        ValueError / EvalReplayError / MetricComputeError: same surfaces
            as `run_replay` — propagated from either inner call.
    """
    run1 = run_replay(case_set, plan, spec, metric)
    run2 = run_replay(case_set, plan, spec, metric)
    compare_reports(run1, run2)
    return run1


__all__ = [
    "METRIC_VALUE_TOLERANCE",
    "NondeterminismError",
    "compare_reports",
    "verify_determinism",
]
