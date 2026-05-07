"""ApprovalJudgment — typed schema for the W5.2 LLM-judge stub approver.

The judge takes a `ProposalContext` (cluster name, skill id, metric
name + improvement axis, the proposal's plain-language explanation)
and emits this. Three orthogonal structural checks are required to
admit; the overall decision is mechanically derived (admit iff all
three pass).

Why a typed artifact (not free-form text) — same reasons as the A4.6
`MetaEvalJudgment`: per-check rationales surface in the audit trail,
the verdict scale is closed (`pass` / `fail`), and a regression on one
check shows up cleanly in CI rather than averaging out behind an
overall score.

The three checks (per PLAN.md § Week 5 5.2):

  * `references_cluster` — the explanation refers (verbatim,
    paraphrase, or substantive reference) to the failure cluster being
    addressed. `pass` if the cluster's behaviour is named or a
    paraphrase is present; `fail` if the explanation talks about
    generic "errors" / "issues" without tying back to the specific
    failure mode.
  * `names_change` — the explanation names what changed in the skill
    (e.g., "added Friday-the-13th feature flag", "switched from
    rolling-mean to median imputation"). `fail` for "tuned the model"
    / "made the predictor better" / "fixed it" without specificity.
  * `states_direction` — the explanation states an expected metric
    direction AND that direction is consistent with the metric's
    improvement axis (lower-is-better → "should reduce", higher-is-
    better → "should improve / increase"). `fail` if no expected
    effect is mentioned, OR if the stated direction contradicts the
    improvement axis (this is what catches the "structural-but-wrong-
    direction" reject bucket).

`schema_version="0.1"` matches the meta-eval pre-freeze convention.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1"

StructuralVerdict = Literal["pass", "fail"]
"""Binary verdict per structural check.

  * `pass` — the explanation cleanly satisfies the check.
  * `fail` — the check is not satisfied. For `states_direction`, this
    includes both "no direction stated" and "direction stated but
    contradicts the metric's improvement axis".

Two-level (not three) on purpose: the W5 admit rule is binary
("all three pass → admit"). A `partial` middle band would force the
runtime to choose a tie-breaker policy without a calibration anchor.
The judge is graded on a hard yes/no, which makes the agreement
metric unambiguous.
"""


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StructuralCheck(_Base):
    """One structural-element check on the proposal's explanation."""

    verdict: StructuralVerdict = Field(
        description=(
            "Discrete verdict on this structural element. See module "
            "docstring + `StructuralVerdict` for the per-check criterion."
        ),
    )
    rationale: str = Field(
        min_length=1,
        max_length=400,
        description=(
            "One-line explanation of the verdict. Quotes the explanation's "
            "verbatim phrasing where relevant. Surfaces in the audit trail "
            "next to the auto-decision."
        ),
    )


class ApprovalJudgment(_Base):
    """W5.2 artifact: the LLM-judge stub approver's verdict on one proposal.

    Three orthogonal structural checks + a paragraph rationale. The
    overall admit/reject decision is mechanically derived from the
    three check verdicts (`admits` property) — the judge does not get
    discretion on the admit/reject call, by design: the W5 spec is a
    hard rule ("rejects everything else"), and a discretionary overall
    verdict would let the judge admit explanations that fail one of
    the three checks.

    `proposal_id` ties the judgment back to the proposal judged so a
    future audit-log query can join judgments to gate runs without
    recomputing the input hash.
    """

    schema_version: Literal["0.1"] = SCHEMA_VERSION
    proposal_id: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "The Proposal.id (UUID-as-string) the judgment is for. Echoed "
            "back from input verbatim so the judge can be wrong about "
            "which proposal it just judged — surfaced as "
            "`JudgeProposalIdMismatchError`."
        ),
    )
    references_cluster: StructuralCheck = Field(
        description=(
            "Structural element 1. Does the explanation reference the "
            "failure cluster being addressed? `pass` if the cluster's "
            "behaviour is named or paraphrased; `fail` for generic "
            "'errors' / 'issues' without tying back."
        ),
    )
    names_change: StructuralCheck = Field(
        description=(
            "Structural element 2. Does the explanation name what is "
            "changing in the skill? `pass` if the change is specified "
            "(e.g., 'added X feature', 'switched Y to Z'); `fail` for "
            "'tuned the model' / 'fixed it' without specificity."
        ),
    )
    states_direction: StructuralCheck = Field(
        description=(
            "Structural element 3. Does the explanation state an expected "
            "metric direction consistent with the metric's improvement "
            "axis? `pass` for 'should reduce RMSSE' on a lower-is-better "
            "metric or 'should improve recall' on a higher-is-better "
            "metric; `fail` if no direction stated OR the stated "
            "direction contradicts the improvement axis."
        ),
    )
    overall_rationale: str = Field(
        min_length=1,
        max_length=600,
        description=(
            "One-paragraph rationale tying the three check verdicts "
            "together. Surfaces in the audit trail next to the "
            "auto-decision; what an operator reads when triaging a "
            "judge-rejected proposal."
        ),
    )

    @property
    def admits(self) -> bool:
        """Mechanical admit rule: all three structural checks must pass.

        The judge does not get discretion on the overall call (see
        class docstring). Read this property to get the binary
        admit/reject the runtime acts on.
        """
        return (
            self.references_cluster.verdict == "pass"
            and self.names_change.verdict == "pass"
            and self.states_direction.verdict == "pass"
        )


__all__ = [
    "SCHEMA_VERSION",
    "StructuralVerdict",
    "StructuralCheck",
    "ApprovalJudgment",
]
