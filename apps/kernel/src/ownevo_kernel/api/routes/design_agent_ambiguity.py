"""`/api/design-agent/*` — ambiguity-detection endpoints.

Two stateless endpoints:

  * `POST /ambiguity-report` — **post-generation** scan. The web layer
    holds the just-generated WorkflowSpec (and optional MetricDefinition)
    in memory after `POST /api/nl-gen/generate` and posts them back here.
    Returns an `AmbiguityReport` with `workflow_spec_id` set.

  * `POST /description-conflicts` — **pre-generation** scan over the
    raw description, before NL-gen has produced a spec. Used by the
    discovery chat panel to surface contradictions
    ("maximize recall, zero false positives") as additional questions
    before the operator clicks Generate. Returns a bare `findings` list
    (no spec, so no spec_id and no `AmbiguityReport` envelope) — the
    operator's answers are persisted as additional `discovery_transcript`
    entries on the workflow row at generate time.

No DB read, no LLM call. Both passes are deterministic — pure
functions of their inputs — so the endpoints are cheap to call and
trivial to test without a kernel boot.

A future slice extends `/ambiguity-report` with an LLM-judge variant
for cross-artifact conflicts; the endpoint contract stays the same
and clients opt in via a query flag.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ...design_agent.ambiguity import (
    AmbiguityFinding,
    AmbiguityReport,
    analyze_workflow,
    find_description_conflicts,
)
from ...nl_gen.metric_def import MetricDefinition
from ...nl_gen.spec import WorkflowSpec

router = APIRouter(prefix="/api/design-agent", tags=["design-agent"])

_DESCRIPTION_MAX_LEN = 4096


class AmbiguityReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=50, max_length=_DESCRIPTION_MAX_LEN)
    spec: WorkflowSpec
    metric_definition: MetricDefinition | None = None


@router.post("/ambiguity-report", response_model=AmbiguityReport, response_model_exclude_none=True)
def ambiguity_report(req: AmbiguityReportRequest) -> AmbiguityReport:
    return analyze_workflow(
        description=req.description,
        spec=req.spec,
        metric_definition=req.metric_definition,
    )


class DescriptionConflictsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=50, max_length=_DESCRIPTION_MAX_LEN)


class DescriptionConflictsResponse(BaseModel):
    """The pre-generation conflict scan. No spec means no `workflow_spec_id`
    and no full `AmbiguityReport` envelope — just the bare findings the
    design agent should surface as additional discovery questions before
    Generate enables."""

    model_config = ConfigDict(extra="forbid")

    findings: tuple[AmbiguityFinding, ...] = Field(default_factory=tuple)


@router.post(
    "/description-conflicts",
    response_model=DescriptionConflictsResponse,
    response_model_exclude_none=True,
)
def description_conflicts(
    req: DescriptionConflictsRequest,
) -> DescriptionConflictsResponse:
    return DescriptionConflictsResponse(
        findings=find_description_conflicts(req.description),
    )
