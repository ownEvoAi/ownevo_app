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

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from ...middleware.otel_receiver import (
    OtelDecodeError,
    OversizedPayloadError,
    decode_otlp_payload,
)
from ...middleware.otel_receiver.mapper import DEFAULT_MAX_BODY_BYTES

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


@router.post(
    "/v1/traces",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
)
async def ingest_otlp_traces(request: Request) -> IngestResponse:
    """Accept one OTLP-JSON ResourceSpans batch.

    Returns the decoded-event count and per-span warnings. The body is
    consumed as raw bytes so the size cap can be enforced before JSON
    parsing.
    """
    raw = await request.body()
    try:
        batch = decode_otlp_payload(raw, max_body_bytes=DEFAULT_MAX_BODY_BYTES)
    except OversizedPayloadError as exc:
        # 413 — the OTLP-HTTP spec mandates this for body-size rejects.
        raise HTTPException(status_code=413, detail=str(exc)) from exc
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
