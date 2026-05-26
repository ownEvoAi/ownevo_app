"""The 10-pair meta-eval set (A4.6).

Each `MetaEvalPair` is one (description, good_bundle, bad_bundle,
ground-truth verdicts, corruption metadata) record. The 10 pairs span
3 production fixtures (demand-prediction, credit-risk-line-recalibration,
union-contract-review) + 7 minimal fixtures, with each of the six
corruption recipes used at least once.

This is the eval set the W4 deliverable A4.6 ships and the W5
deliverable A5.5 grades the judge against (≥0.7 agreement on the
binary `expected_good_verdict` / `expected_bad_verdict`).

Why pre-assembled at module load:

  * Deterministic — same import → same eval set, same pair order, same
    recipe assignment. The runner's per-pair output IDs are stable.
  * One place to read the eval-set composition — anyone reviewing the
    judge can see at a glance which workflow uses which recipe.
  * Cheap — corruption recipes are pure functions over Pydantic
    objects; module import takes single-digit milliseconds.

The eval set is balanced 10 good × 10 bad = 20 evaluations. Recipe
distribution is intentionally uneven (4 recipes used once, 2 recipes
used twice) — uneven is not biased: the judge should still hit ≥0.7
agreement regardless of recipe distribution if its calibration is
honest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..fixtures import (
    CONTRACT_REVIEW_EVAL_CASE_SET,
    CONTRACT_REVIEW_METRIC,
    CONTRACT_REVIEW_SIM_PLAN,
    CONTRACT_REVIEW_SPEC,
    CREDIT_RISK_EVAL_CASE_SET,
    CREDIT_RISK_METRIC,
    CREDIT_RISK_SIM_PLAN,
    CREDIT_RISK_SPEC,
    DEMAND_PREDICTION_EVAL_CASE_SET,
    DEMAND_PREDICTION_METRIC,
    DEMAND_PREDICTION_SIM_PLAN,
    DEMAND_PREDICTION_SPEC,
)
from ..fixtures import (
    DESCRIPTIONS as PROD_DESCRIPTIONS,
)
from .corruptions import (
    Bundle,
    CorruptionDimension,
    CorruptionResult,
    flip_metric_direction,
    set_trivial_threshold,
    set_unreachable_threshold,
    swap_eval_cases,
    swap_metric_family_to_opposing,
    swap_sim_plan,
)
from .fixtures import MINIMAL_BUNDLES, MINIMAL_DESCRIPTIONS
from .fixtures.minimal_fixtures import MinimalBundle


@dataclass(frozen=True)
class MetaEvalPair:
    """One (description, good, bad) evaluation pair with ground truth.

    `pair_id` is the workflow_id (kebab-case) — ties the pair back to
    its source fixture. `expected_good_verdict` / `expected_bad_verdict`
    are the human ground-truth labels A5.5's agreement metric scores
    judge output against.
    """

    pair_id: str
    description: str
    good: Bundle
    bad: Bundle
    bad_recipe_id: str
    bad_target_dimension: CorruptionDimension
    bad_rationale: str
    expected_good_verdict: Literal["good"] = "good"
    expected_bad_verdict: Literal["bad"] = "bad"


def _prod_bundle(spec, plan, case_set, metric) -> Bundle:
    return (spec, plan, case_set, metric)


def _minimal_bundle(b: MinimalBundle) -> Bundle:
    return (b.spec, b.plan, b.case_set, b.metric)


# ---------------------------------------------------------------------------
# Recipe wiring
# ---------------------------------------------------------------------------


def _build_pair(
    pair_id: str,
    description: str,
    good: Bundle,
    bad_corruption: CorruptionResult,
) -> MetaEvalPair:
    return MetaEvalPair(
        pair_id=pair_id,
        description=description,
        good=good,
        bad=bad_corruption.bundle,
        bad_recipe_id=bad_corruption.recipe_id,
        bad_target_dimension=bad_corruption.target_dimension,
        bad_rationale=bad_corruption.rationale,
    )


# Production-fixture good bundles
_PROD_DEMAND = _prod_bundle(
    DEMAND_PREDICTION_SPEC,
    DEMAND_PREDICTION_SIM_PLAN,
    DEMAND_PREDICTION_EVAL_CASE_SET,
    DEMAND_PREDICTION_METRIC,
)
_PROD_CREDIT = _prod_bundle(
    CREDIT_RISK_SPEC,
    CREDIT_RISK_SIM_PLAN,
    CREDIT_RISK_EVAL_CASE_SET,
    CREDIT_RISK_METRIC,
)
_PROD_CONTRACT = _prod_bundle(
    CONTRACT_REVIEW_SPEC,
    CONTRACT_REVIEW_SIM_PLAN,
    CONTRACT_REVIEW_EVAL_CASE_SET,
    CONTRACT_REVIEW_METRIC,
)


# Minimal-fixture good bundles
_MIN_SUPPLIER = _minimal_bundle(MINIMAL_BUNDLES["supplier-late-shipment-risk"])
_MIN_FRAUD = _minimal_bundle(MINIMAL_BUNDLES["fraud-card-decline-review"])
_MIN_CLINICAL = _minimal_bundle(MINIMAL_BUNDLES["clinical-trial-eligibility"])
_MIN_INSURANCE = _minimal_bundle(MINIMAL_BUNDLES["insurance-claim-triage"])
_MIN_HR = _minimal_bundle(MINIMAL_BUNDLES["hr-policy-violation-review"])
_MIN_CONTENT = _minimal_bundle(MINIMAL_BUNDLES["content-moderation-escalation"])
_MIN_MFG = _minimal_bundle(MINIMAL_BUNDLES["manufacturing-defect-detection"])


# ---------------------------------------------------------------------------
# 10 pairs — recipe assignment table
# ---------------------------------------------------------------------------
# Each recipe used at least once:
#  * swap_metric_family_to_opposing — pairs 1, 6
#  * swap_eval_cases — pairs 2, 8
#  * flip_metric_direction — pairs 3, 9
#  * swap_sim_plan — pairs 4, 10
#  * set_unreachable_threshold — pair 5
#  * set_trivial_threshold — pair 7
# Distribution is uneven on purpose — see module docstring.

META_EVAL_SET: list[MetaEvalPair] = [
    # 1. demand-prediction × swap_metric_family
    _build_pair(
        pair_id="demand-prediction",
        description=PROD_DESCRIPTIONS["demand-prediction"],
        good=_PROD_DEMAND,
        bad_corruption=swap_metric_family_to_opposing(_PROD_DEMAND),
    ),
    # 2. credit-risk × swap_eval_cases (donor: contract-review)
    _build_pair(
        pair_id="credit-risk",
        description=PROD_DESCRIPTIONS["credit-risk"],
        good=_PROD_CREDIT,
        bad_corruption=swap_eval_cases(_PROD_CREDIT, CONTRACT_REVIEW_EVAL_CASE_SET),
    ),
    # 3. contract-review × flip_metric_direction
    _build_pair(
        pair_id="contract-review",
        description=PROD_DESCRIPTIONS["contract-review"],
        good=_PROD_CONTRACT,
        bad_corruption=flip_metric_direction(_PROD_CONTRACT),
    ),
    # 4. supplier-late-shipment-risk × swap_sim_plan (donor: clinical)
    _build_pair(
        pair_id="supplier-late-shipment-risk",
        description=MINIMAL_DESCRIPTIONS["supplier-late-shipment-risk"],
        good=_MIN_SUPPLIER,
        bad_corruption=swap_sim_plan(
            _MIN_SUPPLIER, MINIMAL_BUNDLES["clinical-trial-eligibility"].plan
        ),
    ),
    # 5. fraud-card-decline-review × set_unreachable_threshold
    _build_pair(
        pair_id="fraud-card-decline-review",
        description=MINIMAL_DESCRIPTIONS["fraud-card-decline-review"],
        good=_MIN_FRAUD,
        bad_corruption=set_unreachable_threshold(_MIN_FRAUD),
    ),
    # 6. clinical-trial-eligibility × swap_metric_family
    _build_pair(
        pair_id="clinical-trial-eligibility",
        description=MINIMAL_DESCRIPTIONS["clinical-trial-eligibility"],
        good=_MIN_CLINICAL,
        bad_corruption=swap_metric_family_to_opposing(_MIN_CLINICAL),
    ),
    # 7. insurance-claim-triage × set_trivial_threshold
    _build_pair(
        pair_id="insurance-claim-triage",
        description=MINIMAL_DESCRIPTIONS["insurance-claim-triage"],
        good=_MIN_INSURANCE,
        bad_corruption=set_trivial_threshold(_MIN_INSURANCE),
    ),
    # 8. hr-policy-violation-review × swap_eval_cases (donor: content-mod)
    _build_pair(
        pair_id="hr-policy-violation-review",
        description=MINIMAL_DESCRIPTIONS["hr-policy-violation-review"],
        good=_MIN_HR,
        bad_corruption=swap_eval_cases(
            _MIN_HR, MINIMAL_BUNDLES["content-moderation-escalation"].case_set
        ),
    ),
    # 9. content-moderation-escalation × flip_metric_direction
    _build_pair(
        pair_id="content-moderation-escalation",
        description=MINIMAL_DESCRIPTIONS["content-moderation-escalation"],
        good=_MIN_CONTENT,
        bad_corruption=flip_metric_direction(_MIN_CONTENT),
    ),
    # 10. manufacturing-defect-detection × swap_sim_plan (donor: insurance)
    _build_pair(
        pair_id="manufacturing-defect-detection",
        description=MINIMAL_DESCRIPTIONS["manufacturing-defect-detection"],
        good=_MIN_MFG,
        bad_corruption=swap_sim_plan(
            _MIN_MFG, MINIMAL_BUNDLES["insurance-claim-triage"].plan
        ),
    ),
]


__all__ = [
    "MetaEvalPair",
    "META_EVAL_SET",
]
