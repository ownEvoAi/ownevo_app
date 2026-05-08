"""Hand-authored eval fixtures for the W5.2 LLM-judge stub approver.

30 `LabeledApprovalCase` fixtures spanning four buckets:

  * `structural` (10 cases) — all three elements present + consistent;
    ground truth `admit`. The "good explanations" the production agent
    loop should be able to write.
  * `vague-but-positive` (8 cases) — generic optimism, no cluster
    reference, no specific change, no direction; ground truth
    `reject`. The dominant adversarial mode.
  * `structural-but-wrong-direction` (6 cases) — names cluster + names
    change but states a direction that contradicts the cluster's bias
    (e.g., under-forecast cluster + 'reduces forecast'); ground truth
    `reject`. Tests whether the judge catches structural shells with
    semantic contradictions.
  * `hand-wavy` (6 cases) — partial coverage: one or two elements
    present, others missing or vague; ground truth `reject`. Mid-
    quality explanations the judge has to draw a line under.

Why 30 (vs 5 / 20 / 100): per the eng-review expansion (PLAN.md
v3.5), 5 is too noisy for a 0.85 agreement gate (one disagreement =
−0.20), and 100 is more cost-per-CI-run than the W5.2 surface
warrants. 30 keeps a single agreement run under ~$0.40 on opus
4.7 while bracketing the four failure modes with ≥6 each.

`proposal_summary` is a short technical description of what the
change does (the gate's `plain_language_summary` would be longer in
production; we keep these short for fixture readability). `cluster_name`
is the conventional cluster name the change addresses (used by the
judge to ground the cluster-reference check). `metric_direction_expected`
is the direction the change *should* move the metric — the judge
compares this against what the explanation actually claims.

`bucket` lets the runner slice agreement by failure mode so a
regression on the wrong-direction bucket doesn't average out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LabeledApprovalCase:
    """One hand-labeled (proposal, explanation, ground_truth) fixture.

    `case_id` is a kebab-case stable identifier the judge echoes back.
    `proposal_summary` is the technical description of the change
    (1-2 sentences). `cluster_name` is the failure cluster the change
    addresses (the explanation should reference this — synonyms /
    paraphrases count). `metric_direction_expected` is the direction
    the change *ought* to move the success metric.

    `explanation` is the plain-English explanation under test —
    typically 1-3 sentences, the same shape the production agent loop
    would write. `ground_truth_verdict` is the human label.
    """

    case_id: str
    proposal_summary: str
    cluster_name: str
    metric_direction_expected: Literal["up", "down"]
    explanation: str
    ground_truth_verdict: Literal["admit", "reject"]
    bucket: Literal[
        "structural",
        "vague-but-positive",
        "structural-but-wrong-direction",
        "hand-wavy",
    ]
    notes: str | None = None


# ---------------------------------------------------------------------------
# Structural — all three elements present + consistent → admit
# ---------------------------------------------------------------------------

_STRUCTURAL_CASES = (
    LabeledApprovalCase(
        case_id="struct-01-weekend-snack",
        proposal_summary=(
            "Add a weekend-bias correction to the snack-category demand "
            "forecaster: blend in a 12-week trailing weekend-only mean."
        ),
        cluster_name="CA snack weekend under-forecasts",
        metric_direction_expected="up",
        explanation=(
            "This addresses the CA snack weekend under-forecast cluster "
            "by adding a 12-week trailing weekend-only mean to the "
            "blender. We expect recall on the weekend test cases to go up."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
        notes=(
            "Names cluster ('CA snack weekend under-forecast'), names "
            "change ('12-week trailing weekend-only mean'), names "
            "direction ('recall ... go up'). Consistent."
        ),
    ),
    LabeledApprovalCase(
        case_id="struct-02-tx-cleaning-over",
        proposal_summary=(
            "Tighten the over-forecast threshold on TX HOUSEHOLD_1 "
            "items from 1.5x to 1.2x baseline."
        ),
        cluster_name="TX cleaning supply over-forecasts",
        metric_direction_expected="down",
        explanation=(
            "Tightens the over-forecast threshold on TX HOUSEHOLD_1 from "
            "1.5x to 1.2x baseline to address the TX cleaning over-"
            "forecast cluster. Should bring false-alert rate down."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
        notes="Cluster, change, direction all present and consistent.",
    ),
    LabeledApprovalCase(
        case_id="struct-03-zero-inflated",
        proposal_summary=(
            "Add a zero-inflated Poisson head to the WI HOBBIES "
            "predictor for low-velocity items."
        ),
        cluster_name="WI hobbies zero-inflated under-forecasts",
        metric_direction_expected="up",
        explanation=(
            "Targets the WI hobbies zero-inflated cluster by adding a "
            "zero-inflated Poisson head for low-velocity items. "
            "Forecast accuracy on the zero-rich test set should "
            "improve."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
        notes="Names the cluster + the change + that accuracy should improve.",
    ),
    LabeledApprovalCase(
        case_id="struct-04-flat-prediction",
        proposal_summary=(
            "Add lag-7 + lag-14 features to break the constant-prediction "
            "behaviour on FOODS_1 weekday items."
        ),
        cluster_name="FOODS_1 weekday flat-prediction failures",
        metric_direction_expected="up",
        explanation=(
            "The FOODS_1 weekday flat-prediction cluster is caused by "
            "the model emitting constants. Adding lag-7 + lag-14 "
            "features breaks that. We expect WRMSSE to improve on "
            "the affected test fold."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
    ),
    LabeledApprovalCase(
        case_id="struct-05-hurricane-southeast",
        proposal_summary=(
            "Add a hurricane-evacuation indicator to the SE region "
            "demand forecaster, sourced from NOAA storm-tracking feed."
        ),
        cluster_name="Southeast hurricane displacement misses",
        metric_direction_expected="up",
        explanation=(
            "This adds a hurricane-evacuation indicator from the NOAA "
            "storm feed to address the Southeast hurricane "
            "displacement cluster. Markdown-alert recall on storm "
            "weeks should go up."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
    ),
    LabeledApprovalCase(
        case_id="struct-06-credit-thinfile",
        proposal_summary=(
            "Add bureau-derived 'time at current address' as a feature "
            "for thin-file applicants in the credit-risk classifier."
        ),
        cluster_name="thin-file applicant misclassifications",
        metric_direction_expected="up",
        explanation=(
            "Targets the thin-file applicant misclassification cluster "
            "by adding bureau-derived 'time at current address' as a "
            "feature. Recall on the thin-file holdout fold should "
            "improve."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
    ),
    LabeledApprovalCase(
        case_id="struct-07-self-employed",
        proposal_summary=(
            "Use a 24-month income-volatility window (vs 12-month) "
            "for self-employed applicants in the credit-risk model."
        ),
        cluster_name="self-employed income volatility misses",
        metric_direction_expected="up",
        explanation=(
            "Addresses the self-employed income volatility cluster by "
            "switching to a 24-month volatility window. F-beta=2 "
            "should go up on the self-employed eval slice."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
    ),
    LabeledApprovalCase(
        case_id="struct-08-jurisdictional",
        proposal_summary=(
            "Add CA + NY jurisdictional-carve-out clause detector to "
            "the contract-review pipeline."
        ),
        cluster_name="jurisdictional carve-out misses",
        metric_direction_expected="up",
        explanation=(
            "Addresses the jurisdictional carve-out miss cluster by "
            "adding a CA + NY clause detector. Clause-coverage F1 "
            "should go up on the jurisdictional test slice."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
    ),
    LabeledApprovalCase(
        case_id="struct-09-grievance-precedent",
        proposal_summary=(
            "Index the prior-grievance corpus and surface top-3 "
            "matches per new contract clause."
        ),
        cluster_name="grievance precedent overlooked",
        metric_direction_expected="up",
        explanation=(
            "Addresses the grievance-precedent overlooked cluster by "
            "indexing the prior-grievance corpus and surfacing top-3 "
            "matches per clause. Precedent-recall on the historical "
            "grievance set should go up."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
    ),
    LabeledApprovalCase(
        case_id="struct-10-promo-bundle",
        proposal_summary=(
            "Add bundle-uplift coefficient (item-level, learned per "
            "promo type) to the demand model."
        ),
        cluster_name="promotional uplift under-weighted",
        metric_direction_expected="up",
        explanation=(
            "Addresses the under-weighted promotional uplift cluster "
            "by learning a bundle-uplift coefficient per promo type. "
            "Recall on Q4 promo-bundle eval cases should improve."
        ),
        ground_truth_verdict="admit",
        bucket="structural",
    ),
)


# ---------------------------------------------------------------------------
# Vague-but-positive — generic optimism, no structure → reject
# ---------------------------------------------------------------------------

_VAGUE_CASES = (
    LabeledApprovalCase(
        case_id="vague-01-improvements",
        proposal_summary=(
            "Refactor the snack forecaster to use a unified blender."
        ),
        cluster_name="CA snack weekend under-forecasts",
        metric_direction_expected="up",
        explanation=(
            "I made some improvements to the forecaster. This should "
            "make things better."
        ),
        ground_truth_verdict="reject",
        bucket="vague-but-positive",
        notes=(
            "Doesn't reference the cluster, doesn't name the change, "
            "doesn't state a direction."
        ),
    ),
    LabeledApprovalCase(
        case_id="vague-02-cleaner-code",
        proposal_summary=(
            "Tighten the over-forecast threshold on TX HOUSEHOLD_1."
        ),
        cluster_name="TX cleaning supply over-forecasts",
        metric_direction_expected="down",
        explanation=(
            "This is cleaner code than what we had before. The change "
            "should help."
        ),
        ground_truth_verdict="reject",
        bucket="vague-but-positive",
    ),
    LabeledApprovalCase(
        case_id="vague-03-handle-better",
        proposal_summary=(
            "Add zero-inflated Poisson head for low-velocity hobbies items."
        ),
        cluster_name="WI hobbies zero-inflated under-forecasts",
        metric_direction_expected="up",
        explanation=(
            "This is a better approach to handle these cases. Should "
            "be a positive."
        ),
        ground_truth_verdict="reject",
        bucket="vague-but-positive",
    ),
    LabeledApprovalCase(
        case_id="vague-04-robust",
        proposal_summary=(
            "Add lag-7 + lag-14 features to FOODS_1 forecaster."
        ),
        cluster_name="FOODS_1 weekday flat-prediction failures",
        metric_direction_expected="up",
        explanation=(
            "Makes the model more robust. Pretty confident this helps."
        ),
        ground_truth_verdict="reject",
        bucket="vague-but-positive",
    ),
    LabeledApprovalCase(
        case_id="vague-05-cleaner-pipeline",
        proposal_summary=(
            "Index prior grievances for contract-clause matching."
        ),
        cluster_name="grievance precedent overlooked",
        metric_direction_expected="up",
        explanation=(
            "The change cleans up the pipeline and we expect it to "
            "perform better."
        ),
        ground_truth_verdict="reject",
        bucket="vague-but-positive",
    ),
    LabeledApprovalCase(
        case_id="vague-06-improvements-credit",
        proposal_summary=(
            "Switch self-employed income window from 12 to 24 months."
        ),
        cluster_name="self-employed income volatility misses",
        metric_direction_expected="up",
        explanation=(
            "Some improvements to the credit model. Should help "
            "improve the metrics."
        ),
        ground_truth_verdict="reject",
        bucket="vague-but-positive",
        notes=(
            "Mentions 'metrics' but doesn't say which direction or which "
            "specific cluster the change addresses."
        ),
    ),
    LabeledApprovalCase(
        case_id="vague-07-fix-issue",
        proposal_summary=(
            "Add hurricane indicator to SE region forecaster."
        ),
        cluster_name="Southeast hurricane displacement misses",
        metric_direction_expected="up",
        explanation=(
            "This fixes an issue. The output should be better now."
        ),
        ground_truth_verdict="reject",
        bucket="vague-but-positive",
    ),
    LabeledApprovalCase(
        case_id="vague-08-overall-better",
        proposal_summary=(
            "Add CA + NY jurisdictional-carve-out detector."
        ),
        cluster_name="jurisdictional carve-out misses",
        metric_direction_expected="up",
        explanation=(
            "Overall this is better. We're addressing what we needed to."
        ),
        ground_truth_verdict="reject",
        bucket="vague-but-positive",
    ),
)


# ---------------------------------------------------------------------------
# Structural-but-wrong-direction — names cluster + change but the direction
# contradicts the cluster's bias → reject
# ---------------------------------------------------------------------------

_WRONG_DIRECTION_CASES = (
    LabeledApprovalCase(
        case_id="wrong-dir-01-snack-down",
        proposal_summary=(
            "Add weekend-bias correction to CA snack forecaster."
        ),
        cluster_name="CA snack weekend under-forecasts",
        metric_direction_expected="up",
        explanation=(
            "Addresses the CA snack weekend under-forecast cluster by "
            "adding a weekend-bias correction. Recall should go DOWN, "
            "which is what we want."
        ),
        ground_truth_verdict="reject",
        bucket="structural-but-wrong-direction",
        notes=(
            "All three structural elements present, but the direction "
            "is wrong: an under-forecast cluster needs higher recall, "
            "not lower."
        ),
    ),
    LabeledApprovalCase(
        case_id="wrong-dir-02-cleaning-up",
        proposal_summary=(
            "Tighten over-forecast threshold on TX HOUSEHOLD_1."
        ),
        cluster_name="TX cleaning supply over-forecasts",
        metric_direction_expected="down",
        explanation=(
            "Targets the TX cleaning supply over-forecast cluster by "
            "tightening the over-forecast threshold. False-alert rate "
            "should go UP."
        ),
        ground_truth_verdict="reject",
        bucket="structural-but-wrong-direction",
        notes=(
            "Tightening a threshold reduces false alerts, not "
            "increases them. Direction wrong."
        ),
    ),
    LabeledApprovalCase(
        case_id="wrong-dir-03-zero-down",
        proposal_summary=(
            "Add zero-inflated Poisson head for low-velocity hobbies items."
        ),
        cluster_name="WI hobbies zero-inflated under-forecasts",
        metric_direction_expected="up",
        explanation=(
            "Addresses the WI hobbies zero-inflated cluster by adding "
            "a zero-inflated Poisson head. Forecast accuracy on the "
            "zero-rich test set should DROP."
        ),
        ground_truth_verdict="reject",
        bucket="structural-but-wrong-direction",
    ),
    LabeledApprovalCase(
        case_id="wrong-dir-04-credit-down",
        proposal_summary=(
            "Add 'time at current address' as feature for thin-file "
            "applicants."
        ),
        cluster_name="thin-file applicant misclassifications",
        metric_direction_expected="up",
        explanation=(
            "Addresses the thin-file applicant misclassification "
            "cluster by adding 'time at current address'. Recall on "
            "thin-file holdout should DECREASE."
        ),
        ground_truth_verdict="reject",
        bucket="structural-but-wrong-direction",
    ),
    LabeledApprovalCase(
        case_id="wrong-dir-05-jurisdictional-down",
        proposal_summary=(
            "Add CA + NY jurisdictional-carve-out detector."
        ),
        cluster_name="jurisdictional carve-out misses",
        metric_direction_expected="up",
        explanation=(
            "Targets the jurisdictional carve-out miss cluster by "
            "adding a CA + NY detector. Clause-coverage F1 should go "
            "DOWN as a result."
        ),
        ground_truth_verdict="reject",
        bucket="structural-but-wrong-direction",
    ),
    LabeledApprovalCase(
        case_id="wrong-dir-06-promo-down",
        proposal_summary=(
            "Add bundle-uplift coefficient per promo type."
        ),
        cluster_name="promotional uplift under-weighted",
        metric_direction_expected="up",
        explanation=(
            "Addresses the under-weighted promotional uplift cluster "
            "by learning a bundle-uplift coefficient. Recall on Q4 "
            "promo-bundle cases should DECREASE."
        ),
        ground_truth_verdict="reject",
        bucket="structural-but-wrong-direction",
    ),
)


# ---------------------------------------------------------------------------
# Hand-wavy — partial coverage: 1-2 elements present → reject
# ---------------------------------------------------------------------------

_HAND_WAVY_CASES = (
    LabeledApprovalCase(
        case_id="handwavy-01-cluster-only",
        proposal_summary=(
            "Add weekend-bias correction to CA snack forecaster."
        ),
        cluster_name="CA snack weekend under-forecasts",
        metric_direction_expected="up",
        explanation=(
            "This change addresses the CA snack weekend under-forecast "
            "cluster. It's a step in the right direction."
        ),
        ground_truth_verdict="reject",
        bucket="hand-wavy",
        notes=(
            "Cluster referenced; change not specified ('this change' "
            "is a referent without content); direction not stated "
            "('right direction' is empty)."
        ),
    ),
    LabeledApprovalCase(
        case_id="handwavy-02-change-only",
        proposal_summary=(
            "Tighten over-forecast threshold on TX HOUSEHOLD_1 "
            "items from 1.5x to 1.2x baseline."
        ),
        cluster_name="TX cleaning supply over-forecasts",
        metric_direction_expected="down",
        explanation=(
            "Tightens the over-forecast threshold on TX HOUSEHOLD_1 "
            "from 1.5x to 1.2x. Should yield improvements."
        ),
        ground_truth_verdict="reject",
        bucket="hand-wavy",
        notes=(
            "Change is named precisely; cluster not referenced "
            "(generic mention, doesn't tie to over-forecast cluster); "
            "direction is vague ('improvements')."
        ),
    ),
    LabeledApprovalCase(
        case_id="handwavy-03-direction-only",
        proposal_summary=(
            "Add zero-inflated Poisson head for low-velocity items."
        ),
        cluster_name="WI hobbies zero-inflated under-forecasts",
        metric_direction_expected="up",
        explanation=(
            "Forecast accuracy should go up. Several knobs were tuned."
        ),
        ground_truth_verdict="reject",
        bucket="hand-wavy",
        notes="Direction stated; cluster + change both vague.",
    ),
    LabeledApprovalCase(
        case_id="handwavy-04-name-but-no-tie",
        proposal_summary=(
            "Add lag-7 + lag-14 features to FOODS_1 forecaster."
        ),
        cluster_name="FOODS_1 weekday flat-prediction failures",
        metric_direction_expected="up",
        explanation=(
            "Adds lag-7 and lag-14 features. There's a flat-prediction "
            "issue we know about. Things should look better."
        ),
        ground_truth_verdict="reject",
        bucket="hand-wavy",
        notes=(
            "Change named, cluster mentioned in passing without tying "
            "the change to it, direction vague ('better')."
        ),
    ),
    LabeledApprovalCase(
        case_id="handwavy-05-two-of-three",
        proposal_summary=(
            "Index prior-grievance corpus, surface top-3 per clause."
        ),
        cluster_name="grievance precedent overlooked",
        metric_direction_expected="up",
        explanation=(
            "Indexes the prior-grievance corpus and surfaces top-3 "
            "matches per clause. Things should improve overall."
        ),
        ground_truth_verdict="reject",
        bucket="hand-wavy",
        notes=(
            "Change named clearly; cluster not referenced; direction "
            "vague ('improve overall')."
        ),
    ),
    LabeledApprovalCase(
        case_id="handwavy-06-cluster-and-direction-no-change",
        proposal_summary=(
            "Add hurricane-evacuation indicator from NOAA feed."
        ),
        cluster_name="Southeast hurricane displacement misses",
        metric_direction_expected="up",
        explanation=(
            "This addresses the Southeast hurricane displacement "
            "cluster. Markdown-alert recall should go up."
        ),
        ground_truth_verdict="reject",
        bucket="hand-wavy",
        notes=(
            "Cluster + direction both present, but the change itself "
            "is not described ('this addresses' is a referent without "
            "content). Strict gate rejects when the change is not "
            "named."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Public tuple — concatenated with stable order so `_aggregate` uses
# fixture-order indices.
# ---------------------------------------------------------------------------

LABELED_APPROVAL_CASES: tuple[LabeledApprovalCase, ...] = (
    *_STRUCTURAL_CASES,
    *_VAGUE_CASES,
    *_WRONG_DIRECTION_CASES,
    *_HAND_WAVY_CASES,
)


# ---------------------------------------------------------------------------
# Module-import-time invariants. A schema bump or fixture edit that
# violates one of these fails loudly at import — preferable to a CLI
# run reporting a meaningless agreement.
# ---------------------------------------------------------------------------

_EXPECTED_BUCKET_COUNTS = {
    "structural": 10,
    "vague-but-positive": 8,
    "structural-but-wrong-direction": 6,
    "hand-wavy": 6,
}

_seen_ids: set[str] = set()
_bucket_counts: dict[str, int] = {}
for _case in LABELED_APPROVAL_CASES:
    if _case.case_id in _seen_ids:
        raise AssertionError(
            f"Duplicate case_id in LABELED_APPROVAL_CASES: {_case.case_id!r}"
        )
    _seen_ids.add(_case.case_id)
    _bucket_counts[_case.bucket] = _bucket_counts.get(_case.bucket, 0) + 1
    # admit only on `structural`; everything else is reject.
    expected_verdict = "admit" if _case.bucket == "structural" else "reject"
    if _case.ground_truth_verdict != expected_verdict:
        raise AssertionError(
            f"Case {_case.case_id!r} bucket={_case.bucket!r} but "
            f"ground_truth_verdict={_case.ground_truth_verdict!r}; "
            f"expected {expected_verdict!r}."
        )

if _bucket_counts != _EXPECTED_BUCKET_COUNTS:
    raise AssertionError(
        f"LABELED_APPROVAL_CASES bucket distribution {_bucket_counts} "
        f"diverged from expected {_EXPECTED_BUCKET_COUNTS}."
    )

if len(LABELED_APPROVAL_CASES) != 30:
    raise AssertionError(
        f"LABELED_APPROVAL_CASES has {len(LABELED_APPROVAL_CASES)} entries; "
        f"expected 30 per the W5.2 spec."
    )


__all__ = [
    "LabeledApprovalCase",
    "LABELED_APPROVAL_CASES",
]
