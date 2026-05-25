"""`/api/otel/v1/traces` — OTLP-JSON/protobuf ingest entry point.

Accepts a single OTLP `ResourceSpans` batch as JSON or protobuf (the encodings
LangSmith's `langsmith-collector-proxy` and OpenLLMetry exporters emit by
default) and decodes it into typed `AgentEvent`s via
`middleware.otel_receiver`, then writes one `traces` row per unique `trace_id`
in the batch. After this call returns, the ingested traces are first-class
citizens for the failure clustering pipeline, the inspection UI, and the rest
of the kernel.

Auth: the route requires an `Authorization: Bearer ownevo_rt_...` token minted
via `apps/kernel/scripts/mint_receiver_token.py`. The token's bound workflow
(if any) determines `traces.workflow_id` on the ingested rows. Tests and
local-dev flows can opt out by setting `OWNEVO_OTLP_AUTH_OPTIONAL=true` — see
`middleware/otel_receiver/auth.py`.

Both OTLP-HTTP encodings are accepted:
  * JSON — the default for `langsmith-collector-proxy`; Google ADK and
    watsonx / traceloop vendor keys are translated to `gen_ai.*` semconv
    before decode.
  * Protobuf (`Content-Type: application/x-protobuf`) — the default for
    OpenLLMetry / traceloop and most stock OTel SDKs. Already in standard
    semconv; vendor-key translation is skipped.

Out of scope for this slice:

  * gRPC OTLP — HTTP transport only (JSON or protobuf).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from ...agents import register_agent_for_workflow
from ...middleware.google_adk.translator import _walk_and_rewrite_inplace as _adk_rewrite_inplace
from ...middleware.otel_receiver import (
    DEFAULT_MAX_BODY_BYTES,
    DecodedBatch,
    OtelDecodeError,
    OversizedPayloadError,
    ReceiverTokenAuth,
    ReceiverTokenAuthError,
    decode_otlp_payload,
    decode_otlp_protobuf,
    persist_decoded_batch,
    verify_request_token,
)
from ...middleware.watsonx_adk.translator import (
    _walk_and_rewrite_inplace as _watsonx_rewrite_inplace,
)
from ..deps import ConnDep

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/otel", tags=["otel-ingest"])


def _parse_translate_decode(raw: bytes, max_body_bytes: int) -> DecodedBatch:
    """Parse raw OTLP-JSON bytes, apply vendor-key translators, then decode.

    Two translators run in sequence on a single shared deep-copy:
      * `_adk_rewrite_inplace` rewrites Google ADK's
        `gcp.vertex.agent.*` keys onto the standard `gen_ai.*` semconv.
      * `_watsonx_rewrite_inplace` rewrites Traceloop / OpenLLMetry
        `traceloop.*` keys (emitted by watsonx Orchestrate ADK and any
        OpenLLMetry-instrumented agent) onto the same `gen_ai.*` semconv.

    Both translators are conditional (no-op when standard keys are already
    present). Order does not matter because each targets a disjoint vendor
    namespace. The single deep-copy is shared so non-vendor payloads pay
    only one allocation + two attribute-list walks instead of two allocations.

    JSON parse errors are promoted to `OtelDecodeError` so the route handler's
    existing 400 path handles them without a separate except clause.
    """
    try:
        parsed: Any = json.loads(raw)
    except ValueError as exc:
        raise OtelDecodeError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise OtelDecodeError(
            "OTLP-JSON payload must be a JSON object, "
            f"got {type(parsed).__name__}"
        )
    out: dict[str, Any] = copy.deepcopy(parsed)
    _adk_rewrite_inplace(out)
    _watsonx_rewrite_inplace(out)
    return decode_otlp_payload(
        out,
        max_body_bytes=max_body_bytes,
    )


def _batch_has_tool_failure(batch: DecodedBatch) -> bool:
    """True when the batch carries at least one failed tool call.

    The auto-clustering trigger only fires on batches that actually
    introduce a failure signal — clustering a workflow whose newest
    traces are all clean would just re-derive the existing clusters.
    """
    return any(
        getattr(event, "type", None) == "tool_call_result"
        and getattr(event, "status", None) == "error"
        for event in batch.events
    )


class IngestWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_id: str | None
    reason: str


class IngestResponse(BaseModel):
    """Response envelope returned by `/api/otel/v1/traces`.

    `created_trace_ids` lists trace_ids that resulted in a new
    `traces` row; `appended_trace_ids` lists trace_ids whose existing
    row was extended with additional events (the common case when an
    external collector flushes spans in waves); `saturated_trace_ids`
    lists trace_ids that were dropped at the persist layer because
    they would push the per-trace event count past the safety cap
    (see `_MAX_EVENTS_PER_TRACE` in `persist.py`). The split lets a
    calling collector decide whether to surface a "new trace" event
    upstream or pause emitting against a saturated trace, without
    re-querying the database.
    """

    model_config = ConfigDict(extra="forbid")

    accepted_events: int
    warnings: list[IngestWarning]
    created_trace_ids: list[UUID]
    appended_trace_ids: list[UUID]
    saturated_trace_ids: list[UUID]


async def _read_body_with_limit(request: Request, max_bytes: int) -> bytes:
    """Stream the request body up to `max_bytes`.

    Starlette's `request.body()` buffers the entire body in memory
    before returning, which means chunked-transfer payloads can exhaust
    RAM before the byte-cap is enforced. This helper reads in chunks and
    aborts as soon as the running total exceeds the cap.

    A fast path checks the declared `Content-Length` header first so
    well-behaved callers get an immediate 413 without streaming any data.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise OversizedPayloadError(
                    f"Content-Length {content_length} bytes exceeds cap {max_bytes}",
                )
        except ValueError:
            pass  # malformed header — let the body read decide

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise OversizedPayloadError(
                f"payload exceeds cap {max_bytes} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _resolve_workflow_id(
    conn: ConnDep,
    auth: ReceiverTokenAuth | None,
    query_workflow_id: str | None,
) -> str | None:
    """Decide which workflow the batch binds to.

    Precedence:
      * Token bound to a workflow → that workflow wins. A `?workflow_id=`
        that names a *different* workflow is a cross-write attempt and is
        rejected with 403 (the bound token is not allowed to write
        elsewhere). A matching query param is harmless and accepted.
      * Token workflow-agnostic (or no token in auth-optional mode) →
        the `?workflow_id=` query param binds the batch, after checking
        the workflow exists (404 otherwise). Absent → unbound (None).
    """
    token_wf = auth.workflow_id if auth is not None else None
    # Normalise empty string to None — FastAPI passes ?workflow_id= as ""
    # and an empty string is never a valid workflow id.
    query_workflow_id = query_workflow_id or None

    if token_wf is not None:
        if query_workflow_id is not None and query_workflow_id != token_wf:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="token is bound to a different workflow",
            )
        return token_wf

    if query_workflow_id is not None:
        exists = await conn.fetchval(
            "SELECT 1 FROM workflows WHERE id = $1", query_workflow_id
        )
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"workflow {query_workflow_id!r} not found",
            )
        return query_workflow_id

    return None


