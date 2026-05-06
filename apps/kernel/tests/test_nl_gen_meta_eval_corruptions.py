"""Tests for `nl_gen.meta_eval.corruptions` (A4.6).

Pins:
  * Each recipe produces a structurally-valid bundle (every artifact
    round-trips through model_validate).
  * Each recipe modifies the intended dimension's surface — the
    semantic flag, not just a no-op rename.
  * Donor-based recipes rewrite back-pointers so the new bundle
    self-validates.
  * Recipe metadata is consistent (`recipe_id`, `target_dimension`,
    rationale non-empty).
"""

from __future__ import annotations

import pytest

from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.nl_gen.meta_eval.corruptions import (
    Bundle,
    CorruptionResult,
    flip_metric_direction,
    set_trivial_threshold,
    set_unreachable_threshold,
    swap_eval_cases,
    swap_metric_family_to_opposing,
    swap_sim_plan,
)
from ownevo_kernel.nl_gen.metric_def import MetricDefinition
from ownevo_kernel.nl_gen.sim_plan import SimulationPlan


def _bundle(workflow_id: str = "demand-prediction") -> Bundle:
    return (
        FIXTURES[workflow_id],
        SIM_PLAN_FIXTURES[workflow_id],
        EVAL_CASE_SET_FIXTURES[workflow_id],
        METRIC_FIXTURES[workflow_id],
    )


def _assert_bundle_validates(b: Bundle) -> None:
    """Every artifact in the bundle round-trips through model_validate."""
    spec, plan, case_set, metric = b
    SimulationPlan.model_validate(plan.model_dump())
    EvalCaseSet.model_validate(case_set.model_dump())
    MetricDefinition.model_validate(metric.model_dump())
    # WorkflowSpec we don't mutate; skip its re-validate to keep the
    # cost down (it's the largest of the four).


# ---------------------------------------------------------------------------
# CorruptionResult metadata invariants
# ---------------------------------------------------------------------------


def test_every_recipe_returns_corruption_result_with_metadata():
    victim = _bundle("demand-prediction")
    donor_plan = SIM_PLAN_FIXTURES["contract-review"]
    donor_cases = EVAL_CASE_SET_FIXTURES["contract-review"]
    results = [
        swap_sim_plan(victim, donor_plan),
        swap_eval_cases(victim, donor_cases),
        swap_metric_family_to_opposing(victim),
        set_unreachable_threshold(victim),
        set_trivial_threshold(victim),
        flip_metric_direction(victim),
    ]
    for r in results:
        assert isinstance(r, CorruptionResult)
        assert r.recipe_id
        assert r.target_dimension in {
            "sim_coverage",
            "eval_case_coverage",
            "metric_alignment",
        }
        assert len(r.rationale) > 10  # non-empty, descriptive


def test_recipe_ids_are_unique_across_module():
    """Eval set assembly uses recipe_id as a key; collisions would
    silently overwrite earlier corruptions."""
    victim = _bundle("demand-prediction")
    donor_plan = SIM_PLAN_FIXTURES["contract-review"]
    donor_cases = EVAL_CASE_SET_FIXTURES["contract-review"]
    ids = {
        swap_sim_plan(victim, donor_plan).recipe_id,
        swap_eval_cases(victim, donor_cases).recipe_id,
        swap_metric_family_to_opposing(victim).recipe_id,
        set_unreachable_threshold(victim).recipe_id,
        set_trivial_threshold(victim).recipe_id,
        flip_metric_direction(victim).recipe_id,
    }
    assert len(ids) == 6


# ---------------------------------------------------------------------------
# swap_sim_plan
# ---------------------------------------------------------------------------


def test_swap_sim_plan_uses_donor_step_code_with_rewired_id():
    victim = _bundle("demand-prediction")
    donor_plan = SIM_PLAN_FIXTURES["contract-review"]
    result = swap_sim_plan(victim, donor_plan)
    spec, new_plan, case_set, metric = result.bundle
    # spec untouched
    assert spec is FIXTURES["demand-prediction"]
    # new plan carries donor's step_code but the victim spec's id
    assert new_plan.step_code == donor_plan.step_code
    assert new_plan.workflow_spec_id == spec.id
    assert result.target_dimension == "sim_coverage"
    _assert_bundle_validates(result.bundle)


# ---------------------------------------------------------------------------
# swap_eval_cases
# ---------------------------------------------------------------------------


