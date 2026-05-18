"""Corruption recipes for the meta-eval set (A4.6).

Each recipe takes a good NL-gen bundle (WorkflowSpec, SimulationPlan,
EvalCaseSet, MetricDefinition) and returns a structurally-valid but
semantically-wrong variant. The recipes drive the "bad" half of the
judge eval set: paired with a "good" version of the same description,
they let us measure judge-vs-human agreement on the binary
{good, bad} verdict.

Why structurally valid? The judge's contract is *semantic* — it must
catch issues that pass A4.1's `extra='forbid'` + A4.2's direction lock.
Producing bad bundles that fail Pydantic validation would test the
schema layer, not the judge.

Each recipe declares which dimension(s) it primarily targets so the
eval set keeps coverage of all three judge dimensions.

Recipes:

  * `swap_sim_plan` — replace SimulationPlan with one from an
    unrelated workflow (rewriting back-pointers). Targets
    `sim_coverage` — the sim claims to simulate a different domain
    than the description names.
  * `swap_eval_cases` — replace EvalCaseSet with one from an
    unrelated workflow. Targets `eval_case_coverage` — the cases
    test a different decision than the workflow's.
  * `swap_metric_family_to_opposing` — swap the metric family to
    one whose error-mode asymmetry contradicts the past-miss
    framing. Targets `metric_alignment`.
  * `set_unreachable_threshold` — set `target_value` to the upper
    bound (1.0). Targets `metric_alignment`.
  * `set_trivial_threshold` — set `target_value` to the lower
    bound (0.0). Targets `metric_alignment`.
  * `flip_metric_direction` — set `direction` to the opposite of
    the spec's `success_criterion.direction`. Targets
    `metric_alignment` — gate would silently treat regressions as
    wins. The closest analogue to a real-world model regression on
    the prompt-adherence axis.

The judge's three dimensions form a partition of failure modes in
practice; if it can't catch any of these six corruptions reliably,
the agreement gate in A5.5 will fire and we'll know the prompt
needs sharpening.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..eval_case_set import EvalCaseSet
from ..metric_def import MetricDefinition, MetricFamily
from ..sim_plan import SimulationPlan
from ..spec import WorkflowSpec

Bundle = tuple[WorkflowSpec, SimulationPlan, EvalCaseSet, MetricDefinition]
"""4-tuple shape every recipe takes + returns."""


CorruptionDimension = Literal[
    "sim_coverage", "eval_case_coverage", "metric_alignment"
]


@dataclass(frozen=True)
class CorruptionResult:
    """Output of one corruption recipe.

    Carries the corrupted bundle plus metadata the eval set uses to
    build its (description, good, bad, ground_truth, recipe_id) tuples
    and the runner uses to slice judge agreement by recipe.
    """

    bundle: Bundle
    recipe_id: str
    target_dimension: CorruptionDimension
    rationale: str


# ---------------------------------------------------------------------------
# Donor-based recipes
# ---------------------------------------------------------------------------


def swap_sim_plan(victim: Bundle, donor_plan: SimulationPlan) -> CorruptionResult:
    """Replace `victim`'s SimulationPlan with `donor_plan`, rewriting the
    back-pointer to `victim`'s WorkflowSpec id.

    Effect: the bundle claims (via spec) to be about workflow X but
    the simulator implements workflow Y. `sim_coverage` should fail.

    Recipe is robust to donor/victim domain mismatch — that *is* the
    corruption.
    """
    spec, _orig_plan, case_set, metric = victim
    new_plan = donor_plan.model_copy(update={"workflow_spec_id": spec.id})
    # Re-validate via JSON round-trip: model_copy bypasses validators,
    # and we want to fail loudly if the corruption produced an invalid
    # plan (defensive — recipes are meant to keep bundles valid).
    new_plan = SimulationPlan.model_validate(new_plan.model_dump())
    return CorruptionResult(
        bundle=(spec, new_plan, case_set, metric),
        recipe_id="swap_sim_plan",
        target_dimension="sim_coverage",
        rationale=(
            f"SimulationPlan replaced with a donor from workflow "
            f"{donor_plan.workflow_spec_id!r}; the description and "
            f"WorkflowSpec describe a different domain."
        ),
    )


def swap_eval_cases(
    victim: Bundle, donor_case_set: EvalCaseSet
) -> CorruptionResult:
    """Replace `victim`'s EvalCaseSet with `donor_case_set`, rewriting
    both back-pointers (`workflow_spec_id` and `simulation_plan_workflow_id`)
    to `victim`'s WorkflowSpec id.

    Effect: the cases test a decision unrelated to the workflow.
    `eval_case_coverage` should fail.
    """
    spec, plan, _orig_case_set, metric = victim
    new_case_set = donor_case_set.model_copy(
        update={
            "workflow_spec_id": spec.id,
            "simulation_plan_workflow_id": spec.id,
        }
    )
    new_case_set = EvalCaseSet.model_validate(new_case_set.model_dump())
    return CorruptionResult(
        bundle=(spec, plan, new_case_set, metric),
        recipe_id="swap_eval_cases",
        target_dimension="eval_case_coverage",
        rationale=(
            f"EvalCaseSet replaced with a donor from workflow "
            f"{donor_case_set.workflow_spec_id!r}; the cases exercise a "
            f"different decision than the workflow's success criterion."
        ),
    )


# ---------------------------------------------------------------------------
# Metric corruptions (no donor needed)
# ---------------------------------------------------------------------------


# Each family pairs with the family whose error-mode asymmetry it
# contradicts. recall ↔ precision is the canonical pair (FN-cost vs
# FP-cost). f1 ↔ pass_rate is a softer pair (composite vs. uniform);
# we still surface it so the eval set has a recipe that targets
# subtler metric_alignment failures.
_OPPOSING_FAMILY: dict[MetricFamily, MetricFamily] = {
    "recall": "precision",
    "precision": "recall",
    "specificity": "recall",  # FP-cost → FN-cost flip
    "balanced_accuracy": "pass_rate",
    "f1": "pass_rate",
    "pass_rate": "f1",
}


def swap_metric_family_to_opposing(victim: Bundle) -> CorruptionResult:
    """Swap the metric `family` to the one that contradicts the
    workflow's documented past-miss asymmetry.

    Mapping is fixed at module load (`_OPPOSING_FAMILY`). The metric
    definition schema accepts every family; the cross-check that
    catches "wrong family for this past-miss" lives in the judge,
    not in `metric_compute`.
    """
    spec, plan, case_set, metric = victim
    new_family = _OPPOSING_FAMILY[metric.family]
    new_metric = metric.model_copy(update={"family": new_family})
    new_metric = MetricDefinition.model_validate(new_metric.model_dump())
    return CorruptionResult(
        bundle=(spec, plan, case_set, new_metric),
        recipe_id="swap_metric_family_to_opposing",
        target_dimension="metric_alignment",
        rationale=(
            f"Metric family swapped from {metric.family!r} to "
            f"{new_family!r}, contradicting the past-miss asymmetry the "
            f"workflow's success criterion implies."
        ),
    )


def set_unreachable_threshold(victim: Bundle) -> CorruptionResult:
    """Set metric `target_value` to the upper bound (typically 1.0).

    Effect: the gate becomes unclearable — the agent can never produce
    an improvement that meets target. `metric_alignment` should fail
    (the threshold is technically valid but semantically broken).
    """
    spec, plan, case_set, metric = victim
    new_metric = metric.model_copy(update={"target_value": metric.upper_bound})
    new_metric = MetricDefinition.model_validate(new_metric.model_dump())
    return CorruptionResult(
        bundle=(spec, plan, case_set, new_metric),
        recipe_id="set_unreachable_threshold",
        target_dimension="metric_alignment",
        rationale=(
            f"Metric target_value pinned to upper_bound="
            f"{metric.upper_bound}; the gate is unclearable."
        ),
    )


def set_trivial_threshold(victim: Bundle) -> CorruptionResult:
    """Set metric `target_value` to the lower bound (typically 0.0).

    Effect: the gate is trivially passable — every iteration looks
    like an improvement. `metric_alignment` should fail.
    """
    spec, plan, case_set, metric = victim
    new_metric = metric.model_copy(update={"target_value": metric.lower_bound})
    new_metric = MetricDefinition.model_validate(new_metric.model_dump())
    return CorruptionResult(
        bundle=(spec, plan, case_set, new_metric),
        recipe_id="set_trivial_threshold",
        target_dimension="metric_alignment",
        rationale=(
            f"Metric target_value pinned to lower_bound="
            f"{metric.lower_bound}; the gate is trivially clearable."
        ),
    )


def flip_metric_direction(victim: Bundle) -> CorruptionResult:
    """Flip metric `direction` to the opposite of the spec's success criterion.

    Effect: the gate would silently treat regressions as wins. The
    metric definition schema accepts both directions; the cross-check
    in `metric_compute._check_against_spec` would catch this at gate
    time, but the meta-eval is supposed to catch it at generate-time.
    """
    spec, plan, case_set, metric = victim
    flipped = "minimize" if metric.direction == "maximize" else "maximize"
    new_metric = metric.model_copy(update={"direction": flipped})
    new_metric = MetricDefinition.model_validate(new_metric.model_dump())
    return CorruptionResult(
        bundle=(spec, plan, case_set, new_metric),
        recipe_id="flip_metric_direction",
        target_dimension="metric_alignment",
        rationale=(
            f"Metric direction flipped from {metric.direction!r} to "
            f"{flipped!r}; the gate would treat regressions as "
            f"improvements."
        ),
    )


__all__ = [
    "Bundle",
    "CorruptionDimension",
    "CorruptionResult",
    "swap_sim_plan",
    "swap_eval_cases",
    "swap_metric_family_to_opposing",
    "set_unreachable_threshold",
    "set_trivial_threshold",
    "flip_metric_direction",
]
