"""Spec-shaping dimensions the design-agent interview must cover.

The design agent's job at authoring time is to make the domain expert
the source of truth on every decision NL-gen would otherwise have to
guess at. Each `Dimension` below names one decision surface; the
interviewer iterates until every dimension has at least one answer
recorded against it, then declares the interview done.

Why a checklist instead of a fixed question list:
  * Different domains need different questions on the SAME dimension
    (a hospital answers `data_sources_and_connectors` very differently
    from a retail planner), so we let the LLM phrase the question with
    the description in context.
  * The operator's earlier answers should shape later questions ("you
    said the metric optimises recall — what's the per-FN cost?"),
    which a static prompt library can't express.
  * The kernel still owns *coverage*: the LLM doesn't get to declare
    the interview done until every dimension has at least one answer
    or an explicit "not applicable / skip" recorded.

Order is the recommended ask order. Earlier dimensions inform later
ones (goal shapes metric; cadence shapes eval seed shapes; etc.), so
the interviewer prefers an un-covered dimension closer to the top of
the list when multiple are still open.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

DesignDimension = Literal[
    "goal_and_scope",
    "trigger_and_cadence",
    "data_sources_and_connectors",
    "success_metric",
    "eval_seed_cases",
    "operate_ui_primitives",
    "reviewer_role",
]
"""The dimensions the design-agent interview covers.

Stable identifiers — persisted to the audit log and the workflow's
`design_agent_log` jsonb, so renames need a migration.
"""

DESIGN_DIMENSIONS: tuple[str, ...] = get_args(DesignDimension)


class DimensionSpec(BaseModel):
    """Describes one dimension for the LLM interviewer and for clients.

    `key` matches a `DesignDimension` literal; `label` is the
    operator-facing name; `intent` is what the interviewer should
    confirm; `informs` lists the NL-gen artifacts this dimension's
    answer feeds into (used by downstream consumers, not by the
    interviewer itself).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: DesignDimension
    label: str
    intent: str = Field(
        description=(
            "What the interviewer is trying to learn on this dimension. "
            "Passed into the LLM prompt so the model knows what 'covered' "
            "means."
        ),
    )
    informs: tuple[str, ...] = Field(
        description=(
            "Downstream NL-gen artifacts this dimension's answer shapes "
            "(WorkflowSpec / SimulationPlan / EvalCaseSet / "
            "MetricDefinition / UIPlan)."
        ),
    )


DIMENSION_SPECS: tuple[DimensionSpec, ...] = (
    DimensionSpec(
        key="goal_and_scope",
        label="Goal & scope",
        intent=(
            "Pin down the workflow's primary outcome in operator-actionable "
            "terms: what decision or recommendation is the agent producing, "
            "and what is explicitly out of scope. Push back on vague verbs "
            "('optimise', 'monitor') — surface the concrete action."
        ),
        informs=("WorkflowSpec",),
    ),
    DimensionSpec(
        key="trigger_and_cadence",
        label="Trigger & cadence",
        intent=(
            "When does the workflow fire in production? Scheduled "
            "(daily/weekly/monthly), event-driven (new data arrives, "
            "threshold crossed), or on-demand by the operator? Shapes "
            "which trace slices the regression gate replays against and "
            "the SimulationPlan's step rhythm."
        ),
        informs=("SimulationPlan", "WorkflowSpec.environment.env_generators"),
    ),
    DimensionSpec(
        key="data_sources_and_connectors",
        label="Data sources & connectors",
        intent=(
            "Which systems does the agent read at execution time, and which "
            "are already integrated vs. need new connectors? Name vendors "
            "(SAP, Salesforce, Epic, NOAA) and feeds (sales history, weather, "
            "ticket queue). Distinguish 'available today' from "
            "'aspirational' — only the former should land in spec.tools "
            "without a flag."
        ),
        informs=("WorkflowSpec.environment.data_sources", "WorkflowSpec.tools"),
    ),
    DimensionSpec(
        key="success_metric",
        label="Success metric",
        intent=(
            "How is correctness scored? Surface the dominant error mode "
            "(false positives vs. false negatives), the metric family "
            "(recall / precision / balanced_accuracy / f1), and the target "
            "threshold the domain expert considers 'good enough to ship'."
        ),
        informs=("MetricDefinition",),
    ),
    DimensionSpec(
        key="eval_seed_cases",
        label="Eval seed cases",
        intent=(
            "What specific historical cases must the eval suite include? "
            "Past misses, edge cases, sector / region / vintage variants. "
            "The expert names them concretely (case ids, vendor names, "
            "dates) so NL-gen can mint seed cases that reflect their "
            "actual failure mode catalogue."
        ),
        informs=("EvalCaseSet",),
    ),
    DimensionSpec(
        key="operate_ui_primitives",
        label="Operate-view UI",
        intent=(
            "Which renderable primitives match the operator's daily "
            "workflow? Pick from MetricCards, TimeSeriesChart, TableView, "
            "AlertList, KanbanBoard, ScheduleGrid, ConversationView, "
            "SideBySideView, DocumentReader. The choice shapes what the "
            "agent's `output_payload_json` is expected to carry."
        ),
        informs=("WorkflowSpec.ui.tabs[*].primitives",),
    ),
    DimensionSpec(
        key="reviewer_role",
        label="Reviewer & cadence",
        intent=(
            "Who reviews proposed instruction edits, and how often? The "
            "reviewer's role title and review cadence are recorded in the "
            "WorkflowSpec's reviewer field; they shape the proposal-queue "
            "framing in the inbox."
        ),
        informs=("WorkflowSpec.reviewer",),
    ),
)
"""Ordered tuple of all dimensions the interview must cover.

Position matters: the interviewer prefers earlier-in-tuple dimensions
when multiple are still open, because earlier answers inform later
questions.
"""


_SPEC_BY_KEY: dict[str, DimensionSpec] = {d.key: d for d in DIMENSION_SPECS}


def spec_for(key: str) -> DimensionSpec | None:
    """Look up a `DimensionSpec` by key; None when key is unknown."""
    return _SPEC_BY_KEY.get(key)


def dimensions_remaining(
    covered_keys: set[str],
) -> tuple[DimensionSpec, ...]:
    """Return dimensions not yet covered, in canonical ask order."""
    return tuple(d for d in DIMENSION_SPECS if d.key not in covered_keys)


__all__ = [
    "DESIGN_DIMENSIONS",
    "DIMENSION_SPECS",
    "DesignDimension",
    "DimensionSpec",
    "dimensions_remaining",
    "spec_for",
]
