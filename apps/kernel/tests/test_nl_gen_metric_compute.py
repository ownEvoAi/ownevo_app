"""Compute tests for `nl_gen.metric_compute.compute_metric` (A4.2).

Two halves:

  1. Synthetic ReplayResult lists with hand-computed expected values for
     every supported `MetricFamily`. Pins the dispatch byte-identical so
     a future formula edit shows up loudly here.
  2. End-to-end against the matched A4.1 EvalCaseSet fixtures: replay
     every fixture (which all-pass by construction), compute the
     fixture's MetricDefinition over the result list, assert
     value=1.0 + meets_target=True. This is the integration check that
     pins every fixture is internally consistent (workflow ↔ sim ↔
     eval cases ↔ metric all line up).

`MetricComputeError` paths: empty list, non-bool labels.
`_check_against_spec`: id mismatch, direction mismatch.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.nl_gen import (
    MetricComputeError,
    MetricDefinition,
    MetricResult,
    ReplayResult,
    compute_metric,
    replay_set,
)
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.nl_gen.metric_compute import _check_against_spec
from ownevo_kernel.nl_gen.spec import Provenance


def _r(case_id: str, expected: bool, actual: bool) -> ReplayResult:
    return ReplayResult(
        case_id=case_id,
        passed=(expected == actual),
        actual_value=actual,
        expected_value=expected,
    )


def _md(family: str, *, target: float = 0.5, direction: str = "maximize") -> MetricDefinition:
    return MetricDefinition.model_validate(
        {
            "schema_version": "0.1",
            "workflow_spec_id": "demo-workflow",
            "name": f"demo-{family.replace('_', '-')}",
            "family": family,
            "direction": direction,
            "lower_bound": 0.0,
            "upper_bound": 1.0,
            "target_value": target,
            "description": f"Demo metric using family {family}.",
            "rationale": "Synthetic test fixture.",
            "provenance": {"kind": "inferred", "source": "test pattern"},
        }
    )


# ---------------------------------------------------------------------------
# Hand-computed family dispatch
# ---------------------------------------------------------------------------
# Build a fixed 8-case suite: TP=3, TN=2, FP=2, FN=1 → totals = 8
# pass_rate          = (3+2)/8 = 0.625
# precision          = 3/(3+2) = 0.6
# recall             = 3/(3+1) = 0.75
# f1                 = 2*0.6*0.75 / (0.6+0.75) = 0.6666...
# balanced_accuracy  = (recall + specificity) / 2 = (0.75 + 2/(2+2))/2 = 0.625
# specificity        = 2/(2+2) = 0.5

_RESULTS_8 = [
    _r("tp1", True, True),
    _r("tp2", True, True),
    _r("tp3", True, True),
    _r("tn1", False, False),
    _r("tn2", False, False),
    _r("fp1", False, True),
    _r("fp2", False, True),
    _r("fn1", True, False),
]


@pytest.mark.parametrize(
    "family, expected_value",
    [
        ("pass_rate", 5 / 8),
        ("precision", 3 / 5),
        ("recall", 3 / 4),
        ("f1", 2 * (3 / 5) * (3 / 4) / ((3 / 5) + (3 / 4))),
        ("balanced_accuracy", (3 / 4 + 2 / 4) / 2),
        ("specificity", 2 / 4),
    ],
)
def test_family_dispatch_matches_hand_computed_value(family, expected_value):
    result = compute_metric(_md(family), _RESULTS_8)
    assert result.value == pytest.approx(expected_value)


def test_confusion_counts_pinned_for_canonical_suite():
    result = compute_metric(_md("f1"), _RESULTS_8)
    assert (result.tp, result.tn, result.fp, result.fn) == (3, 2, 2, 1)
    assert result.n_total == 8
    assert result.n_pass == 5  # tp + tn


def test_metric_result_carries_definition_metadata():
    md = _md("recall", target=0.9, direction="maximize")
    result = compute_metric(md, _RESULTS_8)
    assert result.metric_name == md.name
    assert result.family == "recall"
    # recall=0.75 < 0.9 → does NOT meet target
    assert result.meets_target is False


def test_meets_target_under_maximize():
    md = _md("recall", target=0.7)
    result = compute_metric(md, _RESULTS_8)  # recall=0.75
    assert result.meets_target is True


def test_meets_target_under_minimize():
    """Defensive: even though all current families are naturally maximize,
    direction=minimize is allowed by the schema. Wire that branch."""
    md = _md("specificity", target=0.6, direction="minimize")  # specificity=0.5 ≤ 0.6
    result = compute_metric(md, _RESULTS_8)
    assert result.meets_target is True


def test_meets_target_at_exact_threshold_under_maximize():
    md = _md("pass_rate", target=5 / 8)
    result = compute_metric(md, _RESULTS_8)
    assert result.meets_target is True
    assert result.degenerate is False


# ---------------------------------------------------------------------------
# Degenerate cases
# ---------------------------------------------------------------------------


def test_no_positive_predictions_precision_returns_zero_and_marks_degenerate():
    """All actual=False → no TP, no FP → precision is undefined. Returns 0.0
    and flags `degenerate=True` so the audit trail can surface it."""
    results = [
        _r("e1", True, False),  # FN
        _r("e2", False, False),  # TN
        _r("e3", False, False),  # TN
    ]
    result = compute_metric(_md("precision"), results)
    assert result.value == 0.0
    assert result.degenerate is True


def test_no_positives_recall_returns_zero_and_marks_degenerate():
    """All expected=False → no positives → recall undefined."""
    results = [
        _r("e1", False, False),
        _r("e2", False, True),
        _r("e3", False, False),
    ]
    result = compute_metric(_md("recall"), results)
    assert result.value == 0.0
    assert result.degenerate is True


def test_no_negatives_specificity_returns_zero_and_marks_degenerate():
    results = [
        _r("e1", True, True),
        _r("e2", True, False),
        _r("e3", True, True),
    ]
    result = compute_metric(_md("specificity"), results)
    assert result.value == 0.0
    assert result.degenerate is True


def test_f1_zero_precision_and_recall_marks_degenerate():
    results = [
        _r("e1", True, False),  # FN
        _r("e2", False, False),  # TN
    ]
    result = compute_metric(_md("f1"), results)
    assert result.value == 0.0
    assert result.degenerate is True


def test_pass_rate_perfect_score_not_degenerate():
    """pass_rate has no zero-division branch — degenerate stays False even
    on all-pass."""
    results = [_r("e1", True, True), _r("e2", False, False)]
    result = compute_metric(_md("pass_rate"), results)
    assert result.value == 1.0
    assert result.degenerate is False


# ---------------------------------------------------------------------------
# MetricComputeError paths
# ---------------------------------------------------------------------------


def test_empty_result_list_raises():
    with pytest.raises(MetricComputeError, match="empty"):
        compute_metric(_md("f1"), [])


def test_non_bool_actual_value_raises():
    bad = ReplayResult(
        case_id="oops",
        passed=False,
        actual_value=1,  # int — not bool
        expected_value=True,
    )
    with pytest.raises(MetricComputeError, match="actual_value"):
        compute_metric(_md("f1"), [bad])


def test_non_bool_expected_value_raises():
    bad = ReplayResult(
        case_id="oops",
        passed=False,
        actual_value=True,
        expected_value="True",  # str — not bool
    )
    with pytest.raises(MetricComputeError, match="expected_value"):
        compute_metric(_md("f1"), [bad])


# ---------------------------------------------------------------------------
# _check_against_spec
# ---------------------------------------------------------------------------


def test_check_against_spec_pass_when_id_and_direction_agree():
    spec = FIXTURES["demand-prediction"]
    md = METRIC_FIXTURES["demand-prediction"]
    _check_against_spec(md, spec)  # no raise


def test_check_against_spec_id_mismatch_raises():
    spec = FIXTURES["demand-prediction"]
    md = METRIC_FIXTURES["credit-risk"]
    with pytest.raises(ValueError, match="workflow_spec_id"):
        _check_against_spec(md, spec)


def test_check_against_spec_direction_mismatch_raises():
    spec = FIXTURES["demand-prediction"]
    md = METRIC_FIXTURES["demand-prediction"].model_copy(
        update={"direction": "minimize"}
    )
    with pytest.raises(ValueError, match="direction"):
        _check_against_spec(md, spec)


# ---------------------------------------------------------------------------
# End-to-end: every fixture replays clean and meets target
# ---------------------------------------------------------------------------
# Closes the loop A4.2 lives inside: workflow → sim → eval set → metric.
# A4.1's eval-case fixtures all-pass under replay against their matched
# sim plans; a metric over an all-pass result list lands at 1.0 for every
# supported family.


@pytest.mark.parametrize("fixture_id", list(METRIC_FIXTURES.keys()))
def test_fixture_metric_over_fixture_replay_value_is_one(fixture_id):
    spec = FIXTURES[fixture_id]
    plan = SIM_PLAN_FIXTURES[fixture_id]
    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    md = METRIC_FIXTURES[fixture_id]

    results = replay_set(case_set, plan, spec)
    metric_result = compute_metric(md, results)

    assert isinstance(metric_result, MetricResult)
    assert metric_result.value == pytest.approx(1.0)


@pytest.mark.parametrize("fixture_id", list(METRIC_FIXTURES.keys()))
def test_fixture_metric_meets_target_on_clean_replay(fixture_id):
    spec = FIXTURES[fixture_id]
    plan = SIM_PLAN_FIXTURES[fixture_id]
    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    md = METRIC_FIXTURES[fixture_id]

    results = replay_set(case_set, plan, spec)
    metric_result = compute_metric(md, results)

    assert metric_result.meets_target is True
    assert metric_result.value >= md.target_value


@pytest.mark.parametrize("fixture_id", list(METRIC_FIXTURES.keys()))
def test_fixture_metric_value_within_definition_bounds(fixture_id):
    spec = FIXTURES[fixture_id]
    plan = SIM_PLAN_FIXTURES[fixture_id]
    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    md = METRIC_FIXTURES[fixture_id]

    metric_result = compute_metric(md, replay_set(case_set, plan, spec))
    assert md.lower_bound <= metric_result.value <= md.upper_bound


@pytest.mark.parametrize("fixture_id", list(METRIC_FIXTURES.keys()))
def test_fixture_metric_replay_n_total_matches_case_count(fixture_id):
    spec = FIXTURES[fixture_id]
    plan = SIM_PLAN_FIXTURES[fixture_id]
    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    md = METRIC_FIXTURES[fixture_id]

    metric_result = compute_metric(md, replay_set(case_set, plan, spec))
    assert metric_result.n_total == len(case_set.cases)


# ---------------------------------------------------------------------------
# Range-check guard
# ---------------------------------------------------------------------------


def test_definition_with_inconsistent_bounds_raises_compute_error():
    """If a future MetricDefinition advertises bounds that don't match the
    family's actual range, compute raises rather than silently returning
    an out-of-bounds value to the gate."""
    md = MetricDefinition.model_validate(
        {
            "schema_version": "0.1",
            "workflow_spec_id": "demo",
            "name": "demo-metric",
            "family": "pass_rate",
            "direction": "maximize",
            "lower_bound": 0.0,
            "upper_bound": 0.5,  # too low for a metric that can hit 1.0
            "target_value": 0.4,
            "description": "Bad bounds intentionally.",
            "rationale": "Test fixture.",
            "provenance": {"kind": "inferred", "source": "test"},
        }
    )
    results = [_r("p1", True, True), _r("p2", True, True), _r("n1", False, False)]
    # All-pass → pass_rate = 1.0, but upper_bound = 0.5 → MetricComputeError
    with pytest.raises(MetricComputeError, match="bounds"):
        compute_metric(md, results)


def test_provenance_object_round_trips_through_compute():
    """Smoke: compute doesn't accidentally drop the provenance subgraph
    when serializing the metric_name into the result."""
    md = METRIC_FIXTURES["demand-prediction"]
    assert isinstance(md.provenance, Provenance)
    # Just exercising — provenance lives on the definition, not the result.
    result = compute_metric(md, _RESULTS_8)
    assert result.metric_name == md.name


# ---------------------------------------------------------------------------
# spec= kwarg: _check_against_spec wired into compute_metric
# ---------------------------------------------------------------------------


def test_compute_metric_with_matching_spec_passes():
    spec = FIXTURES["demand-prediction"]
    md = METRIC_FIXTURES["demand-prediction"]
    results = replay_set(EVAL_CASE_SET_FIXTURES["demand-prediction"], SIM_PLAN_FIXTURES["demand-prediction"], spec)
    result = compute_metric(md, results, spec=spec)
    assert result.meets_target is True


def test_compute_metric_with_mismatched_spec_raises():
    spec = FIXTURES["credit-risk"]
    md = METRIC_FIXTURES["demand-prediction"]
    with pytest.raises(ValueError, match="workflow_spec_id"):
        compute_metric(md, _RESULTS_8, spec=spec)


def test_compute_metric_without_spec_skips_cross_check():
    """Passing spec=None (default) skips _check_against_spec — backward-compatible."""
    md = METRIC_FIXTURES["demand-prediction"]
    result = compute_metric(md, _RESULTS_8)  # no spec — should not raise
    assert isinstance(result.value, float)


# ---------------------------------------------------------------------------
# Float precision: meets_target uses math.isclose for boundary values
# ---------------------------------------------------------------------------


def test_meets_target_at_float_boundary_maximize():
    """f1 computed from integer counts may land at a binary-float value
    that is one ULP away from the stored target. math.isclose covers the gap."""
    # 1 TP, 0 TN, 0 FP, 0 FN → precision=1.0, recall=1.0, f1=1.0 (exact)
    results = [_r("tp1", True, True)]
    md = _md("f1", target=1.0)
    result = compute_metric(md, results)
    assert result.meets_target is True


def test_meets_target_at_float_boundary_minimize():
    """Symmetric check for the minimize direction."""
    results = [_r("tp1", True, False)]  # FN — recall = 0/1 = 0.0
    md = _md("recall", target=0.0, direction="minimize")
    result = compute_metric(md, results)
    assert result.meets_target is True
