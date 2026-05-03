"""Trace capture — accumulate AgentEvent stream and persist as one row.

A `TraceCollector` collects events for a single agent run in memory, then
inserts the whole stream into `traces.events` (JSONB array) on finalize.
ClickHouse / per-event row migration is Phase 2; for MVP, one-row-per-run
keeps the read path trivial (the gate runner reads a trace by id and
walks the array).

Typical use:

    async with trace_session(conn, workflow_id="w1") as session:
        session.record(session.make_event(
            type="skill_loaded",
            skill_id="m5-feature-engineer",
            version_seq=1,
        ))
        ...
        session.set_metric_outputs({"acc": 0.84})
    # finalize fires on context exit, even on exception

`make_event(type=..., **fields)` fills in `event_id`, `trace_id`,
`timestamp` and validates against the AgentEvent discriminated union, so
the caller only writes type-specific fields.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from ownevo_format import AgentEventAdapter
from pydantic import BaseModel


class TraceCollector:
    """Stateful in-memory accumulator for one trace.

    Attributes are set/read by the caller during the session; `finalize`
    persists everything to `traces`.
    """

    def __init__(
        self,
        *,
        workflow_id: str | None = None,
        iteration_id: UUID | None = None,
        skill_version_id: UUID | None = None,
        trace_id: UUID | None = None,
    ) -> None:
        self.trace_id: UUID = trace_id or uuid4()
        self.workflow_id = workflow_id
        self.iteration_id = iteration_id
        self.skill_version_id = skill_version_id
        self.started_at: datetime = datetime.now(UTC)
        self.ended_at: datetime | None = None
        self._events: list[BaseModel] = []
        self._metric_outputs: dict[str, Any] | None = None
        self._token_usage: dict[str, Any] | None = None
        self._finalized = False

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def make_event(self, **fields: Any) -> BaseModel:
        """Build a typed AgentEvent with `event_id` / `trace_id` /
        `timestamp` filled in. `type` is required."""
        fields.setdefault("event_id", uuid4())
        fields.setdefault("trace_id", self.trace_id)
        fields.setdefault("timestamp", datetime.now(UTC))
        if self.iteration_id is not None:
            fields.setdefault("iteration_id", self.iteration_id)
        return AgentEventAdapter.validate_python(fields)

    def record(self, event: BaseModel) -> None:
        """Append a pre-built AgentEvent. Must share this collector's
        `trace_id`; mismatches surface as ValueError so a bug routing
        events to the wrong session can't silently corrupt the table."""
        ev_trace = getattr(event, "trace_id", None)
        if ev_trace != self.trace_id:
            raise ValueError(
                f"Event trace_id {ev_trace} does not match session "
                f"trace_id {self.trace_id}",
            )
        self._events.append(event)

    @property
    def events(self) -> list[BaseModel]:
        return list(self._events)

    # ------------------------------------------------------------------
    # Run-level fields
    # ------------------------------------------------------------------

    def set_metric_outputs(self, value: dict[str, Any]) -> None:
        self._metric_outputs = value

    def set_token_usage(self, value: dict[str, Any]) -> None:
        self._token_usage = value

    # ------------------------------------------------------------------
    # Finalize → INSERT
    # ------------------------------------------------------------------

    async def finalize(self, conn: asyncpg.Connection) -> UUID:
        """Persist the trace. Idempotent — second call is a no-op."""
        if self._finalized:
            return self.trace_id
        self.ended_at = datetime.now(UTC)
        events_json = json.dumps(
            [e.model_dump(mode="json") for e in self._events],
        )
        await conn.execute(
            """
            INSERT INTO traces (
                id, workflow_id, iteration_id, skill_version_id,
                events, started_at, ended_at,
                metric_outputs, token_usage
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8::jsonb, $9::jsonb)
            """,
            self.trace_id,
            self.workflow_id,
            self.iteration_id,
            self.skill_version_id,
            events_json,
            self.started_at,
            self.ended_at,
            json.dumps(self._metric_outputs) if self._metric_outputs is not None else None,
            json.dumps(self._token_usage) if self._token_usage is not None else None,
        )
        self._finalized = True
        return self.trace_id


@asynccontextmanager
async def trace_session(
    conn: asyncpg.Connection,
    *,
    workflow_id: str | None = None,
    iteration_id: UUID | None = None,
    skill_version_id: UUID | None = None,
    trace_id: UUID | None = None,
) -> AsyncIterator[TraceCollector]:
    """Context-managed trace session. Finalizes on exit even if the
    body raises — failing iterations still produce a stored trace, which
    is what the failure-clustering pipeline needs."""
    collector = TraceCollector(
        workflow_id=workflow_id,
        iteration_id=iteration_id,
        skill_version_id=skill_version_id,
        trace_id=trace_id,
    )
    try:
        yield collector
    finally:
        await collector.finalize(conn)
