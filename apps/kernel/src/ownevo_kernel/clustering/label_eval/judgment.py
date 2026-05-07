"""ClusterLabelJudgment — typed schema for the B3.5 cluster-label judge.

The cluster-label judge is the LLM-as-judge that scores a candidate
cluster label (produced by the production `Labeler` — default
`AnthropicLabeler` on haiku 4.5) against a hand-authored ground-truth
label. It is the W3 Track B quality gate D4 calls for: catches
"the labeler hallucinated 'pet supplies' for a cluster of canned-goods
under-forecasts" — failure modes that pass `pipeline.py`'s `[:120]`
length cap + non-empty check but still ship junk to the demo cluster
card.

Why a typed artifact (not free-form text):

  * **Binary verdict** — `agree / disagree`. The W3 deliverable is one
    agreement number (≥0.7); a binary scale makes the math unambiguous
    and avoids the "everything is partial" calibration drift smaller
    judges hit on three-level scales.
  * **Structured rationale** — the rationale is what an operator reads
    when triaging a `disagree` verdict, and what catches "judge is
    rubber-stamping" drift. Surfaces in the audit trail next to the
    verdict.
  * **Echoed cluster_id** — joins judgments back to fixtures without
    relying on positional ordering when the runner runs concurrently.

`schema_version="0.1"` until the W3-end freeze (mirrors A3.1/A3.2/A4.6
pre-freeze convention). When the freeze fires, the snapshot lands at
`clustering/label_eval/schemas/cluster_label_judgment.v1.0.json`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1"

LabelVerdict = Literal["agree", "disagree"]
"""Binary discrete scale for the judge's verdict.

  * `agree` — the candidate label is semantically equivalent to the
    ground-truth label, OR the candidate is more specific than the
    ground-truth in a way that doesn't introduce a false claim
    ("under-forecast in CA" when ground-truth is "under-forecast"
    is `agree`; the extra specificity is supported by the members).
  * `disagree` — the candidate names a different failure mode, or
    invents a domain not present in the members (hallucination), or
    contradicts the ground-truth's bias direction.

Numeric mapping (`verdict_score`): agree=1.0, disagree=0.0. The
mean of `verdict_score` across the eval set IS the agreement number
the W3 ≥0.7 gate measures.
"""


_VERDICT_TO_SCORE: dict[LabelVerdict, float] = {
    "agree": 1.0,
    "disagree": 0.0,
}


def verdict_score(verdict: LabelVerdict) -> float:
    """Map a verdict to a numeric score in [0, 1]."""
    return _VERDICT_TO_SCORE[verdict]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClusterLabelJudgment(_Base):
    """B3.5 artifact: the LLM-as-judge's verdict on one (candidate, ground-truth)
    label pair for one labeled cluster fixture.

    The judge takes (cluster_id, domain_context, member_signatures,
    ground_truth_label, candidate_label) and emits this. Binary verdict
    + one-line rationale + an echoed `cluster_id` for joinability.
    """

    schema_version: Literal["0.1"] = SCHEMA_VERSION
    cluster_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
        description=(
            "LabeledClusterCase.cluster_id of the case this judgment is for. "
            "The judge must echo this verbatim from the input so concurrent "
            "runner.gather() doesn't depend on positional ordering."
        ),
    )
    verdict: LabelVerdict = Field(
        description=(
            "Binary verdict. See module docstring for the agree/disagree "
            "criterion. Agreement = mean(verdict_score) over the eval set."
        ),
    )
    rationale: str = Field(
        min_length=1,
        description=(
            "Explanation of the verdict. Quotes the candidate "
            "and ground-truth phrasing where relevant. Surfaces in the "
            "audit trail; what an operator reads when triaging a disagree."
        ),
    )


__all__ = [
    "SCHEMA_VERSION",
    "LabelVerdict",
    "ClusterLabelJudgment",
    "verdict_score",
]
