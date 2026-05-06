"""Metric compute over eval-replay results (A4.2).

Pure function `compute_metric(definition, results)` — turns a list of
`eval_replay.ReplayResult` into a `MetricResult` per the
`MetricDefinition.family`. Mirrors `eval_replay`'s split between schema
(`metric_def.py`) and execution (this module).

Why a closed dispatch instead of a registry of functions:

  * Every supported `MetricFamily` is a one-line confusion-matrix
    formula; a registry would add indirection without removing anything.
  * The dispatch is the only place that has to change when widening
    `MetricFamily`. Keeping it explicit makes the change diff small and
    review-able.
  * The closed family lets the type checker prove every branch is
    reached (`assert_never` at the end of the if-chain).

Degenerate cases (zero positives → undefined precision; zero negatives
→ undefined specificity) return 0.0 rather than NaN. The gate compares
floats; NaN would silently fail every direction check. 0.0 is the
correct "no positive predictions" value for precision (no TP), and
matches scikit-learn's `zero_division=0` default. We surface the
zero-division branch via `MetricResult.degenerate` so the audit trail
can flag it.

The cross-spec validator `_check_against_spec` lives here (not on
`MetricDefinition`) because pydantic validators can't see the
WorkflowSpec; the call site (the generator + the gate) wires it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from .eval_replay import ReplayResult
from .metric_def import MetricDefinition
from .spec import WorkflowSpec


class MetricComputeError(ValueError):
    """Compute hit a structural problem the result list can't recover from.

    Distinct from a low metric value: a `MetricResult` with a low `value`
    is legitimate gate signal. `MetricComputeError` means the inputs are
    structurally broken — empty result list, non-bool actual or expected
    values, or a metric definition whose `family` isn't in the dispatch
    (which would be a programming error, not a data error).
    """


@dataclass(frozen=True)
class MetricResult:
    """Outcome of computing a metric over an eval-replay result list.

    `value` is what the gate compares against `target_value`. The TP/TN/
    FP/FN counts and `degenerate` flag are surfaced for the audit trail
    and the W7 metric-card UI — the reviewer looks at these to understand
    why `value` is what it is.
    """

    metric_name: str
    family: str
    value: float
    n_total: int
    n_pass: int
    tp: int
    tn: int
    fp: int
    fn: int
    meets_target: bool
    degenerate: bool


def _check_against_spec(definition: MetricDefinition, spec: WorkflowSpec) -> None:
    """Cross-check the metric definition against its source WorkflowSpec.

    Raises `ValueError` (mirroring `eval_replay.replay_set`) if:

      * `definition.workflow_spec_id != spec.id`, or
      * `definition.direction != spec.success_criterion.direction`.

    The direction check is load-bearing: the gate uses `direction` to
    decide "improvement vs regression"; if the metric and the spec
    disagree, regressions look like wins.
    """
    if definition.workflow_spec_id != spec.id:
        raise ValueError(
            f"definition.workflow_spec_id={definition.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}"
        )
    if definition.direction != spec.success_criterion.direction:
        raise ValueError(
            f"definition.direction={definition.direction!r} does not match "
            f"spec.success_criterion.direction="
            f"{spec.success_criterion.direction!r}"
        )


def _confusion_counts(results: list[ReplayResult]) -> tuple[int, int, int, int]:
    """Compute (TP, TN, FP, FN) treating `expected_value=True` as positive.

    Raises `MetricComputeError` if any expected or actual value is not a
    bool — the eval-replay path validates label fields are bool-typed,
    so a non-bool here is a structural break (label-field type drift,
    sim renderer change, etc.).
    """
    tp = tn = fp = fn = 0
    for r in results:
        if not isinstance(r.expected_value, bool):
            raise MetricComputeError(
                f"case {r.case_id!r}: expected_value="
                f"{r.expected_value!r} is not a bool"
            )
        if not isinstance(r.actual_value, bool):
            raise MetricComputeError(
                f"case {r.case_id!r}: actual_value="
                f"{r.actual_value!r} is not a bool"
            )
        if r.expected_value and r.actual_value:
            tp += 1
        elif not r.expected_value and not r.actual_value:
            tn += 1
        elif not r.expected_value and r.actual_value:
            fp += 1
        else:
            fn += 1
    return tp, tn, fp, fn


def _safe_div(num: int, den: int) -> tuple[float, bool]:
    """Return (num/den, degenerate). Degenerate iff den == 0."""
    if den == 0:
        return 0.0, True
    return num / den, False


def compute_metric(
    definition: MetricDefinition, results: list[ReplayResult]
) -> MetricResult:
    """Compute the metric defined by `definition` over `results`.

    Args:
        definition: The metric to compute.
        results: ReplayResult list, typically from `eval_replay.replay_set`.

    Returns:
        A `MetricResult` carrying the computed value, confusion counts,
        and whether `target_value` was met under `direction`.

    Raises:
        MetricComputeError: results is empty, or any expected/actual
            value is not a bool, or `definition.family` is not in the
            dispatch (programming error).
    """
    if not results:
        raise MetricComputeError(
            "cannot compute a metric over an empty result list — the gate "
            "needs at least one case to score against"
        )

    tp, tn, fp, fn = _confusion_counts(results)
    n_total = len(results)
    n_pass = tp + tn

    family = definition.family
    degenerate = False

    if family == "pass_rate":
        value, _ = _safe_div(n_pass, n_total)
    elif family == "precision":
        value, degenerate = _safe_div(tp, tp + fp)
    elif family == "recall":
        value, degenerate = _safe_div(tp, tp + fn)
    elif family == "f1":
        precision, p_deg = _safe_div(tp, tp + fp)
        recall, r_deg = _safe_div(tp, tp + fn)
        denom = precision + recall
        if denom == 0.0:
            value, degenerate = 0.0, True
        else:
            value = 2 * precision * recall / denom
            degenerate = p_deg or r_deg
    elif family == "balanced_accuracy":
        recall, r_deg = _safe_div(tp, tp + fn)
        specificity, s_deg = _safe_div(tn, tn + fp)
        value = (recall + specificity) / 2
        degenerate = r_deg or s_deg
    elif family == "specificity":
        value, degenerate = _safe_div(tn, tn + fp)
    else:
        # Closed Literal — `assert_never` proves the dispatch is exhaustive.
        # If MetricFamily is widened, type-check fails here until the new
        # branch is added.
        assert_never(family)

    if not (definition.lower_bound <= value <= definition.upper_bound):
        raise MetricComputeError(
            f"computed value={value} fell outside the definition's bounds "
            f"[{definition.lower_bound}, {definition.upper_bound}] — "
            f"this means the bounds in MetricDefinition disagree with "
            f"the family's actual range, which is a programming error"
        )

    if definition.direction == "maximize":
        meets_target = value >= definition.target_value
    else:
        meets_target = value <= definition.target_value

    return MetricResult(
        metric_name=definition.name,
        family=family,
        value=value,
        n_total=n_total,
        n_pass=n_pass,
        tp=tp,
        tn=tn,
        fp=fp,
        fn=fn,
        meets_target=meets_target,
        degenerate=degenerate,
    )


__all__ = [
    "MetricComputeError",
    "MetricResult",
    "compute_metric",
    "_check_against_spec",
]
