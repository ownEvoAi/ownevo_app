"""Hand-authored MetricDefinition fixtures for the 3 A4.2 workflows.

These are the structural ground-truth the schema, generator, and compute
tests check against — same role as `eval_case_sets.py` for A4.1:

  * Schema-only round-trip tests prove the fixtures parse and re-serialize.
  * Compute tests run `compute_metric(fixture, replay_set(...))` against
    the matched A4.1 EvalCaseSet (which the A4.1 fixtures pin to all-pass
    under their matched A3.2 SimulationPlan) and assert the resulting
    value lands inside `[lower_bound, upper_bound]` and meets `target_value`.
  * Generator tests use these as the scripted tool-output payload.

Family choice rationale (per PLAN.md A4.2 — pick the family that fits
the workflow's documented past-miss asymmetry):

  * demand-prediction  → `recall`. Past miss "we missed the 2025 Pacific
    NW winter boot spike by 4 weeks" is the canonical false-negative —
    the positive class (markdown-needed) was missed. Recall is the gate
    signal that catches that regression directly.
  * credit-risk        → `balanced_accuracy`. The success_criterion calls
    out both breach-rate (false negatives on risk) AND over-tightening
    false-positive rate (false positives on healthy accounts). With
    asymmetric class costs but no clear directional asymmetry, balanced
    accuracy keeps both classes honest.
  * contract-review    → `f1`. Success_criterion explicitly says
    "Composite of precision and recall over flagged-clause set" — f1
    is the canonical harmonic-mean composite of both.

Target thresholds are deliberately well under 1.0 (typically 0.75-0.85)
so the gate has both reach and headroom: a metric stuck at 1.0 in the
fixtures never proves the gate's threshold check fires; one stuck at
0.0 never proves it's reachable. The all-pass eval-case fixtures land
above these thresholds, so `meets_target=True` is the expected
fixture-time invariant.
"""

from __future__ import annotations

from ..metric_def import MetricDefinition
from ..spec import Provenance

# ---------------------------------------------------------------------------
# Demand prediction
# ---------------------------------------------------------------------------
# Past miss "missed the 2025 Pacific NW winter boot spike by 4 weeks" is
# a false-negative on the positive class — recall is the metric that
# catches that exact regression.

DEMAND_PREDICTION_METRIC = MetricDefinition(
    workflow_spec_id="supply-chain-demand-forecast",
    name="markdown-alert-recall",
    family="recall",
    direction="maximize",
    lower_bound=0.0,
    upper_bound=1.0,
    target_value=0.80,
    description=(
        "Recall on markdown-needed weeks: the fraction of weeks that "
        "actually needed a markdown alert that the agent successfully "
        "fired one for. Targets 80% — high enough that the past-miss "
        "regressions don't slip through, low enough to leave the gate "
        "headroom while the suite is still small."
    ),
    rationale=(
        "Past miss 'missed the 2025 Pacific NW winter boot spike by 4 "
        "weeks' is a false negative — recall is the family that scores "
        "exactly that error mode."
    ),
    provenance=Provenance(
        kind="derived",
        source="missed the 2025 Pacific NW winter boot spike by 4 weeks",
    ),
)

# ---------------------------------------------------------------------------
# Credit risk
# ---------------------------------------------------------------------------
# Success criterion explicitly names both directions — breach-rate (FN
# on risky accounts) AND over-tightening false-positive rate (FP on
# healthy accounts). Balanced accuracy keeps both classes honest under
# class imbalance.

CREDIT_RISK_METRIC = MetricDefinition(
    workflow_spec_id="credit-risk-line-recalibration",
    name="line-recalibration-balanced-accuracy",
    family="balanced_accuracy",
    direction="maximize",
    lower_bound=0.0,
    upper_bound=1.0,
    target_value=0.75,
    description=(
        "Balanced accuracy across the default/no-default classes: the "
        "average of recall on accounts that defaulted and specificity "
        "on accounts that did not. Targets 75% so the gate catches "
        "both breach-rate regressions and over-tightening "
        "false-positive regressions symmetrically."
    ),
    rationale=(
        "Success criterion names both breach-rate AND over-tightening "
        "false-positive rate — balanced accuracy is the symmetric "
        "composite that holds both directions accountable."
    ),
    provenance=Provenance(
        kind="derived",
        source=(
            "Composite of breach-rate, default-rate, and over-tightening "
            "false-positive rate."
        ),
    ),
)

# ---------------------------------------------------------------------------
# Contract review
# ---------------------------------------------------------------------------
# Success criterion: "Composite of precision and recall over
# flagged-clause set" — f1 is the canonical harmonic-mean composite.

CONTRACT_REVIEW_METRIC = MetricDefinition(
    workflow_spec_id="union-contract-review",
    name="clause-flag-f1",
    family="f1",
    direction="maximize",
    lower_bound=0.0,
    upper_bound=1.0,
    target_value=0.75,
    description=(
        "F1 over the flagged-clause set: the harmonic mean of "
        "precision (flagged clauses that legal actually redlines) and "
        "recall (problematic clauses the agent caught). Targets 75% to "
        "balance reviewer trust (precision) against catch rate (recall) "
        "while leaving gate headroom."
    ),
    rationale=(
        "Success criterion explicitly says 'Composite of precision and "
        "recall over flagged-clause set' — f1 is the canonical "
        "harmonic-mean composite."
    ),
    provenance=Provenance(
        kind="derived",
        source=(
            "Composite of precision and recall over flagged-clause set, "
            "weighted by clause severity."
        ),
    ),
)


METRIC_FIXTURES = {
    "demand-prediction": DEMAND_PREDICTION_METRIC,
    "credit-risk": CREDIT_RISK_METRIC,
    "contract-review": CONTRACT_REVIEW_METRIC,
}


__all__ = [
    "DEMAND_PREDICTION_METRIC",
    "CREDIT_RISK_METRIC",
    "CONTRACT_REVIEW_METRIC",
    "METRIC_FIXTURES",
]
