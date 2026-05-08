"""LLMJudgeApprovalJudgment — typed schema for the W5.2 LLM-judge stub
approver.

The judge takes (proposal, plain-language explanation) and answers
two interlocking questions:

  1. Per-element: which of the three structural elements is present?
     Each element gets a `present: bool` + a one-line `quote` (the
     phrase from the explanation that supports the verdict; empty
     when `present=False`).

  2. Overall: should the change be admitted to the agent loop?
     Binary `admit | reject` with a short rationale. The judge has
     discretion here — a structural-but-wrong-direction explanation
     has all three elements present (cluster + change + direction)
     but the direction is wrong, so the verdict is `reject`.

Why a typed artifact (not free-form text):

  * **Per-element booleans** — the eval set slices agreement by
    failure mode (vague vs. wrong-direction vs. hand-wavy), and
    that requires structured per-element verdicts so a single-bucket
    regression doesn't average out behind the overall.
  * **Quotes for transparency** — when the judge admits or rejects,
    the audit trail records *what part of the explanation* drove the
    decision. A reviewer triaging "why did the judge reject this?"
    reads the `quote` field, not the rationale.
  * **Echoed proposal_id** — joins judgments back to fixtures /
    proposals without relying on positional ordering when the
    runner runs concurrently (mirrors B3.5 + A4.6).

`schema_version="0.1"` until the W5-end freeze (mirrors A3.1/A3.2/
A4.6/B3.5 pre-freeze convention). When the freeze fires, the
snapshot lands at
`approvers/llm_judge/schemas/llm_judge_approval_judgment.v1.0.json`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.1"

ApprovalVerdict = Literal["admit", "reject"]
"""Binary discrete scale for the judge's verdict.

  * `admit` — the explanation contains all three structural elements
    AND the elements are mutually consistent (cluster + change name +
    metric direction). Safe to advance the proposal to
    `approved-awaiting-deploy`.
  * `reject` — at least one element is missing, OR the elements are
    contradictory (e.g., names a cluster of under-forecasts but
    claims the change should reduce forecasts). The change is held
    back; an operator should review.

Numeric mapping (`verdict_score`): admit=1.0, reject=0.0. The mean
of `verdict_score == ground_truth_score` across the eval set IS the
agreement number the W5.2 ≥0.85 gate measures. `reject` is the safe
default — it's a strict gate, not a permissive one."""


_VERDICT_TO_SCORE: dict[ApprovalVerdict, float] = {
    "admit": 1.0,
    "reject": 0.0,
}


def verdict_score(verdict: ApprovalVerdict) -> float:
    """Map a verdict to a numeric score in [0, 1]."""
    return _VERDICT_TO_SCORE[verdict]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StructuralElement(_Base):
    """One of the three structural elements: present + supporting quote.

    `present` is the load-bearing field; `quote` is a one-line excerpt
    from the explanation that supports the verdict (empty string when
    `present=False`). The quote is what surfaces in the audit trail —
    a reviewer triaging "why did the judge reject this?" reads the
    quotes per element to see what the judge saw.
    """

    present: bool = Field(
        description=(
            "Whether the structural element appears in the explanation. "
            "The judge resolves synonyms + paraphrases (e.g., 'the "
            "weekend snack cluster' counts as a cluster reference even "
            "if the cluster_id is not quoted verbatim)."
        ),
    )
    quote: str = Field(
        max_length=400,
        description=(
            "A short verbatim or near-verbatim excerpt from the "
            "explanation that supports `present=True`. Empty string "
            "when `present=False`. Used by the audit trail."
        ),
    )

    @model_validator(mode="after")
    def _quote_required_when_present(self) -> "StructuralElement":
        if self.present and not self.quote:
            raise ValueError(
                "quote must be non-empty when present=True — "
                "the audit trail requires evidence for each admitted element"
            )
        return self


class LLMJudgeApprovalJudgment(_Base):
    """W5.2 artifact: the LLM judge's verdict on one (proposal, explanation)
    pair.

    `proposal_id` is echoed back from the input so concurrent
    `runner.gather()` doesn't depend on positional ordering.

    The three element fields are checked structurally; the `verdict`
    is the judge's discretionary call (admit iff all three are present
    AND consistent). The runner enforces:
        all([cluster_referenced.present, change_named.present,
             metric_direction_stated.present]) AND verdict == "admit"
        OR
        verdict == "reject"
    """

    schema_version: Literal["0.1"] = SCHEMA_VERSION
    proposal_id: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "LabeledApprovalCase.case_id (or proposal id in production). "
            "The judge must echo this verbatim from the input so "
            "concurrent runner.gather() doesn't depend on positional "
            "ordering."
        ),
    )
    cluster_referenced: StructuralElement = Field(
        description=(
            "Element 1. Does the explanation reference the failure "
            "cluster the change addresses? Synonyms + paraphrases "
            "count ('the weekend snack cluster', 'the under-forecast "
            "issue we found'); a vague 'this fixes a problem' does "
            "NOT count."
        ),
    )
    change_named: StructuralElement = Field(
        description=(
            "Element 2. Does the explanation describe the change "
            "being made? The change description must be specific "
            "(names a feature added, a threshold tuned, a signal "
            "introduced); 'I made some improvements' does NOT count."
        ),
    )
    metric_direction_stated: StructuralElement = Field(
        description=(
            "Element 3. Does the explanation state which direction "
            "the success metric is expected to move (up / down / "
            "specific direction)? 'this should reduce false alerts' "
            "counts; 'this should be better' does NOT count — better "
            "in which direction is missing."
        ),
    )
    verdict: ApprovalVerdict = Field(
        description=(
            "Binary admit/reject. `admit` iff all three structural "
            "elements are present AND they're mutually consistent. "
            "Reject everything else, including 'all three present "
            "but the direction contradicts the cluster's bias' "
            "(e.g., 'weekend under-forecast cluster' + 'reduces "
            "forecast' + 'lowers recall' — wrong direction)."
        ),
    )
    rationale: str = Field(
        min_length=1,
        max_length=600,
        description=(
            "One- or two-sentence rationale tying the verdict to the "
            "per-element decisions. Surfaces in the audit trail next "
            "to the verdict; what an operator reads when triaging a "
            "judge-rejected proposal."
        ),
    )

    @model_validator(mode="after")
    def _admit_requires_all_elements_present(self) -> "LLMJudgeApprovalJudgment":
        if self.verdict == "admit":
            missing = [
                name
                for name, el in (
                    ("cluster_referenced", self.cluster_referenced),
                    ("change_named", self.change_named),
                    ("metric_direction_stated", self.metric_direction_stated),
                )
                if not el.present
            ]
            if missing:
                raise ValueError(
                    f"verdict='admit' but present=False on: {missing}. "
                    "Admit requires all three structural elements present=True."
                )
        return self


__all__ = [
    "SCHEMA_VERSION",
    "ApprovalVerdict",
    "StructuralElement",
    "LLMJudgeApprovalJudgment",
    "verdict_score",
]
