"""`/api/otel/v1/traces` — OTLP-JSON ingest entry point.

Accepts a single OTLP `ResourceSpans` batch as JSON (the encoding
LangSmith's `langsmith-collector-proxy` and OpenLLMetry exporters
emit by default when targeting a non-OTLP/gRPC sink), decodes it into
typed `AgentEvent`s via `middleware.otel_receiver.decode_otlp_payload`,
and writes one `traces` row per unique `trace_id` in the batch (via
`middleware.otel_receiver.persist_decoded_batch`). After this call
returns, the ingested traces are first-class citizens for the failure
clustering pipeline, the inspection UI, and the rest of the kernel.

Out of scope for this slice:

  * gRPC + protobuf OTLP — JSON-over-HTTP only.
  * Authentication — the receiver is single-tenant and assumes a
    trusted network path. Bearer-token auth lands with the
    multi-tenant retrofit.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from ...middleware.google_adk import translate_otlp_json_for_adk
from ...middleware.otel_receiver import (
    DEFAULT_MAX_BODY_BYTES,
    DecodedBatch,
    OtelDecodeError,
    OversizedPayloadError,
    decode_otlp_payload,
    persist_decoded_batch,
)
from ...middleware.watsonx_adk import translate_otlp_json_for_watsonx
from ..deps import ConnDep

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/otel", tags=["otel-ingest"])


def _parse_translate_decode(raw: bytes, max_body_bytes: int) -> DecodedBatch:
    """Parse raw OTLP-JSON bytes, apply vendor-key translators, then decode.

    Two translators run in sequence:
      * `translate_otlp_json_for_adk` rewrites Google ADK's
        `gcp.vertex.agent.*` keys onto the standard `gen_ai.*` semconv.
      * `translate_otlp_json_for_watsonx` rewrites Traceloop / OpenLLMetry
        `traceloop.*` keys (emitted by watsonx Orchestrate ADK and any
        OpenLLMetry-instrumented agent) onto the same `gen_ai.*` semconv.

    Both translators are conditional (no-op when standard keys are already
    present) and non-destructive (operate on a deep copy). Order does not
    matter because each targets a disjoint vendor namespace. Non-vendor
    payloads pay only the deep-copy + attribute-walk cost.

    JSON parse errors are promoted to `OtelDecodeError` so the route handler's
    existing 400 path handles them without a separate except clause.
    """
    try:
        parsed: dict[str, Any] = json.loads(raw)
    except ValueError as exc:
        raise OtelDecodeError(f"invalid JSON: {exc}") from exc
    translated = translate_otlp_json_for_watsonx(translate_otlp_json_for_adk(parsed))
    return decode_otlp_payload(
        translated,
        max_body_bytes=max_body_bytes,
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


@router.post(
    "/v1/traces",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
)
async def ingest_otlp_traces(
    request: Request, conn: ConnDep,
) -> IngestResponse:
    """Accept one OTLP-JSON ResourceSpans batch and persist it.

    Decoded events land in the `traces` table — one row per unique
    `AgentEvent.trace_id`. Subsequent batches for the same trace_id
    append onto the existing row's JSONB events array (the common
    pattern when an external collector flushes spans in waves).

    The body is streamed with a byte cap to prevent unbounded memory
    accumulation from chunked-transfer payloads. The CPU-bound JSON
    decode is offloaded to a thread-pool executor so the event loop
    stays free during large-batch processing.
    """
    try:
        raw = await _read_body_with_limit(request, DEFAULT_MAX_BODY_BYTES)
    except OversizedPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc

    try:
        batch = await asyncio.to_thread(
            _parse_translate_decode, raw, DEFAULT_MAX_BODY_BYTES
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

    persisted = await persist_decoded_batch(conn, batch)

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
