"""`/api/design-agent/ambiguity-report` — post-generation ambiguity scan.

Stateless. The web layer holds the just-generated WorkflowSpec (and
optional MetricDefinition) in memory after `POST /api/nl-gen/generate`
and posts them straight back here for analysis. Returns an
`AmbiguityReport` the design-agent UI renders as additional questions
in the discovery conversation.

No DB read, no LLM call. The whole pass is deterministic — pure
function of (description, spec, metric_definition) — so the endpoint
is cheap to call and trivial to test without a kernel boot.

A future slice extends this with an LLM-judge variant for the
cross-artifact conflict pass; the endpoint contract stays the same and
clients opt in via a query flag.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ...design_agent.ambiguity import AmbiguityReport, analyze_workflow
from ...nl_gen.metric_def import MetricDefinition
from ...nl_gen.spec import WorkflowSpec

router = APIRouter(prefix="/api/design-agent", tags=["design-agent"])

_DESCRIPTION_MAX_LEN = 4096


class AmbiguityReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=_DESCRIPTION_MAX_LEN)
    spec: WorkflowSpec
    metric_definition: MetricDefinition | None = None


@router.post("/ambiguity-report", response_model=AmbiguityReport)
def ambiguity_report(req: AmbiguityReportRequest) -> AmbiguityReport:
    return analyze_workflow(
        description=req.description,
        spec=req.spec,
        metric_definition=req.metric_definition,
    )
