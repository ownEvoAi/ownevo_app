"""MetaEvalJudgment — typed schema for the A4.6 NL-gen meta-eval judge.

The meta-eval judge is the LLM-as-judge that scores generated NL-gen
artifacts (sim plan + eval case set + metric definition) against the
plain-English description that drove the generation. It is the W4
quality gate D7 calls for: catches "the sim looks fine but doesn't
actually instantiate the entities the description mentioned" and
"the metric is structurally valid but contradicts the past-miss
asymmetry" — failure modes that pass A4.1's structural validators
+ A4.2's direction lock but still ship junk to the agent loop.

Why a typed artifact (not free-form text):

  * **Per-dimension scoring** — three orthogonal questions
    (sim_coverage, eval_case_coverage, metric_alignment) need
    independent verdicts so a regression on one dimension shows up
    cleanly in CI rather than averaging out behind an overall score.
  * **Closed verdict scale** — `pass / partial / fail` per dimension
    + binary `good / bad` overall. Discrete picks are something
    small models can ground reliably; continuous floats invite the
    "everything is 0.7" failure mode the eval-case generator hit
    before we tightened its prompt.
  * **Structured rationale** — every verdict carries a one-line
    rationale so the audit trail doesn't degenerate to "score=0.4"
    with no explanation. The supply chain VP reading the lift chart
    sees *why* the meta-eval judge held a workflow back, in plain
    English, next to the verdict.

The judge is validated in W5 (A5.5): hand-labeled eval set ≥10
descriptions × {good, bad} pairs; judge-vs-human agreement on the
overall verdict must reach ≥0.7. Per-dimension verdicts feed the
"sim covers 11/12 of your description" coverage badge in the
approval UI (A5.1).

`schema_version="0.1"` until the A4-end freeze (mirrors A3.1/A3.2
pre-freeze convention). When the freeze fires, the snapshot lands
at `nl_gen/schemas/meta_eval_judgment.v1.0.json`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1"

DimensionVerdict = Literal["pass", "partial", "fail"]
"""Three-level discrete scale for per-dimension verdicts.

  * `pass` — the dimension's question is answered cleanly (every
    description-named entity appears in the sim; every described
    behavior has at least one eval case; the metric family + target
    are aligned with the spec's framing).
  * `partial` — most coverage is there but a non-trivial gap remains.
    Surfaces in the UI as a warning rather than a hard block.
  * `fail` — the dimension's contract is broken (an entity is
    missing, an eval case set is off-topic, the metric direction
    contradicts the past-miss).

Numeric mapping (`dimension_score`): pass=1.0, partial=0.5, fail=0.0.
The mean of dimension scores is `MetaEvalJudgment.aggregate_score()`,
which is the single-number summary the runner reports.
"""

OverallVerdict = Literal["good", "bad"]
"""Binary overall verdict.

A bundle is `good` when it's safe to feed into the agent loop —
covers the description, has a defensible eval set, has an aligned
metric. A bundle is `bad` when at least one dimension fails badly
enough that the agent loop's outcomes won't be interpretable.