async def _apply_provenance_hints(
    conn: ConnDep,
    workflow_id: str,
    batch: DecodedBatch,
) -> None:
    """Auto-tag workflow.origin and back-fill skills.langsmith_prompt_id.

    Both use COALESCE / `IS NULL` guards so a value already set (manually
    or by an earlier batch) is never overwritten — the first signal wins
    and the manual binding picker stays authoritative.

    Prompt-id binding only fires when the batch carried exactly one
    distinct prompt id; multiple distinct ids are ambiguous (which skill
    maps to which?) so we skip and leave it to the manual picker.
    """
    if batch.detected_origin is not None:
        await conn.execute(
            "UPDATE workflows SET origin = $1 WHERE id = $2 AND origin IS NULL",
            batch.detected_origin,
            workflow_id,
        )

    prompt_ids = batch.detected_prompt_ids
    if len(prompt_ids) == 1:
        (prompt_id,) = tuple(prompt_ids)
        # Auto-bind only when the workflow has exactly ONE skill. With
        # multiple skills, one prompt ID is ambiguous (different skills may
        # map to different LangSmith prompts), and stamping all unbound skills
        # with the same id would cause ship-langsmith to push to the wrong
        # prompt for any secondary skill. Leave multi-skill workflows to the
        # manual binding picker.
        skill_count = await conn.fetchval(
            "SELECT COUNT(*) FROM skills WHERE workflow_id = $1",
            workflow_id,
        )
        if skill_count == 1:
            await conn.execute(
                "UPDATE skills SET langsmith_prompt_id = $1 "
                "WHERE workflow_id = $2 AND langsmith_prompt_id IS NULL",
                prompt_id,
                workflow_id,
            )
        else:
            _log.info(
                "otel-ingest: 1 distinct prompt id but %d skills on workflow %s — "
                "skipping auto-bind (ambiguous for multi-skill workflows); "
                "use the manual binding picker",
                skill_count,
                workflow_id,
            )
    elif len(prompt_ids) > 1:
        _log.info(
            "otel-ingest: %d distinct prompt ids in batch for workflow %s — "
            "skipping auto-bind (ambiguous); use the manual binding picker",
            len(prompt_ids),
            workflow_id,
        )


