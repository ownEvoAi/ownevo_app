"""HTTP-agnostic receiver entry point.

This module wraps the pure mapper in a thin async function the FastAPI
route can call. Keeping the wrapper here (rather than in the route
module) lets non-HTTP callers — tests, batch importers, the planned
LangSmith dry-run script — invoke the receiver without touching
FastAPI.

The wrapper is intentionally minimal: payload-in, AgentEvents-out.
Persistence (writing into the `traces.events` JSONB array) is the
caller's responsibility — different ingest paths route the events
differently (live ingest → clustering pipeline; replay → in-memory
comparison).
"""

from __future__ import annotations

import logging
from typing import Any

from .mapper import (
    DEFAULT_MAX_BODY_BYTES,
    DecodedBatch,
    decode_otlp_payload,
)

_log = logging.getLogger(__name__)


async def receive_otlp_batch(
    payload: bytes | str | dict[str, Any],
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> DecodedBatch:
    """Decode one OTLP-JSON batch and return the AgentEvents.

    Async by signature so callers compose naturally with the rest of
    the kernel's async surface; the mapper itself is pure-CPU and
    needs no awaits today. If the mapper grows IO (e.g. a tenant
    lookup), this seam absorbs it without changing the call sites.
    """
    batch = decode_otlp_payload(payload, max_body_bytes=max_body_bytes)
    if batch.warnings:
        _log.debug(
            "otel_receiver: decoded %d events with %d warnings",
            len(batch.events),
            len(batch.warnings),
        )
    return batch