`good` does not require all three dimensions = pass; the judge has
discretion to call `good` on (pass, pass, partial) where the
`partial` is a benign omission. The spec validator below does not
mechanically derive overall from dimension verdicts, on purpose —
the LLM judge calibrates the overall verdict against examples the
human labeler tagged.
"""


_DIMENSION_TO_SCORE: dict[DimensionVerdict, float] = {
    "pass": 1.0,
    "partial": 0.5,
    "fail": 0.0,
}


def dimension_score(verdict: DimensionVerdict) -> float:
    """Map a per-dimension verdict to a numeric score in [0, 1]."""
    return _DIMENSION_TO_SCORE[verdict]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetaEvalDimension(_Base):
    """One dimension of the meta-eval judgment.

    A pass/partial/fail verdict + a one-line rationale. Three of
    these compose the full judgment.
    """

    verdict: DimensionVerdict = Field(
        description=(
            "Discrete verdict on this dimension. See module docstring + "
            "`DimensionVerdict` for the per-level criterion."
        ),
    )
    rationale: str = Field(
        min_length=1,
        max_length=600,
        description=(
            "One-line explanation of the verdict. Quotes the description's "
            "verbatim phrasing where relevant. Surfaces in the audit trail + "
            "the approval UI's coverage badge."
        ),
    )


class MetaEvalJudgment(_Base):
    """A4.6 artifact: the LLM-as-judge's verdict on one generated bundle.

    The judge takes (description, WorkflowSpec, SimulationPlan,
    EvalCaseSet, MetricDefinition) and emits this. Three orthogonal
    dimensions + an overall binary verdict + an aggregate rationale.

    `workflow_spec_id` ties the judgment back to the bundle judged so
    a future audit-log query can join judgments to gate runs without
    recomputing the bundle hash.
    """

    schema_version: Literal["0.1"] = SCHEMA_VERSION
    workflow_spec_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
        description="WorkflowSpec.id of the bundle this judgment is for.",
    )
    sim_coverage: MetaEvalDimension = Field(
        description=(
            "Dimension 1. Does the SimulationPlan instantiate every entity, "
            "condition, and objective the description mentioned? `pass` if "
            "every description-named entity has a counterpart in the sim's "
            "agents/data_sources/personas; `partial` if one minor entity is "
            "missing; `fail` if a load-bearing entity is absent or the sim "
            "is in a different domain entirely."
        ),
    )
    eval_case_coverage: MetaEvalDimension = Field(
        description=(
            "Dimension 2. Do the eval cases cover the described behaviors? "
            "`pass` if every documented past-miss has at least one case "
            "exercising it AND class balance reflects the description's "
            "framing; `partial` if past-miss coverage is partial OR class "
            "balance is skewed; `fail` if cases are off-topic or the set "
            "doesn't exercise the workflow's key decision."
        ),
    )
    metric_alignment: MetaEvalDimension = Field(
        description=(
            "Dimension 3. Is the metric bounded and aligned with the "
            "description? `pass` if the family matches the past-miss "
            "asymmetry (recall for missed-positives, precision for false-"
            "alerts) AND the threshold is achievable + non-trivial; "
            "`partial` if the family is defensible but suboptimal OR the "
            "threshold is too aggressive/lax; `fail` if the family "
            "contradicts the past-miss framing or the threshold is "
            "unreachable (1.0) or trivially-passable (0.0)."
        ),
    )
    overall_verdict: OverallVerdict = Field(
        description=(
            "Binary overall. `good` = safe to feed to the agent loop; "
            "`bad` = at least one dimension fails badly enough that the "
            "agent loop's outcomes won't be interpretable. Not "
            "mechanically derived from per-dimension verdicts — the "
            "judge has discretion on borderline cases."
        ),
    )
    overall_rationale: str = Field(
        min_length=1,
        max_length=800,
        description=(
            "One-paragraph rationale tying the overall verdict to the "
            "dimension verdicts. Surfaces in the audit trail next to "
            "the verdict; what an operator reads when triaging a "
            "judge-rejected workflow."
        ),
    )

    def aggregate_score(self) -> float:
        """Mean of the three per-dimension numeric scores in [0, 1].

        pass=1.0, partial=0.5, fail=0.0. The runner reports this as
        the single-number summary alongside the binary overall_verdict
        — useful for tracking judge calibration over time.
        """
        return (
            dimension_score(self.sim_coverage.verdict)
            + dimension_score(self.eval_case_coverage.verdict)
            + dimension_score(self.metric_alignment.verdict)
        ) / 3.0


__all__ = [
    "SCHEMA_VERSION",
    "DimensionVerdict",
    "OverallVerdict",
    "MetaEvalDimension",
    "MetaEvalJudgment",
    "dimension_score",
]
