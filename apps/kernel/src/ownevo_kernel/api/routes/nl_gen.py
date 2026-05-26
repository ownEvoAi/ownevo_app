"""`/api/nl-gen/*` — natural-language → workflow surface.

Two endpoints:

  * `GET /api/nl-gen/preview` (+ `/preview/{id}`) — read-only fixture
    bundle (spec, sim plan, eval case set, metric, meta-eval judgment).
    DB-free, key-free; used by tests and the legacy preview UI.

  * `POST /api/nl-gen/generate` — live LLM call. Takes a description,
    runs `generate_workflow_spec`, persists a `workflows` row, returns
    the new workflow id. Requires `ANTHROPIC_API_KEY` + a DB pool.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal

_log = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ...agents import register_agent
from ...design_agent.log import DesignAgentLog, persist_design_agent_log
from ...nl_gen.design_brief_context import (
    METRIC_DIMENSIONS,
    SIM_PLAN_DIMENSIONS,
    SPEC_DIMENSIONS,
    format_dimensions_block,
)
from ...nl_gen.fixtures import (
    DESCRIPTIONS,
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ...nl_gen.meta_eval import PREVIEW_JUDGMENT_FIXTURES
from ...nl_gen.metric_generator import generate_metric_definition
from ...nl_gen.sim_generator import generate_simulation_plan
from ...nl_gen.workflow_spec_generator import (
    NoToolUseError,
    WorkflowSpecValidationError,
    generate_workflow_spec,
)
from ...tenant_session import acquire_workspace_conn
from .._anthropic_client import build_async_anthropic
from .._demo_gate import DemoGateDep
from .._demo_quota import record_usage as record_demo_usage
from .._demo_token_accountant import TokenAccountant, wrap_client_for_accounting
from ..deps import PrincipalDep

router = APIRouter(prefix="/api/nl-gen", tags=["nl-gen"])

# Description must be substantive enough for NL-gen to anchor a spec, but
# short enough to fit the model's input + the schema-side cap on
# `workflow_spec.description`. Smoketest fixtures are ~700 chars; cap at 4 KB.
_DESCRIPTION_MIN_LEN = 50
_DESCRIPTION_MAX_LEN = 4096

# Allowed shape for a workflow id (matches the kebab-slug rule on WorkflowSpec.id).
_WORKFLOW_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")

# Allowlist of template IDs that may be stored as created_from_template.
# Must stay in sync with VERTICAL_TEMPLATES in apps/web/.../templates.ts.
_VALID_TEMPLATE_IDS: frozenset[str] = frozenset({
    "retail-demand-planning",
    "credit-risk-recalibration",
    "clinical-trial-site-selection",
})


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
    if workflow_id not in PREVIEW_JUDGMENT_FIXTURES:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"No preview judgment for {workflow_id!r}; "
                f"add it to nl_gen/meta_eval/preview_fixtures.py"
            ),
        )
    judgment = PREVIEW_JUDGMENT_FIXTURES[workflow_id]

    return PreviewResponse(
        workflow_id=workflow_id,
        description=DESCRIPTIONS[workflow_id],
        workflow_spec=spec.model_dump(),
        simulation_plan=plan.model_dump(),
        eval_case_set=case_set.model_dump(),
        metric_definition=metric.model_dump(),
        meta_eval_judgment=judgment.model_dump(),
    )


class GenerateRequest(BaseModel):
    """Body shape for `POST /api/nl-gen/generate`."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        min_length=_DESCRIPTION_MIN_LEN,
        max_length=_DESCRIPTION_MAX_LEN,
    )
    workflow_id: str | None = Field(default=None, max_length=64)
    # Vertical-template slug the user picked on /workflows/new.
    # Recorded on the workflow row for analytics. None = free-form description.
    template_id: str | None = Field(default=None, max_length=64)
    # . The design-agent discovery transcript + ambiguity
    # report (if the operator ran the /workflows/new/design flow before
    # generate). Persisted to `workflows.design_agent_log` JSONB column
    # and mirrored into the hash-chained audit trail. None when the
    # operator skipped discovery — backward-compatible with pre-9.1.4
    # web clients.
    design_agent_log: DesignAgentLog | None = None


