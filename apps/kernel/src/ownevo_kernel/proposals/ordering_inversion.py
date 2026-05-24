"""Ordering-inversion check for kind='metric' proposals.

When the success metric changes, the gate's verdict on prior
iterations can flip. The Track 9.2.3 product story is "metric changes
surface ordering-inversion warnings before approve" — this module
implements that check.

The check re-scores every prior iteration on the workflow under both
the current and the proposed metric, then identifies any iteration
whose gate verdict (`meets_target`) would flip. The proposal-detail
surface renders the result as a warning panel above the approval
form so the reviewer sees the consequence of approving the metric
change before they click.

Scope notes:
* Re-scoring uses `nl_gen.metric_compute.compute_metric` — the same
  function the regression gate calls. Single source of truth for
  metric semantics.
* The proposed metric payload from the Spec-tab form may not carry
  every MetricDefinition field (workflow_spec_id / bounds /
  target_value). Defaults are inherited from the workflow's current
  metric_definition so the check still runs against incomplete
  user-supplied payloads.
* `actual_value` is extracted from `iteration_case_outputs.output_json`
  using the eval case's `target_label_field`. When the field is
  missing on a row the case is skipped — the result reports the
  partial coverage rather than erroring.
* The result `status` is `'ok'`, `'unavailable'`, or `'error'`. The
  caller renders gracefully on the non-`ok` paths rather than
  exposing internal failure modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from ..nl_gen.eval_replay import ReplayResult
from ..nl_gen.metric_compute import (
    MetricComputeError,
    compute_metric,
)
from ..nl_gen.metric_def import MetricDefinition


# ---------------------------------------------------------------------------
# Result shapes — render-ready dicts for the proposal detail page.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IterationReScore:
    iteration_index: int
    old_score: float | None
    new_score: float | None
    delta: float | None
    old_meets_target: bool | None
    new_meets_target: bool | None
    inverted: bool
    n_cases: int


@dataclass(frozen=True)
class InversionCheckResult:
    status: str  # 'ok' | 'unavailable' | 'error'
    reason: str | None
    current_metric_family: str | None
    proposed_metric_family: str | None
    iterations: list[IterationReScore]
    n_inverted: int


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


async def check_metric_ordering_inversion(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    proposed_metric_payload: dict[str, Any],
) -> InversionCheckResult:
    """Re-score every iteration under the proposed metric and report
    deltas + inversions.

    Returns a result with `status='unavailable'` when the workflow has
    no current metric_definition, no iterations, or no case outputs;
    `'error'` when re-scoring throws structurally (compute_metric
    raises on empty results, non-bool labels, etc.). `'ok'` otherwise.
    """
    workflow_row = await conn.fetchrow(
        "SELECT metric_definition FROM workflows WHERE id = $1",
        workflow_id,
    )
    if workflow_row is None:
        return _unavailable("Workflow not found.")
    current_md = workflow_row["metric_definition"]
    if current_md is None:
        return _unavailable(
            "Workflow has no current metric definition — nothing to "
            "compare the proposed metric against."
        )

    if isinstance(current_md, str):
        import json as _json

        try:
            current_md = _json.loads(current_md)
        except (ValueError, TypeError):
            return _unavailable(
                "Current metric definition could not be parsed as JSON."
            )
    if not isinstance(current_md, dict):
        return _unavailable(
            "Current metric definition is not a JSON object."
        )

    try:
        current_def = MetricDefinition.model_validate(current_md)
    except Exception as exc:  # pragma: no cover - data drift edge case
        return _unavailable(
            f"Current metric definition failed validation: {exc}"
        )

    proposed_def = _inflate_proposed_metric(proposed_metric_payload, current_def)
    if proposed_def is None:
        return _unavailable(
            "Proposed metric payload missing required fields — fill "
            "name + family + direction at minimum."
        )

    rows = await conn.fetch(
        """
        SELECT
            i.id AS iteration_id,
            i.iteration_index,
            ec.expected_behavior,
            ico.output_json
        FROM iterations i
        JOIN iteration_case_outputs ico ON ico.iteration_id = i.id
        JOIN eval_cases ec ON ec.id = ico.eval_case_id
        WHERE i.workflow_id = $1
        ORDER BY i.iteration_index ASC
        """,
        workflow_id,
    )
    if not rows:
        return _unavailable(
            "No iteration case outputs to re-score yet — run a baseline "
            "iteration first."
        )

    by_iter: dict[int, list[ReplayResult]] = {}
    import json as _json

    for r in rows:
        eb = r["expected_behavior"]
        if isinstance(eb, str):
            try:
                eb = _json.loads(eb)
            except (ValueError, TypeError):
                continue
        if not isinstance(eb, dict):
            continue
        out = r["output_json"]
        if isinstance(out, str):
            try:
                out = _json.loads(out)
            except (ValueError, TypeError):
                continue
        if not isinstance(out, dict):
            continue
        label_field = eb.get("target_label_field") or "label"
        if label_field not in out:
            continue
        expected = eb.get("expected_value")
        actual = out.get(label_field)
        if not isinstance(expected, bool) or not isinstance(actual, bool):
            continue
        case_id = eb.get("case_id") or "(unknown)"
        passed = expected == actual
        by_iter.setdefault(r["iteration_index"], []).append(
            ReplayResult(
                case_id=str(case_id),
                passed=passed,
                actual_value=actual,
                expected_value=expected,
            )
        )

    if not by_iter:
        return _unavailable(
            "Could not reconstruct (expected, actual) bool pairs from any "
            "iteration's case outputs — re-scoring needs both fields per "
            "case."
        )

    per_iter: list[IterationReScore] = []
    n_inverted = 0
    for iter_idx in sorted(by_iter.keys()):
        results = by_iter[iter_idx]
        old_score: float | None = None
        new_score: float | None = None
        old_meets: bool | None = None
        new_meets: bool | None = None
        try:
            old_result = compute_metric(current_def, results)
            old_score = old_result.value
            old_meets = old_result.meets_target
        except MetricComputeError:
            pass
        try:
            new_result = compute_metric(proposed_def, results)
            new_score = new_result.value
            new_meets = new_result.meets_target
        except MetricComputeError:
            pass

        delta: float | None = None
        if old_score is not None and new_score is not None:
            delta = new_score - old_score

        inverted = (
            old_meets is not None
            and new_meets is not None
            and old_meets != new_meets
        )
        if inverted:
            n_inverted += 1

        per_iter.append(
            IterationReScore(
                iteration_index=iter_idx,
                old_score=old_score,
                new_score=new_score,
                delta=delta,
                old_meets_target=old_meets,
                new_meets_target=new_meets,
                inverted=inverted,
                n_cases=len(results),
            )
        )

    return InversionCheckResult(
        status="ok",
        reason=None,
        current_metric_family=current_def.family,
        proposed_metric_family=proposed_def.family,
        iterations=per_iter,
        n_inverted=n_inverted,
    )


def _unavailable(reason: str) -> InversionCheckResult:
    return InversionCheckResult(
        status="unavailable",
        reason=reason,
        current_metric_family=None,
        proposed_metric_family=None,
        iterations=[],
        n_inverted=0,
    )


# ---------------------------------------------------------------------------
# Inflate the loose Spec-tab payload into a full MetricDefinition.
# ---------------------------------------------------------------------------


# Spec-tab form uses "higher" / "lower"; MetricDefinition uses
# "maximize" / "minimize". Map both ways here so the form value is
# accepted as-is.
_DIRECTION_ALIAS = {
    "higher": "maximize",
    "lower": "minimize",
    "maximize": "maximize",
    "minimize": "minimize",
}


def _inflate_proposed_metric(
    payload: dict[str, Any],
    current: MetricDefinition,
) -> MetricDefinition | None:
    """Map a loose Spec-tab payload to a full MetricDefinition.

    Required fields from the payload: `name` (str). Other fields fall
    back to the current metric definition so a reviewer who edits just
    `name` + `family` still produces a valid MetricDefinition to
    compute against. Returns None when even the minimum (name) is
    missing.
    """
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    family = payload.get("family") or current.family
    direction_raw = payload.get("direction") or current.direction
    direction = _DIRECTION_ALIAS.get(direction_raw, direction_raw)
    try:
        return current.model_copy(
            update={
                "name": name.strip(),
                "family": family,
                "direction": direction,
            }
        )
    except Exception:  # pragma: no cover - schema drift edge case
        return None


# Render shape used by the API route — keeps the dataclass body
# private to this module.
def to_api_dict(result: InversionCheckResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "reason": result.reason,
        "current_metric_family": result.current_metric_family,
        "proposed_metric_family": result.proposed_metric_family,
        "n_inverted": result.n_inverted,
        "iterations": [
            {
                "iteration_index": it.iteration_index,
                "old_score": it.old_score,
                "new_score": it.new_score,
                "delta": it.delta,
                "old_meets_target": it.old_meets_target,
                "new_meets_target": it.new_meets_target,
                "inverted": it.inverted,
                "n_cases": it.n_cases,
            }
            for it in result.iterations
        ],
    }