def test_swap_eval_cases_rewires_both_back_pointers():
    victim = _bundle("demand-prediction")
    donor_cases = EVAL_CASE_SET_FIXTURES["contract-review"]
    result = swap_eval_cases(victim, donor_cases)
    spec, plan, new_cases, metric = result.bundle
    assert new_cases.workflow_spec_id == spec.id
    assert new_cases.simulation_plan_workflow_id == spec.id
    # Cases themselves are donor's
    donor_case_ids = {c.case_id for c in donor_cases.cases}
    new_case_ids = {c.case_id for c in new_cases.cases}
    assert new_case_ids == donor_case_ids
    assert result.target_dimension == "eval_case_coverage"
    _assert_bundle_validates(result.bundle)


# ---------------------------------------------------------------------------
# swap_metric_family_to_opposing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "workflow_id,expected_flip",
    [
        # demand-prediction is recall → opposing precision
        ("demand-prediction", "precision"),
        # credit-risk is balanced_accuracy → opposing pass_rate
        ("credit-risk", "pass_rate"),
        # contract-review is f1 → opposing pass_rate
        ("contract-review", "pass_rate"),
    ],
)
def test_swap_metric_family_picks_opposing_family(workflow_id, expected_flip):
    victim = _bundle(workflow_id)
    result = swap_metric_family_to_opposing(victim)
    _, _, _, new_metric = result.bundle
    assert new_metric.family == expected_flip
    assert result.target_dimension == "metric_alignment"
    _assert_bundle_validates(result.bundle)


def test_swap_metric_family_is_an_involution_on_recall_precision():
    """recall → precision → recall: the canonical FN/FP-cost flip is
    self-inverse, which keeps the eval set's recipe behavior stable
    across re-runs."""
    victim = _bundle("demand-prediction")
    once = swap_metric_family_to_opposing(victim)
    twice = swap_metric_family_to_opposing(once.bundle)
    assert twice.bundle[3].family == "recall"


# ---------------------------------------------------------------------------
# Threshold corruptions
# ---------------------------------------------------------------------------


def test_set_unreachable_threshold_pins_target_to_upper_bound():
    victim = _bundle("demand-prediction")
    _, _, _, original_metric = victim
    result = set_unreachable_threshold(victim)
    _, _, _, new_metric = result.bundle
    assert new_metric.target_value == new_metric.upper_bound
    assert new_metric.upper_bound == original_metric.upper_bound  # bounds unchanged
    assert result.target_dimension == "metric_alignment"
    _assert_bundle_validates(result.bundle)


def test_set_trivial_threshold_pins_target_to_lower_bound():
    victim = _bundle("demand-prediction")
    _, _, _, original_metric = victim
    result = set_trivial_threshold(victim)
    _, _, _, new_metric = result.bundle
    assert new_metric.target_value == new_metric.lower_bound
    assert new_metric.lower_bound == original_metric.lower_bound
    assert result.target_dimension == "metric_alignment"
    _assert_bundle_validates(result.bundle)


# ---------------------------------------------------------------------------
# flip_metric_direction
# ---------------------------------------------------------------------------


def test_flip_metric_direction_inverts():
    victim = _bundle("demand-prediction")
    _, _, _, original_metric = victim
    assert original_metric.direction == "maximize"  # sanity: fixture is maximize
    result = flip_metric_direction(victim)
    _, _, _, new_metric = result.bundle
    assert new_metric.direction == "minimize"
    assert result.target_dimension == "metric_alignment"
    _assert_bundle_validates(result.bundle)


def test_flip_metric_direction_is_self_inverse():
    victim = _bundle("demand-prediction")
    once = flip_metric_direction(victim)
    twice = flip_metric_direction(once.bundle)
    assert twice.bundle[3].direction == victim[3].direction


# ---------------------------------------------------------------------------
# All recipes preserve immutability of the input victim
# ---------------------------------------------------------------------------


def test_recipes_do_not_mutate_input_bundle():
    """Pydantic frozen=True should make this impossible, but pin it
    explicitly so a future refactor that drops frozen would surface."""
    victim = _bundle("demand-prediction")
    original_metric_family = victim[3].family
    original_metric_target = victim[3].target_value
    original_metric_direction = victim[3].direction
    swap_metric_family_to_opposing(victim)
    set_unreachable_threshold(victim)
    set_trivial_threshold(victim)
    flip_metric_direction(victim)
    # Same victim values still in place
    assert victim[3].family == original_metric_family
    assert victim[3].target_value == original_metric_target
    assert victim[3].direction == original_metric_direction
