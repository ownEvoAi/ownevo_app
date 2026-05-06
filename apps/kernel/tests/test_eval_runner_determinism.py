"""Tests for `eval_runner.determinism` (A4.5).

Pins:

  * Happy path: every fixture trio passes verify_determinism — the
    deterministic replay path IS deterministic on every shipped
    workflow. (This is the validation hook PLAN.md A4.5 calls for.)
  * `verify_determinism` returns an `EvalRunReport` shaped identically
    to `run_replay`, so it's a drop-in replacement.
  * Outcome-count divergence raises with kind=outcome_count.
  * Per-case actual_value divergence raises with kind=actual_value
    naming the case.
  * Per-case passed flag divergence raises with kind=passed.
  * Confusion-count divergence raises with kind=count:<field>.
  * Metric-value divergence beyond tolerance raises with kind=metric_value.
  * Equal metric values within `1e-9` are accepted.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from ownevo_kernel.eval_runner import (
    EvalCaseOutcome,
    EvalRunReport,
    NondeterminismError,
    verify_determinism,
)
from ownevo_kernel.eval_runner.determinism import (
    METRIC_VALUE_TOLERANCE,
    compare_reports,
)
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)


WORKFLOW_IDS = sorted(FIXTURES.keys())


# ---------------------------------------------------------------------------
# Happy path on real fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", WORKFLOW_IDS)
def test_verify_determinism_passes_on_every_fixture(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = verify_determinism(case_set, plan, spec, metric)

    assert isinstance(report, EvalRunReport)
    assert report.workflow_spec_id == spec.id
    # Fixtures pin replay-equivalence: every case's actual matches expected.
    assert report.meets_target is True


@pytest.mark.parametrize("workflow_id", WORKFLOW_IDS)
def test_verify_determinism_byte_equal_to_run_replay(workflow_id):
    """The returned report is structurally identical to run_replay's
    output — same workflow_spec_id, same metric, same outcomes."""
    from ownevo_kernel.eval_runner import run_replay

    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    a = run_replay(case_set, plan, spec, metric)
    b = verify_determinism(case_set, plan, spec, metric)

    assert a.to_dict() == b.to_dict()


# ---------------------------------------------------------------------------
# Hand-crafted divergence reports — drive compare_reports directly
# ---------------------------------------------------------------------------


def _outcome(case_id: str, *, actual: Any, expected: bool, passed: bool) -> EvalCaseOutcome:
    return EvalCaseOutcome(
        case_id=case_id,
        expected_value=expected,
        actual_value=actual,
        passed=passed,
        is_test_fold=False,
    )


def _make_report(
    *,
    outcomes: tuple[EvalCaseOutcome, ...],
    value: float = 1.0,
    tp: int = 1,
    tn: int = 1,
    fp: int = 0,
    fn: int = 0,
    n_total: int = 2,
    n_pass: int = 2,
) -> EvalRunReport:
    return EvalRunReport(
        workflow_spec_id="wf",
        metric_name="m",
        metric_family="recall",
        direction="maximize",
        value=value,
        target_value=0.5,
        meets_target=value >= 0.5,
        degenerate=False,
        n_total=n_total,
        n_pass=n_pass,
        tp=tp,
        tn=tn,
        fp=fp,
        fn=fn,
        outcomes=outcomes,
    )


def _baseline_pair() -> tuple[EvalRunReport, EvalRunReport]:
    outcomes = (
        _outcome("c1", actual=True, expected=True, passed=True),
        _outcome("c2", actual=False, expected=False, passed=True),
    )
    a = _make_report(outcomes=outcomes)
    b = _make_report(outcomes=outcomes)
    return a, b


def test_compare_reports_identical_passes():
    a, b = _baseline_pair()
    compare_reports(a, b)  # does not raise


def test_compare_reports_outcome_count_mismatch_raises():
    a, b = _baseline_pair()
    b = dataclasses.replace(b, outcomes=b.outcomes[:1])
    with pytest.raises(NondeterminismError) as excinfo:
        compare_reports(a, b)
    assert excinfo.value.kind == "outcome_count"
    assert excinfo.value.case_id is None


def test_compare_reports_case_id_order_diverges_raises():
    a, b = _baseline_pair()
    b = dataclasses.replace(b, outcomes=tuple(reversed(b.outcomes)))
    with pytest.raises(NondeterminismError) as excinfo:
        compare_reports(a, b)
    assert excinfo.value.kind == "case_id_order"


def test_compare_reports_actual_value_divergence_raises():
    a, _ = _baseline_pair()
    flipped = (
        _outcome("c1", actual=False, expected=True, passed=False),
        a.outcomes[1],
    )
    b = _make_report(outcomes=flipped, n_pass=1, tp=0, fn=1)
    with pytest.raises(NondeterminismError) as excinfo:
        compare_reports(a, b)
    err = excinfo.value
    assert err.kind == "actual_value"
    assert err.case_id == "c1"
    assert err.run1_value is True
    assert err.run2_value is False


def test_compare_reports_passed_flag_divergence_raises():
    """Synthetic: same actual_value but flipped `passed`. Should be
    unreachable in practice but pinned so a future change in the pass
    predicate can't slip through."""
    a, _ = _baseline_pair()
    diverged = (
        _outcome("c1", actual=True, expected=True, passed=False),
        a.outcomes[1],
    )
    b = _make_report(outcomes=diverged, n_pass=1)
    with pytest.raises(NondeterminismError) as excinfo:
        compare_reports(a, b)
    assert excinfo.value.kind == "passed"
    assert excinfo.value.case_id == "c1"


def test_compare_reports_confusion_count_divergence_raises():
    a, b = _baseline_pair()
    b = dataclasses.replace(b, tp=999)  # Only the count differs.
    with pytest.raises(NondeterminismError) as excinfo:
        compare_reports(a, b)
    assert excinfo.value.kind == "count:tp"


def test_compare_reports_metric_value_divergence_raises():
    a, _ = _baseline_pair()
    b = _make_report(outcomes=a.outcomes, value=0.0)
    with pytest.raises(NondeterminismError) as excinfo:
        compare_reports(a, b)
    assert excinfo.value.kind == "metric_value"


def test_compare_reports_within_tolerance_accepted():
    a, _ = _baseline_pair()
    # Tiny delta well under 1e-9.
    b = _make_report(outcomes=a.outcomes, value=1.0 + METRIC_VALUE_TOLERANCE / 10)
    compare_reports(a, b)  # does not raise


def test_compare_reports_just_over_tolerance_rejected():
    a, _ = _baseline_pair()
    b = _make_report(outcomes=a.outcomes, value=1.0 + METRIC_VALUE_TOLERANCE * 10)
    with pytest.raises(NondeterminismError) as excinfo:
        compare_reports(a, b)
    assert excinfo.value.kind == "metric_value"


def test_compare_reports_empty_outcomes_passes():
    """Zero-case replay: zip short-circuits, falls through to count + metric checks."""
    a = _make_report(outcomes=(), tp=0, tn=0, fp=0, fn=0, n_total=0, n_pass=0, value=0.0)
    b = _make_report(outcomes=(), tp=0, tn=0, fp=0, fn=0, n_total=0, n_pass=0, value=0.0)
    compare_reports(a, b)  # does not raise