class GenerateResponse(BaseModel):
    """Body shape for `POST /api/nl-gen/generate` success.

    The full `spec` is returned so the client can render the generated
    artifacts immediately without a follow-up GET.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: str
    description: str
    spec: dict[str, Any]


@router.post(
    "/generate",
    response_model=GenerateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_workflow(
    request: Request,
    body: GenerateRequest,
    principal: PrincipalDep,
    demo: DemoGateDep,
) -> GenerateResponse:
    """Generate a WorkflowSpec from a plain-English description, persist it.

    Calls the existing `generate_workflow_spec` pipeline (Anthropic
    `messages.create` with forced tool-use against the WorkflowSpec
    schema), then inserts the workflow row. No skills are written —
    those land when an iteration runs.

    Errors:
      * **400** — `workflow_id` not a valid kebab slug (when provided)
      * **409** — workflow id already exists
      * **502** — LLM did not emit a tool use, or emitted an invalid spec
      * **503** — `ANTHROPIC_API_KEY` is not set in the kernel env
    """
    if body.workflow_id is not None and not _WORKFLOW_ID_PATTERN.fullmatch(
        body.workflow_id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"workflow_id {body.workflow_id!r} is not a kebab slug "
                "(must match /^[a-z0-9][a-z0-9-]*[a-z0-9]$/)."
            ),
        )

    if body.template_id is not None:
        if not _WORKFLOW_ID_PATTERN.fullmatch(body.template_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"template_id {body.template_id!r} is not a kebab slug "
                    "(must match /^[a-z0-9][a-z0-9-]*[a-z0-9]$/)."
                ),
            )
        if body.template_id not in _VALID_TEMPLATE_IDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"template_id {body.template_id!r} is not a recognised template. "
                    f"Valid values: {sorted(_VALID_TEMPLATE_IDS)}."
                ),
            )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ANTHROPIC_API_KEY is not set in the kernel environment; "
                "the NL-gen endpoint requires it to call the LLM."
            ),
        )

    # Optional model override: caller can target a cheaper / faster /
    # self-hosted model without touching code. `ANTHROPIC_BASE_URL` for
    # the endpoint is honored by `build_async_anthropic`.
    client = build_async_anthropic(api_key)
    nl_gen_model = os.environ.get("OWNEVO_NL_GEN_MODEL") or None
    spec_kwargs: dict[str, str] = {"model": nl_gen_model} if nl_gen_model else {}

    # In demo mode, intercept every messages.create response to accumulate
    # token usage. The accountant is request-scoped — the patched method
    # never escapes this handler because each request builds its own client.
    accountant = TokenAccountant()
    if demo is not None:
        wrap_client_for_accounting(client, accountant)

    # Slice the persisted design-agent transcript per generator. Each
    # generator reads only the dimensions it can encode (spec gets
    # goal/connectors/UI/reviewer, sim_plan gets goal/trigger, metric
    # gets goal/metric). Empty subsets → None → generator skips the
    # block entirely. See `nl_gen/design_brief_context.py` for the
    # canonical assignments.
    spec_brief = format_dimensions_block(body.design_agent_log, SPEC_DIMENSIONS)
    sim_brief = format_dimensions_block(body.design_agent_log, SIM_PLAN_DIMENSIONS)
    metric_brief = format_dimensions_block(body.design_agent_log, METRIC_DIMENSIONS)

    try:
        try:
            spec = await generate_workflow_spec(
                client,
                body.description,
                design_brief_block=spec_brief,
                **spec_kwargs,
            )
        except NoToolUseError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM did not emit a workflow spec: {exc}",
            ) from exc
        except WorkflowSpecValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM produced invalid spec: {exc}",
            ) from exc

        # The improvement loop needs sim_plan + metric to score iterations.
        # Generate them eagerly (2 more LLM calls, ~25-40s each) so the user
        # can run an iteration immediately without a second wait.
        try:
            sim_plan = await generate_simulation_plan(
                client, spec, design_brief_block=sim_brief, **spec_kwargs
            )
            metric_def = await generate_metric_definition(
                client, spec, design_brief_block=metric_brief, **spec_kwargs
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM-side failure during sim/metric generation: {exc}",
            ) from exc
    finally:
        # Record tokens even if the LLM call raised — Anthropic already
        # billed for whatever rounds completed before the failure.
        # Swallow DB errors here so a transient Postgres issue never
        # replaces an informative 502/429 with an opaque 500.
        if demo is not None and (accountant.input_tokens or accountant.output_tokens):
            try:
                async with request.app.state.pool.acquire() as _usage_conn:
                    await record_demo_usage(
                        _usage_conn,
                        demo,
                        input_tokens=accountant.input_tokens,
                        output_tokens=accountant.output_tokens,
                    )
            except Exception as _exc:  # noqa: BLE001
                _log.warning("demo usage recording failed (best-effort): %s", _exc)

    workflow_id = body.workflow_id or spec.id

    pool = request.app.state.pool
    async with (
        acquire_workspace_conn(pool, principal.workspace_id, user_id=principal.user_id) as conn,
        conn.transaction(),
    ):
        row = await conn.fetchrow(
            """
                INSERT INTO workflows (id, description, spec,
                                       simulation_plan, metric_definition,
                                       created_from_template)
                VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
            workflow_id,
            body.description,
            spec.model_dump_json(),
            sim_plan.model_dump_json(),
            metric_def.model_dump_json(),
            body.template_id,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"workflow_id {workflow_id!r} already exists. Pass a "
                    "different workflow_id, or delete the existing row first."
                ),
            )

        if body.design_agent_log is not None:
            await persist_design_agent_log(
                conn,
                workflow_id=workflow_id,
                log=body.design_agent_log,
            )

        await register_agent(
            conn,
            workflow_id=workflow_id,
            description=body.description,
            workflow_origin=None,
        )

    return GenerateResponse(
        workflow_id=workflow_id,
        description=body.description,
        spec=spec.model_dump(),
    )


__all__ = [
    "GenerateRequest",
    "GenerateResponse",
    "PreviewIndex",
    "PreviewIndexEntry",
    "PreviewResponse",
    "router",
]
