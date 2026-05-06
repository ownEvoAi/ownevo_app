"""Tests for `eval_runner.inspect_task.build_inspect_task` (A4.3).

Skipped end-to-end when `inspect-ai` isn't installed (the `eval` extra
is optional). The cross-check failure tests run unconditionally because
they fire before the lazy import.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.eval_runner.inspect_task import build_inspect_task
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    SIM_PLAN_FIXTURES,
)

_inspect_ai = pytest.importorskip(
    "inspect_ai",
    reason="set OWNEVO_EVAL_EXTRA=1 + install ownevo-kernel[eval] to exercise",
)


# ---------------------------------------------------------------------------
# Happy path × 3 fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_build_task_returns_inspect_task(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]

    from inspect_ai import Task

    task = build_inspect_task(case_set, plan, spec)
    assert isinstance(task, Task)


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_dataset_has_one_sample_per_case(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]

    task = build_inspect_task(case_set, plan, spec)
    samples = list(task.dataset)
    assert len(samples) == len(case_set.cases)


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_sample_input_is_case_id_and_target_is_str_bool(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]

    task = build_inspect_task(case_set, plan, spec)
    samples = list(task.dataset)

    for sample, case in zip(samples, case_set.cases):
        assert sample.input == case.case_id
        assert sample.target == str(case.expected_value)


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
def test_sample_metadata_carries_replay_knobs(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]

    task = build_inspect_task(case_set, plan, spec)
    samples = list(task.dataset)

    for sample, case in zip(samples, case_set.cases):
        md = sample.metadata
        assert md["sim_seed"] == case.sim_seed
        assert md["n_steps"] == case.n_steps
        assert md["target_step_index"] == case.target_step_index
        assert md["target_label_field"] == case.target_label_field
        assert md["expected_value"] == case.expected_value
        assert md["is_test_fold"] == case.is_test_fold
        assert md["rationale"] == case.rationale
        assert md["provenance_kind"] == case.provenance.kind
        assert md["provenance_source"] == case.provenance.source
        assert md["workflow_spec_id"] == spec.id


# ---------------------------------------------------------------------------
# Cross-check failure paths
# ---------------------------------------------------------------------------


def test_case_set_workflow_id_mismatch_raises():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["credit-risk"]
    with pytest.raises(ValueError, match="workflow_spec_id"):
        build_inspect_task(case_set, plan, spec)


def test_plan_workflow_id_mismatch_raises():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["credit-risk"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    with pytest.raises(ValueError, match="workflow_spec_id"):
        build_inspect_task(case_set, plan, spec)