@router.post(
    "/v1/traces",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
)
async def ingest_otlp_traces(
    request: Request,
    conn: ConnDep,
    authorization: str | None = Header(default=None),
    workflow_id: str | None = Query(default=None),
) -> IngestResponse:
    """Accept one OTLP-JSON or OTLP-protobuf ResourceSpans batch and persist it.

    Decoded events land in the `traces` table — one row per unique
    `AgentEvent.trace_id`, bound to the workflow resolved from the receiver
    token (or the `?workflow_id=` query param for workflow-agnostic tokens).
    Subsequent batches for the same trace_id append onto the existing row's
    JSONB events array (the common pattern when an external collector flushes
    spans in waves).

    The body is streamed with a byte cap to prevent unbounded memory
    accumulation from chunked-transfer payloads. The CPU-bound decode is
    offloaded to a thread-pool executor so the event loop stays free during
    large-batch processing.

    Authentication happens before body read so a bad token rejects cheaply
    without consuming a multi-MiB upload.
    """
    try:
        auth = await verify_request_token(conn, authorization)
    except ReceiverTokenAuthError:
        # Uniform 401 across every failure mode — revealing "revoked"
        # vs "unknown" vs "malformed" gives an attacker free information.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid receiver token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    bound_workflow_id = await _resolve_workflow_id(conn, auth, workflow_id)

    if auth is not None:
        _log.debug(
            "otel-ingest: authenticated token_id=%s workflow_id=%s",
            auth.token_id,
            bound_workflow_id,
        )

    try:
        raw = await _read_body_with_limit(request, DEFAULT_MAX_BODY_BYTES)
    except OversizedPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc

    # OpenLLMetry / traceloop and most OTel SDKs emit OTLP-HTTP protobuf by
    # default; langsmith-collector-proxy emits OTLP-JSON. Branch on the
    # declared Content-Type — anything that isn't protobuf is treated as JSON.
    # Protobuf payloads already use standard OTel semconv; vendor-key
    # translation via ADK/watsonx rewriters is skipped for that path.
    content_type = request.headers.get("content-type", "").lower()
    is_protobuf = "application/x-protobuf" in content_type or (
        "application/protobuf" in content_type
    )
    decode_fn = decode_otlp_protobuf if is_protobuf else _parse_translate_decode

    try:
        batch = await asyncio.to_thread(
            decode_fn, raw, max_body_bytes=DEFAULT_MAX_BODY_BYTES
        )
    except OversizedPayloadError as exc:
        # Second-line cap guard — catches the rare case where the
        # streaming limit was bypassed (e.g. direct function calls in tests).
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except OtelDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    persisted = await persist_decoded_batch(
        conn, batch, workflow_id=bound_workflow_id
    )

    if bound_workflow_id is not None:
        # Provenance hints first: this writes `workflows.origin` from span
        # attributes if it is currently NULL. The agent registration below
        # reads `workflows.origin` to derive the agent's origin, so the
        # provenance write must come first — otherwise an OTLP-first agent
        # (one that streams traces before going through the import flow)
        # would be permanently stamped 'greenfield' regardless of what the
        # spans say. Idempotent: `_apply_provenance_hints` uses
        # `WHERE origin IS NULL`, so an already-set origin is never overwritten.
        await _apply_provenance_hints(conn, bound_workflow_id, batch)

        # Register the agent on first ingestion — an imported agent that
        # streams traces without ever going through the kernel's creation
        # flow still earns a registry row. Idempotent.
        await register_agent_for_workflow(conn, bound_workflow_id)

        # Nudge the debounced auto-clustering trigger when this batch landed a
        # tool failure. No-op when the trigger is disabled (the default) or the
        # app didn't start one. The actual clustering runs later, off-request,
        # once the workflow has been quiet for the debounce window.
        trigger = getattr(request.app.state, "cluster_auto_trigger", None)
        if trigger is not None and _batch_has_tool_failure(batch):
            trigger.signal(bound_workflow_id)

    _log.info(
        "otel-ingest: accepted %d events, %d warnings, "
        "%d trace(s) created, %d trace(s) appended, %d trace(s) saturated",
        len(batch.events),
        len(batch.warnings),
        len(persisted.created),
        len(persisted.appended),
        len(persisted.saturated),
    )

    return IngestResponse(
        accepted_events=len(batch.events),
        warnings=[IngestWarning(span_id=w.span_id, reason=w.reason) for w in batch.warnings],
        created_trace_ids=persisted.created,
        appended_trace_ids=persisted.appended,
        saturated_trace_ids=persisted.saturated,
    )
