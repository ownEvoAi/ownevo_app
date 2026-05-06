"""Tests for `eval_runner.run_replay` (A4.3).

Two kinds of coverage:

  1. End-to-end against the A4.1/A3.2/A4.2 fixture trio — every workflow
     replays clean, the metric scores at 1.0, the report shape is pinned,
     and the per-case outcomes carry `is_test_fold` through verbatim.
  2. Cross-check failure paths — the runner must surface the underlying
     `ValueError` from `replay_set` or `_check_against_spec` so a
     mis-stitched trio fails loudly before reaching the gate.

Inspect AI Task adapter tests live in `test_eval_runner_inspect_task.py`
(import-skipped when the `eval` extra isn't installed).
"""

from __future__ import annotations

import json

import pytest
from ownevo_kernel.eval_runner import (
    EvalCaseOutcome,
    EvalRunReport,
    run_replay,
)
from ownevo_kernel.nl_gen.eval_replay import EvalReplayError
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)


# ---------------------------------------------------------------------------
# Happy path × 3 fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_run_replay_returns_report_for_every_fixture(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = run_replay(case_set, plan, spec, metric)

    assert isinstance(report, EvalRunReport)
    assert report.workflow_spec_id == spec.id
    assert report.metric_name == metric.name
    assert report.metric_family == metric.family
    assert report.direction == metric.direction
    assert report.target_value == metric.target_value


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_fixture_value_is_one_and_meets_target(workflow_id):
    """A4.1 fixtures all-pass under replay → metric value=1.0 → meets_target."""
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = run_replay(case_set, plan, spec, metric)

    assert report.value == pytest.approx(1.0)
    assert report.meets_target is True
    assert report.degenerate is False


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_outcomes_count_matches_case_set(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = run_replay(case_set, plan, spec, metric)

    assert report.n_total == len(case_set.cases)
    assert len(report.outcomes) == len(case_set.cases)
    assert all(isinstance(o, EvalCaseOutcome) for o in report.outcomes)


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_outcomes_all_pass_for_fixture(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = run_replay(case_set, plan, spec, metric)

    assert report.n_pass == report.n_total
    for outcome in report.outcomes:
        assert outcome.passed is True
        assert outcome.actual_value == outcome.expected_value


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_outcome_order_matches_case_order(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = run_replay(case_set, plan, spec, metric)

    assert [o.case_id for o in report.outcomes] == [
        c.case_id for c in case_set.cases
    ]


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_is_test_fold_carries_through(workflow_id):
    """The runner's outcomes must surface is_test_fold so the gate can split
    train/test without re-joining against the source case set."""
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = run_replay(case_set, plan, spec, metric)

    by_id = {o.case_id: o for o in report.outcomes}
    for case in case_set.cases:
        assert by_id[case.case_id].is_test_fold == case.is_test_fold


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_confusion_counts_consistent_with_n_pass(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = run_replay(case_set, plan, spec, metric)

    assert report.tp + report.tn + report.fp + report.fn == report.n_total
    assert report.tp + report.tn == report.n_pass
    # Fixture all-pass → no FP, no FN
    assert report.fp == 0
    assert report.fn == 0


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_to_dict_round_trips_through_json(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = run_replay(case_set, plan, spec, metric)
    payload = report.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    decoded = json.loads(encoded)

    # Fields the gate + audit chain rely on are present.
    for k in (
        "workflow_spec_id", "metric_name", "metric_family", "direction",
        "value", "target_value", "meets_target", "degenerate",
        "n_total", "n_pass", "tp", "tn", "fp", "fn", "outcomes",
    ):
        assert k in decoded


def test_to_dict_outcomes_are_dicts_not_dataclasses():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    report = run_replay(case_set, plan, spec, metric)
    d = report.to_dict()
    assert isinstance(d["outcomes"], list)
    for o in d["outcomes"]:
        assert isinstance(o, dict)
        for k in ("case_id", "expected_value", "actual_value", "passed", "is_test_fold"):
            assert k in o


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_two_runs_produce_identical_report(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    a = run_replay(case_set, plan, spec, metric)
    b = run_replay(case_set, plan, spec, metric)
    assert a == b


# ---------------------------------------------------------------------------
# Cross-check failure paths
# ---------------------------------------------------------------------------


def test_metric_workflow_id_mismatch_raises_value_error():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["credit-risk"]  # wrong workflow

    with pytest.raises(ValueError, match="workflow_spec_id"):
        run_replay(case_set, plan, spec, metric)


def test_metric_direction_mismatch_raises_value_error():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"].model_copy(
        update={"direction": "minimize"}
    )

    with pytest.raises(ValueError, match="direction"):
        run_replay(case_set, plan, spec, metric)


def test_plan_workflow_id_mismatch_raises_value_error():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["credit-risk"]  # wrong plan
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]

    with pytest.raises(ValueError, match="workflow_spec_id"):
        run_replay(case_set, plan, spec, metric)


def test_case_set_workflow_id_mismatch_raises_value_error():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["credit-risk"]  # wrong case set
    metric = METRIC_FIXTURES["demand-prediction"]

    with pytest.raises(ValueError, match="workflow_spec_id"):
        run_replay(case_set, plan, spec, metric)


def test_case_targeting_unknown_label_field_raises_eval_replay_error():
    """A case targeting a label_field absent from the plan must surface as
    EvalReplayError (structural break), not a low metric value."""
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]

    bad_case = case_set.cases[0].model_copy(
        update={"target_label_field": "nonexistent_field"}
    )
    bad_set = case_set.model_copy(
        update={"cases": (bad_case, *case_set.cases[1:])}
    )

    with pytest.raises(EvalReplayError, match="target_label_field"):
        run_replay(bad_set, plan, spec, metric)


# ---------------------------------------------------------------------------
# Inspect AI lazy-import shim
# ---------------------------------------------------------------------------


def test_unknown_attribute_raises_attribute_error():
    """The package's __getattr__ shim only resolves `build_inspect_task` —
    arbitrary attribute access must raise rather than silently return None."""
    import ownevo_kernel.eval_runner as er

    with pytest.raises(AttributeError):
        er.this_does_not_exist  # type: ignore[attr-defined]


def test_build_inspect_task_resolves_through_shim():
    """The lazy shim must resolve `build_inspect_task` to a callable.
    Attribute access via __getattr__ should not raise AttributeError regardless
    of whether inspect-ai is installed (ImportError fires only when the function
    is called, not when the attribute is accessed)."""
    import ownevo_kernel.eval_runner as er

    fn = er.build_inspect_task
    assert callable(fn)
