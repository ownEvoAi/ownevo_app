"""`/api/design-agent/import-*` — trace-import discovery + generation.

The trace-import counterpart to `design_agent.py`. Where the authoring
surface starts from a free-text description, this surface starts from a
running agent whose traces were just imported (Copilot Studio /
LangSmith / OTel). Two endpoints:

  * `POST /api/design-agent/import-next-question` — given the imported
    `trace_ids`, an optional exported `agent_definition`, and the
    answers collected so far, returns the next discovery question. The
    LLM interviewer reads a deterministic `TraceSummary` of the imported
    traces (so it grounds questions in observed behaviour); on any LLM
    failure it falls back to the static trace-import prompt set so the
    operator is never blocked.

  * `POST /api/design-agent/import-generate` — reverse-engineers a
    WorkflowSpec from the imported traces + the negotiated answers,
    generates the simulation plan + metric, persists a `workflows` row,
    and mirrors the discovery transcript into the audit chain. Returns
    the new workflow id.

Both endpoints are stateless w.r.t. the conversation: the client owns
the transcript and echoes the full `prior_answers` / `design_agent_log`
on every request.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...agents import register_agent
from ...design_agent import (
    DIMENSION_SPECS,
    InterviewerError,
    get_trace_import_discovery_questions,
)
from ...design_agent.import_log import (
    DesignAgentImportLog,
    ReverseDiscoveryRecord,
    persist_design_agent_import_log,
)
from ...design_agent.log import DesignAgentLog
from ...design_agent.reverse_discovery import (
    AGENT_DEFINITION_TRUNCATE,
    ReverseDiscoverySummary,
    fallback_reverse_discovery_summary,
    generate_reverse_discovery_summary,
)
from ...design_agent.trace_interviewer import pick_next_question_from_traces
from ...design_agent.trace_summary import (
    TraceSummary,
    load_trace_events,
    summarize_events,
)
from ...nl_gen.design_brief_context import (
    METRIC_DIMENSIONS,
    SIM_PLAN_DIMENSIONS,
    SPEC_DIMENSIONS,
    format_dimensions_block,
)
from ...nl_gen.metric_generator import generate_metric_definition
from ...nl_gen.sim_generator import generate_simulation_plan
from ...nl_gen.workflow_spec_from_traces import (
    NoToolUseError,
    WorkflowSpecValidationError,
    generate_workflow_spec_from_traces,
)
from .._anthropic_client import build_async_anthropic
from .._demo_gate import DemoGateDep
from .._demo_quota import record_usage as record_demo_usage
from .._demo_token_accountant import TokenAccountant, wrap_client_for_accounting
from .design_agent import (
    NextQuestionResponse,
    PriorAnswerIn,
    _convert_prior_for_interviewer,
    _fallback_question_to_brief,
    _llm_brief_to_response,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/design-agent", tags=["design-agent"])

_MAX_TRACE_IDS = 50
_AGENT_DEFINITION_MAX_LEN = 16_384
_MAX_PRIOR_ANSWERS = 32
_DESCRIPTION_MAX_LEN = 4096


class ImportNextQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_ids: list[UUID] = Field(default_factory=list, min_length=1, max_length=_MAX_TRACE_IDS)
    agent_definition: str | None = Field(
        default=None, max_length=_AGENT_DEFINITION_MAX_LEN
    )
    prior_answers: list[PriorAnswerIn] = Field(
        default_factory=list, max_length=_MAX_PRIOR_ANSWERS
    )


class ReverseDiscoveryIn(BaseModel):
    """The reverse-discovery turn + the reviewer's decision, echoed back
    at generate time so it can be persisted to the import audit log."""

    model_config = ConfigDict(extra="forbid")

    inferred_summary: str = Field(min_length=1, max_length=4096)
    basis: Literal["traces", "definition+traces"]
    source: Literal["llm", "fallback"]
    decision: Literal["confirmed", "corrected", "skipped"]
    final_definition: str | None = Field(
        default=None, max_length=_AGENT_DEFINITION_MAX_LEN
    )

    @model_validator(mode="after")
    def _validate_decision_consistency(self) -> "ReverseDiscoveryIn":
        if self.decision == "corrected" and not self.final_definition:
            raise ValueError(
                "final_definition is required when decision is 'corrected'"
            )
        if self.decision == "skipped" and self.final_definition is not None:
            raise ValueError(
                "final_definition must be None when decision is 'skipped'"
            )
        return self

    def to_record(self) -> ReverseDiscoveryRecord:
        return ReverseDiscoveryRecord(
            inferred_summary=self.inferred_summary,
            basis=self.basis,
            source=self.source,
            decision=self.decision,
            final_definition=self.final_definition,
        )


class ImportGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_ids: list[UUID] = Field(default_factory=list, min_length=1, max_length=_MAX_TRACE_IDS)
    agent_definition: str | None = Field(
        default=None, max_length=_AGENT_DEFINITION_MAX_LEN
    )
    # Vendor the imported agent came from, tagged onto the created workflow so
    # the right fix-delivery action appears later (ship-langsmith /
    # ship-copilot-studio). None = greenfield / undetermined source. The
    # OTLP receiver auto-tags origin on *bound* ingest; the trace-import flow
    # creates a fresh workflow from unbound traces, so the source is passed
    # explicitly here instead.
    origin: Literal["langsmith", "copilot_studio"] | None = None
    reverse_discovery: ReverseDiscoveryIn | None = None
    design_agent_log: DesignAgentLog | None = None
    workflow_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
    )

    @model_validator(mode="after")
    def _corrected_definition_matches_agent_definition(self) -> "ImportGenerateRequest":
        """When the reviewer corrected the inferred summary, the corrected text
        must match `agent_definition` — they are two views of the same value and
        must agree so the audit record accurately describes what was generated from.
        """
        if (
            self.reverse_discovery is not None
            and self.reverse_discovery.decision == "corrected"
            and (self.reverse_discovery.final_definition or "").strip()
            != (self.agent_definition or "").strip()
        ):
            raise ValueError(
                "agent_definition must match reverse_discovery.final_definition "
                "when decision is 'corrected'"
            )
        return self


class ImportGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    description: str
    spec: dict


class ImportSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_ids: list[UUID] = Field(default_factory=list, min_length=1, max_length=_MAX_TRACE_IDS)
    agent_definition: str | None = Field(
        default=None, max_length=_AGENT_DEFINITION_MAX_LEN
    )


class ImportSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    basis: Literal["traces", "definition+traces"]
    source: Literal["llm", "fallback"]
    agent_definition_truncated: bool = False


async def _summarize(request: Request, trace_ids: list[UUID]) -> TraceSummary:
    """Load + summarise the imported traces, or 404 when none resolve."""
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        trace_rows = await load_trace_events(conn, trace_ids)
    if not trace_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "None of the supplied trace_ids resolved to a stored trace. "
                "Import the agent's traces before running discovery."
            ),
        )
    return summarize_events(trace_rows)


def _static_fallback(
    prior_answers: list[PriorAnswerIn],
) -> NextQuestionResponse:
    """Positional walk over the static trace-import prompt set.

    Used when no LLM is available or the interviewer fails. Picks the
    lowest unanswered positional index, exactly like the authoring
    route's hardcoded fallback.
    """
    questions = get_trace_import_discovery_questions()
    total = len(questions)
    answered = len(prior_answers)
    if answered >= total:
        return NextQuestionResponse(
            next_question=None,
            done=True,
            total_questions=total,
            answered_count=total,
        )
    return NextQuestionResponse(
        next_question=_fallback_question_to_brief(
            q=questions[answered], index=answered
        ),
        done=False,
        total_questions=total,
        answered_count=answered,
    )


def _llm_client_or_none():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    if os.environ.get("ANTHROPIC_BASE_URL") == "":
        del os.environ["ANTHROPIC_BASE_URL"]
    try:
        return build_async_anthropic(api_key)
    except Exception:
        return None


def _summary_to_response(
    summary: ReverseDiscoverySummary,
    *,
    agent_definition: str | None = None,
) -> ImportSummaryResponse:
    truncated = bool(
        agent_definition
        and len(agent_definition) > AGENT_DEFINITION_TRUNCATE
    )
    return ImportSummaryResponse(
        summary=summary.summary,
        basis=summary.basis,
        source="fallback" if summary.is_fallback else "llm",
        agent_definition_truncated=truncated,
    )


@router.post(
    "/import-summary",
    response_model=ImportSummaryResponse,
    response_model_exclude_none=True,
)
async def import_summary(
    req: ImportSummaryRequest,
    request: Request,
    demo: DemoGateDep,
) -> ImportSummaryResponse:
    """Open the discovery conversation with a "this agent does X" inference.

    Reverse-engineers a one-to-two-sentence summary of the imported
    agent's observed behaviour (and exported definition, if supplied) so
    the operator confirms or corrects it before the dimension interview
    begins. Falls back to a deterministic render of the trace summary
    when no LLM is available or the LLM call fails — the conversation is
    never blocked.
    """
    trace_summary = await _summarize(request, req.trace_ids)

    client = _llm_client_or_none()
    if client is None:
        return _summary_to_response(
            fallback_reverse_discovery_summary(trace_summary, req.agent_definition),
            agent_definition=req.agent_definition,
        )

    accountant = TokenAccountant()
    if demo is not None:
        wrap_client_for_accounting(client, accountant)
    try:
        try:
            rd_summary = await generate_reverse_discovery_summary(
                summary=trace_summary,
                agent_definition=req.agent_definition,
                client=client,
            )
        except InterviewerError as _ie:
            _log.warning(
                "reverse discovery failed; using deterministic fallback: %s", _ie
            )
            rd_summary = fallback_reverse_discovery_summary(
                trace_summary, req.agent_definition
            )
        return _summary_to_response(rd_summary, agent_definition=req.agent_definition)
    finally:
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


@router.post(
    "/import-next-question",
    response_model=NextQuestionResponse,
    response_model_exclude_none=True,
)
async def import_next_question(
    req: ImportNextQuestionRequest,
    request: Request,
    demo: DemoGateDep,
) -> NextQuestionResponse:
    summary = await _summarize(request, req.trace_ids)
    answered_count = len(req.prior_answers)
    total_questions = len(DIMENSION_SPECS)

    client = _llm_client_or_none()
    accountant = TokenAccountant()
    if client is not None and demo is not None:
        wrap_client_for_accounting(client, accountant)
    if client is not None:
        try:
            try:
                brief = await pick_next_question_from_traces(
                    summary=summary,
                    agent_definition=req.agent_definition,
                    prior_answers=_convert_prior_for_interviewer(req.prior_answers),
                    client=client,
                )
            except InterviewerError as _ie:
                _log.warning(
                    "trace-import interviewer failed; using static fallback: %s", _ie
                )
                brief = None
            else:
                if brief is None:
                    return NextQuestionResponse(
                        next_question=None,
                        done=True,
                        total_questions=total_questions,
                        answered_count=answered_count,
                    )
                return NextQuestionResponse(
                    next_question=_llm_brief_to_response(brief),
                    done=False,
                    total_questions=total_questions,
                    answered_count=answered_count,
                )
        finally:
            if demo is not None and (
                accountant.input_tokens or accountant.output_tokens
            ):
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

    return _static_fallback(req.prior_answers)


@router.post(
    "/import-generate",
    response_model=ImportGenerateResponse,
    response_model_exclude_none=True,
)
async def import_generate(
    req: ImportGenerateRequest,
    request: Request,
    demo: DemoGateDep,
) -> ImportGenerateResponse:
    summary = await _summarize(request, req.trace_ids)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LLM credentials are not configured on this server; "
                "trace-import generation cannot proceed without them."
            ),
        )
    if os.environ.get("ANTHROPIC_BASE_URL") == "":
        del os.environ["ANTHROPIC_BASE_URL"]

    client = build_async_anthropic(api_key)
    nl_gen_model = os.environ.get("OWNEVO_NL_GEN_MODEL") or None
    model_kwargs: dict[str, str] = {"model": nl_gen_model} if nl_gen_model else {}

    accountant = TokenAccountant()
    if demo is not None:
        wrap_client_for_accounting(client, accountant)

    spec_brief = format_dimensions_block(req.design_agent_log, SPEC_DIMENSIONS)
    sim_brief = format_dimensions_block(req.design_agent_log, SIM_PLAN_DIMENSIONS)
    metric_brief = format_dimensions_block(req.design_agent_log, METRIC_DIMENSIONS)

    try:
        try:
            spec = await generate_workflow_spec_from_traces(
                client,
                summary,
                agent_definition=req.agent_definition,
                design_brief_block=spec_brief,
                **model_kwargs,
            )
        except NoToolUseError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM did not emit a workflow spec: {exc}",
            ) from exc
        except WorkflowSpecValidationError as exc:
            _log.error("import-generate: spec validation failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"LLM produced a spec that failed validation after "
                    f"{exc.pydantic_error.error_count()} error(s). "
                    "Check kernel logs for the raw LLM output."
                ),
            ) from exc

        try:
            sim_plan, metric_def = await asyncio.gather(
                generate_simulation_plan(
                    client, spec, design_brief_block=sim_brief, **model_kwargs
                ),
                generate_metric_definition(
                    client, spec, design_brief_block=metric_brief, **model_kwargs
                ),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM-side failure during sim/metric generation: {exc}",
            ) from exc
    finally:
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

    workflow_id = req.workflow_id or spec.id
    # The `workflows.description` column anchors the Operate UI; store a
    # capped render of the observed behaviour so the imported agent reads
    # as "what we saw" rather than a human-written brief.
    description = summary.as_prompt_text()[:_DESCRIPTION_MAX_LEN]

    pool = request.app.state.pool
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
                INSERT INTO workflows (id, description, spec,
                                       simulation_plan, metric_definition,
                                       created_from_template, origin)
                VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, NULL, $6)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
            workflow_id,
            description,
            spec.model_dump_json(),
            sim_plan.model_dump_json(),
            metric_def.model_dump_json(),
            req.origin,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"workflow_id {workflow_id!r} already exists. Pass a "
                    "different workflow_id, or delete the existing row first."
                ),
            )

        import_log = DesignAgentImportLog(
            reverse_discovery=(
                req.reverse_discovery.to_record()
                if req.reverse_discovery is not None
                else None
            ),
            discovery_transcript=(
                req.design_agent_log.discovery_transcript
                if req.design_agent_log is not None
                else ()
            ),
            ambiguity_report=(
                req.design_agent_log.ambiguity_report
                if req.design_agent_log is not None
                else None
            ),
        )
        if not import_log.is_empty():
            await persist_design_agent_import_log(
                conn,
                workflow_id=workflow_id,
                log=import_log,
            )

        await register_agent(
            conn,
            workflow_id=workflow_id,
            description=description,
            workflow_origin=req.origin,
        )

    return ImportGenerateResponse(
        workflow_id=workflow_id,
        description=description,
        spec=spec.model_dump(),
    )


__all__ = [
    "ImportGenerateRequest",
    "ImportGenerateResponse",
    "ImportNextQuestionRequest",
    "ImportSummaryRequest",
    "ImportSummaryResponse",
    "ReverseDiscoveryIn",
    "import_generate",
    "import_next_question",
    "import_summary",
    "router",
]
