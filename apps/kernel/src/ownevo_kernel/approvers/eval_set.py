"""The 30-pair LLM-judge stub approver eval set (W5.2).

Hand-labeled (proposal-context, explanation, expected_admit) records
spanning one admit bucket and four reject buckets — six records per
bucket, six domains. Used by `runner.run_judge_eval` to grade the
judge's calibration; the W5.2 deliverable target is judge-vs-human
agreement ≥ 0.85.

Bucket distribution (6 each, balanced 6 admit + 24 reject):

  A. `admit-structural-correct`
     All three structural elements present + direction is correct on
     the metric's improvement axis. The judge MUST admit. These are
     the proposals an unattended replay should be allowed to ship.

  B. `reject-vague-positive`
     Generic upbeat copy with no structural specifics ("this should
     improve performance"). The judge MUST reject; admitting these
     would let the agent ship without explaining what changed.

  C. `reject-wrong-direction`
     All three structural elements *appear*, but the stated metric
     direction contradicts the metric's improvement axis (e.g.,
     "expect RMSSE to increase" on a lower-is-better metric). The
     judge MUST catch this — it's the highest-leverage adversarial
     case (lift drifts the wrong way silently if the judge admits).

  D. `reject-handwavy-change`
     Cluster reference + correct direction present, but the change
     itself is vague ("tuned the model", "made it more conservative"
     — no named lever). Reject.

  E. `reject-missing-cluster`
     Specific change + correct direction present, but no reference
     to the cluster being addressed (the change might be unrelated
     to the failure mode the proposal is supposed to fix). Reject.

Domains: 6 distinct workflows (M5 demand-prediction, credit-risk-line-
recalibration, supplier-late-shipment-risk, fraud-card-decline-review,
clinical-trial-eligibility, content-moderation-escalation). Each
domain appears once per bucket so a domain-specific bias in the judge
shows up across rows in different buckets.

Why pre-assembled at module load: same reasons as `META_EVAL_SET` —
deterministic, one place to read the set, cheap import.

The pair_id format is `<bucket-id>:<domain-slug>` so the runner's
per-bucket aggregation is robust to reordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .llm_judge import MetricImprovementAxis, ProposalContext

JudgeBucketId = Literal[
    "admit-structural-correct",
    "reject-vague-positive",
    "reject-wrong-direction",
    "reject-handwavy-change",
    "reject-missing-cluster",
]
"""The five hand-labeled buckets. Surfaced in the runner's per-bucket
correctness breakdown so a regression on one specific failure mode
(e.g., the judge starts admitting wrong-direction proposals) is
visible without re-reading every record."""


@dataclass(frozen=True)
class JudgeEvalPair:
    """One (context, explanation, expected_admit) eval record.

    `bucket_id` is metadata for grading — the judge does NOT see it.
    `expected_admit` is True for the admit bucket and False for the
    four reject buckets; the runner compares this against
    `judgment.admits`.

    `rationale` documents WHY this record landed in the bucket, for
    future reviewers maintaining the set. Not surfaced to the judge.
    """

    pair_id: str
    bucket_id: JudgeBucketId
    expected_admit: bool
    context: ProposalContext
    rationale: str = field(default="")


_DOMAINS: dict[str, dict[str, str]] = {
    "demand-prediction": {
        "cluster_label": "weekend-spike-under-forecast",
        "cluster_summary": (
            "Saturday and Sunday demand for snack items is systematically "
            "under-predicted; the baseline misses the weekend spike "
            "across most snack series."
        ),
        "skill_id": "feature_engineer",
        "metric_name": "RMSSE",
        "metric_improvement_axis": "lower-is-better",
    },
    "credit-risk": {
        "cluster_label": "young-thin-file-false-alerts",
        "cluster_summary": (
            "Applicants under 25 with fewer than 12 months of credit "
            "history flag at roughly three times the baseline rate, "
            "while their realized default rate matches the baseline."
        ),
        "skill_id": "risk_recalibrator",
        "metric_name": "false_alert_rate",
        "metric_improvement_axis": "lower-is-better",
    },
    "supplier-risk": {
        "cluster_label": "port-strike-blind-spot",
        "cluster_summary": (
            "The screener missed late shipments during the LA-port "
            "strike because the strike signal wasn't represented in "
            "the input feature set."
        ),
        "skill_id": "supplier_screener",
        "metric_name": "recall",
        "metric_improvement_axis": "higher-is-better",
    },
    "fraud-review": {
        "cluster_label": "subscription-renewal-mass-decline",
        "cluster_summary": (
            "Monthly streaming-service renewal batches (Netflix, "
            "Spotify, Disney+) flag as fraud — high recall, low "
            "precision on the renewal-burst slice."
        ),
        "skill_id": "fraud_reviewer",
        "metric_name": "precision",
        "metric_improvement_axis": "higher-is-better",
    },
    "clinical-eligibility": {
        "cluster_label": "coexisting-condition-narrow-rule",
        "cluster_summary": (
            "The screener rejects patients with controlled comorbidities "
            "(well-managed diabetes, stable hypertension) where the "
            "trial protocol explicitly allows them."
        ),
        "skill_id": "eligibility_screener",
        "metric_name": "balanced_accuracy",
        "metric_improvement_axis": "higher-is-better",
    },
    "content-moderation": {
        "cluster_label": "coded-language-policy-violations",
        "cluster_summary": (
            "The router misses policy violations expressed in emerging "
            "coded slang (e.g., 'unalive' for self-harm, 'seggs' for "
            "sexual content) that the lexicon hasn't caught up with."
        ),
        "skill_id": "moderation_router",
        "metric_name": "recall_on_policy_violations",
        "metric_improvement_axis": "higher-is-better",
    },
}


def _ctx(domain_slug: str, *, proposal_id: str, explanation: str) -> ProposalContext:
    d = _DOMAINS[domain_slug]
    axis: MetricImprovementAxis = d["metric_improvement_axis"]  # type: ignore[assignment]
    return ProposalContext(
        proposal_id=proposal_id,
        cluster_label=d["cluster_label"],
        cluster_summary=d["cluster_summary"],
        skill_id=d["skill_id"],
        metric_name=d["metric_name"],
        metric_improvement_axis=axis,
        explanation=explanation,
    )


# ---------------------------------------------------------------------------
# Bucket A — admit (structural-correct, all three checks pass + direction OK)
# ---------------------------------------------------------------------------

_A_DEMAND = JudgeEvalPair(
    pair_id="admit-structural-correct:demand-prediction",
    bucket_id="admit-structural-correct",
    expected_admit=True,
    context=_ctx(
        "demand-prediction",
        proposal_id="00000000-0000-0000-0000-0000000000a1",
        explanation=(
            "Cluster `weekend-spike-under-forecast` shows we systematically "
            "under-predict Saturday and Sunday demand for snack items. "
            "Added an `is_weekend × category=snack` interaction term to "
            "the feature pipeline; expect this to reduce RMSSE on weekend "
            "snack series by roughly 5%."
        ),
    ),
    rationale=(
        "Cluster named verbatim; specific change (interaction term added); "
        "direction is reduce-RMSSE on a lower-is-better metric — admit."
    ),
)

_A_CREDIT = JudgeEvalPair(
    pair_id="admit-structural-correct:credit-risk",
    bucket_id="admit-structural-correct",
    expected_admit=True,
    context=_ctx(
        "credit-risk",
        proposal_id="00000000-0000-0000-0000-0000000000a2",
        explanation=(
            "For the `young-thin-file-false-alerts` cluster — applicants "
            "under 25 with under 12 months of history were flagging at "
            "three times baseline. Lowered the score-threshold reweighting "
            "from 0.85 to 0.70 for the thin-file segment; expect the "
            "false-alert-rate on this segment to drop without hurting "
            "the realized catch rate."
        ),
    ),
    rationale=(
        "Cluster named verbatim; specific change (threshold lowered from "
        "0.85 to 0.70); direction is drop-false-alerts on lower-is-better."
    ),
)

_A_SUPPLIER = JudgeEvalPair(
    pair_id="admit-structural-correct:supplier-risk",
    bucket_id="admit-structural-correct",
    expected_admit=True,
    context=_ctx(
        "supplier-risk",
        proposal_id="00000000-0000-0000-0000-0000000000a3",
        explanation=(
            "Cluster `port-strike-blind-spot` — the screener missed late "
            "shipments during the LA-port strike because the strike-event "
            "signal wasn't in the input feature set. Added a "
            "`port_strike_active` boolean fed from the news ingestion feed; "
            "expect supplier-late-shipment recall to rise on shipments "
            "routing through affected ports."
        ),
    ),
    rationale=(
        "Cluster named verbatim; specific change (added boolean feature); "
        "direction is rise-recall on higher-is-better."
    ),
)

_A_FRAUD = JudgeEvalPair(
    pair_id="admit-structural-correct:fraud-review",
    bucket_id="admit-structural-correct",
    expected_admit=True,
    context=_ctx(
        "fraud-review",
        proposal_id="00000000-0000-0000-0000-0000000000a4",
        explanation=(
            "Cluster `subscription-renewal-mass-decline` flagged the monthly "
            "Spotify and Netflix renewal batches as fraud (high recall, "
            "terrible precision). Added a recurrence-detector that "
            "whitelists merchant_id × user_id pairs with three or more "
            "prior on-cycle charges; expect precision to improve on the "
            "renewal-burst slice."
        ),
    ),
    rationale=(
        "Cluster named verbatim; specific change (added recurrence detector "
        "with whitelist rule); direction is improve-precision on higher-is-"
        "better."
    ),
)

_A_CLINICAL = JudgeEvalPair(
    pair_id="admit-structural-correct:clinical-eligibility",
    bucket_id="admit-structural-correct",
    expected_admit=True,
    context=_ctx(
        "clinical-eligibility",
        proposal_id="00000000-0000-0000-0000-0000000000a5",
        explanation=(
            "Cluster `coexisting-condition-narrow-rule` — the screener was "
            "rejecting patients with controlled comorbidities (well-managed "
            "diabetes, stable hypertension) where the protocol allows them. "
            "Switched the comorbidity rule from a hard exclusion list to a "
            "`protocol-allowed-comorbidities` whitelist. Expect "
            "balanced_accuracy to rise as more eligible patients are "
            "correctly admitted."
        ),
    ),
    rationale=(
        "Cluster named verbatim; specific change (exclusion list → "
        "whitelist); direction is rise-balanced_accuracy on higher-is-"
        "better."
    ),
)

_A_MODERATION = JudgeEvalPair(
    pair_id="admit-structural-correct:content-moderation",
    bucket_id="admit-structural-correct",
    expected_admit=True,
    context=_ctx(
        "content-moderation",
        proposal_id="00000000-0000-0000-0000-0000000000a6",
        explanation=(
            "Cluster `coded-language-policy-violations` — the router misses "
            "policy violations expressed in emerging coded slang (e.g., "
            "'unalive' for self-harm). Added a coded-slang lexicon updated "
            "from the trust-and-safety drift report. Expect recall on "
            "policy-violation cases to rise as those terms are now caught."
        ),
    ),
    rationale=(
        "Cluster named verbatim; specific change (added coded-slang "
        "lexicon); direction is rise-recall on higher-is-better."
    ),
)

# ---------------------------------------------------------------------------
# Bucket B — reject: vague-but-positive (no structural elements)
# ---------------------------------------------------------------------------

_B_DEMAND = JudgeEvalPair(
    pair_id="reject-vague-positive:demand-prediction",
    bucket_id="reject-vague-positive",
    expected_admit=False,
    context=_ctx(
        "demand-prediction",
        proposal_id="00000000-0000-0000-0000-0000000000b1",
        explanation=(
            "This proposal should reduce errors and improve overall model "
            "performance. The change addresses several issues I noticed "
            "in the recent results."
        ),
    ),
    rationale="No cluster, no specific change, no specific direction.",
)

_B_CREDIT = JudgeEvalPair(
    pair_id="reject-vague-positive:credit-risk",
    bucket_id="reject-vague-positive",
    expected_admit=False,
    context=_ctx(
        "credit-risk",
        proposal_id="00000000-0000-0000-0000-0000000000b2",
        explanation=(
            "Made some improvements to the recalibrator that I think will "
            "help with the issues we've been seeing on recent applicants."
        ),
    ),
    rationale=(
        "No cluster, no specific change ('improvements'), no direction."
    ),
)

_B_SUPPLIER = JudgeEvalPair(
    pair_id="reject-vague-positive:supplier-risk",
    bucket_id="reject-vague-positive",
    expected_admit=False,
    context=_ctx(
        "supplier-risk",
        proposal_id="00000000-0000-0000-0000-0000000000b3",
        explanation=(
            "Tweaked the screener slightly. This is a positive change and "
            "should make things better across the board."
        ),
    ),
    rationale="No cluster, vague change, vague direction.",
)

_B_FRAUD = JudgeEvalPair(
    pair_id="reject-vague-positive:fraud-review",
    bucket_id="reject-vague-positive",
    expected_admit=False,
    context=_ctx(
        "fraud-review",
        proposal_id="00000000-0000-0000-0000-0000000000b4",
        explanation=(
            "Updated the reviewer. Expect performance to improve on the "
            "kinds of cases that were previously misclassified."
        ),
    ),
    rationale="No cluster, no specific change ('updated'), no direction.",
)

_B_CLINICAL = JudgeEvalPair(
    pair_id="reject-vague-positive:clinical-eligibility",
    bucket_id="reject-vague-positive",
    expected_admit=False,
    context=_ctx(
        "clinical-eligibility",
        proposal_id="00000000-0000-0000-0000-0000000000b5",
        explanation=(
            "Refactored the rule pipeline and cleaned up a few things. "
            "Should be in better shape now."
        ),
    ),
    rationale="No cluster, no specific change, no direction.",
)

_B_MODERATION = JudgeEvalPair(
    pair_id="reject-vague-positive:content-moderation",
    bucket_id="reject-vague-positive",
    expected_admit=False,
    context=_ctx(
        "content-moderation",
        proposal_id="00000000-0000-0000-0000-0000000000b6",
        explanation=(
            "Fixed a few things in the router. Metrics should move in the "
            "right direction."
        ),
    ),
    rationale="No cluster, vague change ('fixed a few things'), no direction.",
)

# ---------------------------------------------------------------------------
# Bucket C — reject: structural-but-wrong-direction (passes refs+names, fails direction)
# ---------------------------------------------------------------------------

_C_DEMAND = JudgeEvalPair(
    pair_id="reject-wrong-direction:demand-prediction",
    bucket_id="reject-wrong-direction",
    expected_admit=False,
    context=_ctx(
        "demand-prediction",
        proposal_id="00000000-0000-0000-0000-0000000000c1",
        explanation=(
            "Cluster `weekend-spike-under-forecast` — added an "
            "`is_weekend × category=snack` interaction term to the feature "
            "pipeline. Expect RMSSE to increase by roughly 5% on weekend "
            "snack series."
        ),
    ),
    rationale=(
        "Cluster + change present, but direction (RMSSE increase) "
        "contradicts the lower-is-better axis."
    ),
)

_C_CREDIT = JudgeEvalPair(
    pair_id="reject-wrong-direction:credit-risk",
    bucket_id="reject-wrong-direction",
    expected_admit=False,
    context=_ctx(
        "credit-risk",
        proposal_id="00000000-0000-0000-0000-0000000000c2",
        explanation=(
            "For the `young-thin-file-false-alerts` cluster, lowered the "
            "score-threshold reweighting from 0.85 to 0.70 for the "
            "thin-file segment. Expect the false-alert-rate to climb on "
            "this segment as a result."
        ),
    ),
    rationale=(
        "Cluster + change present, but direction (false-alert-rate climb) "
        "contradicts lower-is-better."
    ),
)

_C_SUPPLIER = JudgeEvalPair(
    pair_id="reject-wrong-direction:supplier-risk",
    bucket_id="reject-wrong-direction",
    expected_admit=False,
    context=_ctx(
        "supplier-risk",
        proposal_id="00000000-0000-0000-0000-0000000000c3",
        explanation=(
            "Cluster `port-strike-blind-spot` — added a `port_strike_active` "
            "boolean fed from the news ingestion feed. Expect recall on "
            "late-shipment cases to drop on shipments routing through "
            "affected ports."
        ),
    ),
    rationale=(
        "Cluster + change present, but direction (recall drop) "
        "contradicts higher-is-better."
    ),
)

_C_FRAUD = JudgeEvalPair(
    pair_id="reject-wrong-direction:fraud-review",
    bucket_id="reject-wrong-direction",
    expected_admit=False,
    context=_ctx(
        "fraud-review",
        proposal_id="00000000-0000-0000-0000-0000000000c4",
        explanation=(
            "Cluster `subscription-renewal-mass-decline` — added a "
            "recurrence-detector that whitelists merchant_id × user_id "
            "pairs with three or more prior on-cycle charges. Expect "
            "precision on the renewal-burst slice to fall as a result."
        ),
    ),
    rationale=(
        "Cluster + change present, but direction (precision fall) "
        "contradicts higher-is-better."
    ),
)

_C_CLINICAL = JudgeEvalPair(
    pair_id="reject-wrong-direction:clinical-eligibility",
    bucket_id="reject-wrong-direction",
    expected_admit=False,
    context=_ctx(
        "clinical-eligibility",
        proposal_id="00000000-0000-0000-0000-0000000000c5",
        explanation=(
            "Cluster `coexisting-condition-narrow-rule` — switched the "
            "comorbidity rule from a hard exclusion list to a "
            "`protocol-allowed-comorbidities` whitelist. Expect "
            "balanced_accuracy to decrease on the screener."
        ),
    ),
    rationale=(
        "Cluster + change present, but direction (balanced_accuracy "
        "decrease) contradicts higher-is-better."
    ),
)

_C_MODERATION = JudgeEvalPair(
    pair_id="reject-wrong-direction:content-moderation",
    bucket_id="reject-wrong-direction",
    expected_admit=False,
    context=_ctx(
        "content-moderation",
        proposal_id="00000000-0000-0000-0000-0000000000c6",
        explanation=(
            "Cluster `coded-language-policy-violations` — added a coded-"
            "slang lexicon updated from the trust-and-safety drift report. "
            "Expect recall on policy-violation cases to drop."
        ),
    ),
    rationale=(
        "Cluster + change present, but direction (recall drop) "
        "contradicts higher-is-better."
    ),
)

# ---------------------------------------------------------------------------
# Bucket D — reject: hand-wavy / missing change name (passes refs+direction, fails names_change)
# ---------------------------------------------------------------------------

_D_DEMAND = JudgeEvalPair(
    pair_id="reject-handwavy-change:demand-prediction",
    bucket_id="reject-handwavy-change",
    expected_admit=False,
    context=_ctx(
        "demand-prediction",
        proposal_id="00000000-0000-0000-0000-0000000000d1",
        explanation=(
            "Cluster `weekend-spike-under-forecast` — tuned the feature "
            "pipeline for snack categories. Expect RMSSE to drop on "
            "weekend snack series."
        ),
    ),
    rationale=(
        "Cluster + direction OK, but 'tuned' is hand-wavy — no specific "
        "lever named."
    ),
)

_D_CREDIT = JudgeEvalPair(
    pair_id="reject-handwavy-change:credit-risk",
    bucket_id="reject-handwavy-change",
    expected_admit=False,
    context=_ctx(
        "credit-risk",
        proposal_id="00000000-0000-0000-0000-0000000000d2",
        explanation=(
            "For the `young-thin-file-false-alerts` cluster, made the "
            "recalibrator more conservative on this segment. False-alert-"
            "rate should fall."
        ),
    ),
    rationale=(
        "Cluster + direction OK, but 'more conservative' is hand-wavy — "
        "no specific lever (which threshold? which input?) named."
    ),
)

_D_SUPPLIER = JudgeEvalPair(
    pair_id="reject-handwavy-change:supplier-risk",
    bucket_id="reject-handwavy-change",
    expected_admit=False,
    context=_ctx(
        "supplier-risk",
        proposal_id="00000000-0000-0000-0000-0000000000d3",
        explanation=(
            "Cluster `port-strike-blind-spot` — improved the screener's "
            "input handling. Recall on affected lanes should rise."
        ),
    ),
    rationale="Cluster + direction OK, but 'improved input handling' is hand-wavy.",
)

_D_FRAUD = JudgeEvalPair(
    pair_id="reject-handwavy-change:fraud-review",
    bucket_id="reject-handwavy-change",
    expected_admit=False,
    context=_ctx(
        "fraud-review",
        proposal_id="00000000-0000-0000-0000-0000000000d4",
        explanation=(
            "Cluster `subscription-renewal-mass-decline` — fixed the "
            "renewal-detection logic. Precision should improve on "
            "renewal bursts."
        ),
    ),
    rationale=(
        "Cluster + direction OK, but 'fixed the renewal-detection logic' "
        "doesn't say HOW it was fixed."
    ),
)

_D_CLINICAL = JudgeEvalPair(
    pair_id="reject-handwavy-change:clinical-eligibility",
    bucket_id="reject-handwavy-change",
    expected_admit=False,
    context=_ctx(
        "clinical-eligibility",
        proposal_id="00000000-0000-0000-0000-0000000000d5",
        explanation=(
            "Cluster `coexisting-condition-narrow-rule` — adjusted the "
            "comorbidity rule. Expect balanced_accuracy to lift."
        ),
    ),
    rationale="Cluster + direction OK, but 'adjusted the comorbidity rule' is hand-wavy.",
)

_D_MODERATION = JudgeEvalPair(
    pair_id="reject-handwavy-change:content-moderation",
    bucket_id="reject-handwavy-change",
    expected_admit=False,
    context=_ctx(
        "content-moderation",
        proposal_id="00000000-0000-0000-0000-0000000000d6",
        explanation=(
            "Cluster `coded-language-policy-violations` — refreshed the "
            "moderation lexicon handling. Recall on policy violations "
            "should climb."
        ),
    ),
    rationale=(
        "Cluster + direction OK, but 'refreshed the lexicon handling' "
        "doesn't say what specifically changed."
    ),
)

# ---------------------------------------------------------------------------
# Bucket E — reject: missing cluster reference (passes names+direction, fails refs)
# ---------------------------------------------------------------------------

_E_DEMAND = JudgeEvalPair(
    pair_id="reject-missing-cluster:demand-prediction",
    bucket_id="reject-missing-cluster",
    expected_admit=False,
    context=_ctx(
        "demand-prediction",
        proposal_id="00000000-0000-0000-0000-0000000000e1",
        explanation=(
            "Added an L2 regularization term (lambda=0.01) to the "
            "LightGBM trainer. Expect overall validation RMSSE to fall "
            "by roughly 3%."
        ),
    ),
    rationale=(
        "Specific change + correct direction, but no reference to the "
        "weekend-spike-under-forecast cluster — change is unrelated to "
        "the failure mode."
    ),
)

_E_CREDIT = JudgeEvalPair(
    pair_id="reject-missing-cluster:credit-risk",
    bucket_id="reject-missing-cluster",
    expected_admit=False,
    context=_ctx(
        "credit-risk",
        proposal_id="00000000-0000-0000-0000-0000000000e2",
        explanation=(
            "Replaced the linear-regression recalibrator with a gradient-"
            "boosted tree on the same feature set. Expect false-alert-rate "
            "to drop by roughly 10%."
        ),
    ),
    rationale=(
        "Specific change + correct direction, but no reference to the "
        "young-thin-file-false-alerts cluster — change is generic."
    ),
)

_E_SUPPLIER = JudgeEvalPair(
    pair_id="reject-missing-cluster:supplier-risk",
    bucket_id="reject-missing-cluster",
    expected_admit=False,
    context=_ctx(
        "supplier-risk",
        proposal_id="00000000-0000-0000-0000-0000000000e3",
        explanation=(
            "Switched the screener from a daily refresh cadence to an "
            "hourly refresh. Expect recall to climb across the board."
        ),
    ),
    rationale=(
        "Specific change + correct direction, but no reference to the "
        "port-strike-blind-spot cluster."
    ),
)

_E_FRAUD = JudgeEvalPair(
    pair_id="reject-missing-cluster:fraud-review",
    bucket_id="reject-missing-cluster",
    expected_admit=False,
    context=_ctx(
        "fraud-review",
        proposal_id="00000000-0000-0000-0000-0000000000e4",
        explanation=(
            "Removed the deprecated `reason_code_legacy` feature from the "
            "reviewer's input. Expect precision to rise as a stale signal "
            "stops confusing the model."
        ),
    ),
    rationale=(
        "Specific change + correct direction, but no reference to the "
        "subscription-renewal-mass-decline cluster."
    ),
)

_E_CLINICAL = JudgeEvalPair(
    pair_id="reject-missing-cluster:clinical-eligibility",
    bucket_id="reject-missing-cluster",
    expected_admit=False,
    context=_ctx(
        "clinical-eligibility",
        proposal_id="00000000-0000-0000-0000-0000000000e5",
        explanation=(
            "Bumped the screener model from sonnet to opus. Expect "
            "balanced_accuracy to climb on the eligibility-screen task."
        ),
    ),
    rationale=(
        "Specific change + correct direction, but no reference to the "
        "coexisting-condition-narrow-rule cluster."
    ),
)

_E_MODERATION = JudgeEvalPair(
    pair_id="reject-missing-cluster:content-moderation",
    bucket_id="reject-missing-cluster",
    expected_admit=False,
    context=_ctx(
        "content-moderation",
        proposal_id="00000000-0000-0000-0000-0000000000e6",
        explanation=(
            "Added a per-language confidence-calibration step on the "
            "router's output. Expect recall on policy violations to rise."
        ),
    ),
    rationale=(
        "Specific change + correct direction, but no reference to the "
        "coded-language-policy-violations cluster."
    ),
)


JUDGE_EVAL_SET: list[JudgeEvalPair] = [
    # Bucket A — admit (6)
    _A_DEMAND, _A_CREDIT, _A_SUPPLIER, _A_FRAUD, _A_CLINICAL, _A_MODERATION,
    # Bucket B — reject vague-positive (6)
    _B_DEMAND, _B_CREDIT, _B_SUPPLIER, _B_FRAUD, _B_CLINICAL, _B_MODERATION,
    # Bucket C — reject wrong-direction (6) — the highest-leverage adversarial bucket
    _C_DEMAND, _C_CREDIT, _C_SUPPLIER, _C_FRAUD, _C_CLINICAL, _C_MODERATION,
    # Bucket D — reject hand-wavy change (6)
    _D_DEMAND, _D_CREDIT, _D_SUPPLIER, _D_FRAUD, _D_CLINICAL, _D_MODERATION,
    # Bucket E — reject missing cluster reference (6)
    _E_DEMAND, _E_CREDIT, _E_SUPPLIER, _E_FRAUD, _E_CLINICAL, _E_MODERATION,
]


JUDGE_SMOKE_SET: list[JudgeEvalPair] = [
    # 3 admits — one across three different domains so a judge that's
    # only calibrated on one domain (e.g. M5) gets caught.
    _A_DEMAND,
    _A_SUPPLIER,
    _A_CLINICAL,
    # 2 rejects — the highest-leverage adversarial cases:
    #   - vague-but-positive: nothing structural at all (PLAN.md adversarial
    #     test "vague-but-positive → rejected" is anchored on this case).
    #   - wrong-direction: passes refs+names but contradicts the metric's
    #     improvement axis. If the judge fails this on the smoke set,
    #     the unattended replay drifts the lift the wrong way silently.
    _B_DEMAND,
    _C_FRAUD,
]
"""Five-record smoke subset (3 admit + 2 reject) used by `--smoke` for
fast/cheap iteration on the judge prompt without burning a full
30-call run. The two rejects are the highest-leverage adversarial
cases (vague-but-positive + wrong-direction) — if the judge mis-calls
either, the unattended-replay risk is highest. Per PLAN.md § Week 5
5.2's smoke spec ("5 hand-crafted proposals → judge admits 3, rejects 2")."""


__all__ = [
    "JudgeBucketId",
    "JudgeEvalPair",
    "JUDGE_EVAL_SET",
    "JUDGE_SMOKE_SET",
]
