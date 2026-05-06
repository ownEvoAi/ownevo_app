"""Tests for `nl_gen.meta_eval.eval_set` (A4.6).

Pins the A4.6 deliverable's structural contract:

  * ≥10 pairs (the PLAN.md threshold).
  * Every pair's good and bad bundles round-trip through Pydantic.
  * Every recipe is used at least once (so the eval set exercises
    every corruption path the judge needs to detect).
  * Every pair carries (description, good, bad, ground-truth verdicts,
    recipe metadata).
  * pair_ids are unique (the runner uses pair_id as the key).
  * Per-pair description is non-empty.
"""

from __future__ import annotations

from collections import Counter

import pytest

from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet
from ownevo_kernel.nl_gen.meta_eval.eval_set import (
    META_EVAL_SET,
    MetaEvalPair,
)
from ownevo_kernel.nl_gen.meta_eval.fixtures import MINIMAL_BUNDLES
from ownevo_kernel.nl_gen.metric_def import MetricDefinition
from ownevo_kernel.nl_gen.sim_plan import SimulationPlan


def _all_recipe_ids() -> set[str]:
    return {
        "swap_sim_plan",
        "swap_eval_cases",
        "swap_metric_family_to_opposing",
        "set_unreachable_threshold",
        "set_trivial_threshold",
        "flip_metric_direction",
    }


def _all_dimensions() -> set[str]:
    return {"sim_coverage", "eval_case_coverage", "metric_alignment"}


# ---------------------------------------------------------------------------
# Cardinality + structural shape
# ---------------------------------------------------------------------------


def test_eval_set_meets_size_threshold():
    """PLAN.md A4.6 requires ≥10 (description, good, bad) pairs."""
    assert len(META_EVAL_SET) >= 10


def test_every_pair_carries_required_metadata():
    for p in META_EVAL_SET:
        assert isinstance(p, MetaEvalPair)
        assert p.pair_id
        assert p.description
        assert p.bad_recipe_id
        assert p.bad_target_dimension in _all_dimensions()
        assert p.bad_rationale
        assert p.expected_good_verdict == "good"
        assert p.expected_bad_verdict == "bad"


def test_pair_ids_are_unique():
    """Runner uses pair_id as the result key — collisions would silently
    overwrite earlier judgments."""
    ids = [p.pair_id for p in META_EVAL_SET]
    assert len(ids) == len(set(ids))


def test_every_pair_has_4_tuple_bundles():
    for p in META_EVAL_SET:
        assert len(p.good) == 4
        assert len(p.bad) == 4


# ---------------------------------------------------------------------------
# Bundle validity (good and bad both round-trip)
# ---------------------------------------------------------------------------


def test_every_good_bundle_round_trips():
    """A4.6 contract: corruption recipes produce structurally valid
    bundles. The "bad" bundle should pass model_validate just like good."""
    for p in META_EVAL_SET:
        spec, plan, case_set, metric = p.good
        SimulationPlan.model_validate(plan.model_dump())
        EvalCaseSet.model_validate(case_set.model_dump())
        MetricDefinition.model_validate(metric.model_dump())


def test_every_bad_bundle_round_trips():
    for p in META_EVAL_SET:
        spec, plan, case_set, metric = p.bad
        SimulationPlan.model_validate(plan.model_dump())
        EvalCaseSet.model_validate(case_set.model_dump())
        MetricDefinition.model_validate(metric.model_dump())


def test_every_bad_bundle_back_pointers_match_spec_id():
    """After corruption, plan.workflow_spec_id and case_set.workflow_spec_id
    still match spec.id — otherwise the bundle is incoherent in a way the
    judge would correctly reject but for the wrong reason."""
    for p in META_EVAL_SET:
        spec, plan, case_set, metric = p.bad
        assert plan.workflow_spec_id == spec.id
        assert case_set.workflow_spec_id == spec.id
        assert case_set.simulation_plan_workflow_id == spec.id
        assert metric.workflow_spec_id == spec.id


# ---------------------------------------------------------------------------
# Recipe coverage
# ---------------------------------------------------------------------------


def test_every_corruption_recipe_used_at_least_once():
    """The judge has to catch every corruption mode — if a recipe is
    never exercised in the eval set, judge agreement on it isn't
    measured."""
    used = {p.bad_recipe_id for p in META_EVAL_SET}
    assert _all_recipe_ids() <= used


def test_recipe_distribution_is_documented():
    """Snapshot the recipe distribution. If a future edit changes it,
    bump this assertion intentionally rather than letting it drift."""
    dist = Counter(p.bad_recipe_id for p in META_EVAL_SET)
    assert dist == Counter(
        {
            "swap_sim_plan": 2,
            "swap_eval_cases": 2,
            "swap_metric_family_to_opposing": 2,
            "flip_metric_direction": 2,
            "set_unreachable_threshold": 1,
            "set_trivial_threshold": 1,
        }
    )


def test_dimension_distribution_skews_to_metric_alignment():
    """4 of 6 recipes target metric_alignment, so the eval set is
    expected to skew that way. Sanity-pin the skew so a recipe
    refactor that loses sim_coverage / eval_case_coverage coverage is
    visible in CI."""
    dist = Counter(p.bad_target_dimension for p in META_EVAL_SET)
    assert dist["sim_coverage"] >= 2
    assert dist["eval_case_coverage"] >= 2
    assert dist["metric_alignment"] >= 4


# ---------------------------------------------------------------------------
# Production + minimal fixtures both represented
# ---------------------------------------------------------------------------


def test_production_fixtures_represented():
    """The 3 production fixtures (A4.1-A4.4) should anchor the eval set —
    they are the highest-fidelity bundles available."""
    pair_ids = {p.pair_id for p in META_EVAL_SET}
    for prod_id in ("demand-prediction", "credit-risk", "contract-review"):
        assert prod_id in pair_ids


def test_all_minimal_fixtures_represented():
    """Every minimal fixture should appear — otherwise authoring it was
    wasted effort."""
    pair_ids = {p.pair_id for p in META_EVAL_SET}
    for minimal_id in MINIMAL_BUNDLES:
        assert minimal_id in pair_ids


# ---------------------------------------------------------------------------
# Description sanity
# ---------------------------------------------------------------------------


def test_descriptions_are_non_empty_and_distinct():
    descriptions = [p.description for p in META_EVAL_SET]
    for d in descriptions:
        assert len(d.strip()) > 50  # non-trivial prose
    assert len(set(descriptions)) == len(descriptions)
