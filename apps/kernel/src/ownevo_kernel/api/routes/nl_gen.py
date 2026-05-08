"""`/api/nl-gen/preview` — W5.5 NL-gen UI preview surface.

Serves the four NL-gen artifacts (spec, sim plan, eval case set,
metric definition) plus a pre-computed `MetaEvalJudgment` for one of
the three production fixtures. The web app's `/workflows/preview`
route renders them with the W5.5 coverage badge as the headliner.

This endpoint is **read-only and DB-free** — it returns deterministic
hand-authored fixture data so:

  * `make web-dev` works without a Postgres or an Anthropic key.
  * The UI contract for the coverage badge can be exercised in CI
    without consuming API tokens (project policy).
  * The W6 follow-up (`POST /api/nl-gen/generate`) plugs into the
    same response shape, so the web UI doesn't need to swap when
    the live wire-up lands.

`provenance="preview-fixture"` on every response so the UI can show
a "demo data" banner if it ever ships beyond the preview route.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from ...nl_gen.fixtures import (
    DESCRIPTIONS,
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ...nl_gen.meta_eval import PREVIEW_JUDGMENT_FIXTURES

router = APIRouter(prefix="/api/nl-gen", tags=["nl-gen"])


class PreviewResponse(BaseModel):
    """Wire shape for the W5.5 UI preview.

    The four NL-gen artifacts ship as `dict[str, Any]` so the web app
    can render them without re-deriving the Pydantic schemas in TS.
    Each is the exact `model_dump_json` of the matching fixture, so
    the wire shape is byte-identical to what a future `generate`
    endpoint would emit.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: str
    description: str
    workflow_spec: dict[str, Any]
    simulation_plan: dict[str, Any]
    eval_case_set: dict[str, Any]
    metric_definition: dict[str, Any]
    meta_eval_judgment: dict[str, Any]
    provenance: Literal["preview-fixture"] = "preview-fixture"


class PreviewIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: str
    description: str


class PreviewIndex(BaseModel):
    """List of available preview fixtures so the web UI can offer a
    workflow picker without hard-coding ids."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[PreviewIndexEntry]


@router.get("/preview", response_model=PreviewIndex)
async def preview_index() -> PreviewIndex:
    """List every fixture id the preview endpoint can serve.

    The web UI uses this to render the "demo workflow" picker without
    having to reach into the kernel package itself.
    """
    return PreviewIndex(
        items=[
            PreviewIndexEntry(
                workflow_id=workflow_id,
                description=DESCRIPTIONS[workflow_id],
            )
            for workflow_id in sorted(FIXTURES)
        ]
    )


@router.get("/preview/{workflow_id}", response_model=PreviewResponse)
async def preview_one(workflow_id: str) -> PreviewResponse:
    """Return the full preview bundle for one fixture id.

    404 when the id is unknown so the web app can render a not-found
    page rather than a 500 trace.
    """
    if workflow_id not in FIXTURES:
        known = sorted(FIXTURES)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Unknown preview workflow id {workflow_id!r}; "
                f"available: {known}"
            ),
        )

    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]
    judgment = PREVIEW_JUDGMENT_FIXTURES[workflow_id]

    return PreviewResponse(
        workflow_id=workflow_id,
        description=DESCRIPTIONS[workflow_id],
        workflow_spec=json.loads(spec.model_dump_json()),
        simulation_plan=json.loads(plan.model_dump_json()),
        eval_case_set=json.loads(case_set.model_dump_json()),
        metric_definition=json.loads(metric.model_dump_json()),
        meta_eval_judgment=json.loads(judgment.model_dump_json()),
    )


__all__ = ["PreviewIndex", "PreviewIndexEntry", "PreviewResponse", "router"]
