"""`/api/otel/v1/traces` — OTLP-JSON ingest entry point.

Accepts a single OTLP `ResourceSpans` batch as JSON (the encoding
LangSmith's `langsmith-collector-proxy` and OpenLLMetry exporters
emit by default when targeting a non-OTLP/gRPC sink). The body is
decoded into typed AgentEvents via
`middleware.otel_receiver.decode_otlp_payload`; persistence happens
in a follow-on slice (the live LangSmith conformance CI run hooks
into the same response shape).

Out of scope for this slice:

  * gRPC + protobuf OTLP — JSON-over-HTTP only.
  * Persistence into `traces.events` — the response carries the
    decoded events back to the caller; the actual write path is the
    next slice.
  * Authentication — the receiver is single-tenant and assumes a
    trusted network path. Bearer-token auth lands with the
    multi-tenant retrofit.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from ...middleware.otel_receiver import (
    DEFAULT_MAX_BODY_BYTES,
    OtelDecodeError,
    OversizedPayloadError,
    decode_otlp_payload,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/otel", tags=["otel-ingest"])


class IngestWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_id: str | None
    reason: str


class IngestResponse(BaseModel):
    """Response envelope returned by `/api/otel/v1/traces`."""

    model_config = ConfigDict(extra="forbid")

    accepted_events: int
    warnings: list[IngestWarning]


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
async def ingest_otlp_traces(request: Request) -> IngestResponse:
    """Accept one OTLP-JSON ResourceSpans batch.

    Returns the decoded-event count and per-span warnings. The body is
    streamed with a byte cap to prevent unbounded memory accumulation
    from chunked-transfer payloads. The CPU-bound JSON decode is
    offloaded to a thread-pool executor so the event loop stays free
    during large-batch processing.
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
            decode_otlp_payload, raw, max_body_bytes=DEFAULT_MAX_BODY_BYTES
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

    _log.info(
        "otel-ingest: accepted %d events, %d warnings",
        len(batch.events),
        len(batch.warnings),
    )

    return IngestResponse(
        accepted_events=len(batch.events),
        warnings=[IngestWarning(span_id=w.span_id, reason=w.reason) for w in batch.warnings],
    )
