"""Persist a `DecodedBatch` into the `traces` table.

The OTLP receiver decodes an external span batch into typed AgentEvents,
but those events are useless to the rest of the kernel — failure
clustering, the gate runner, the trace inspection UI — until they're
written into the same `traces` row that the in-process `TraceCollector`
produces. This module owns that write step.

What gets written
-----------------
Each unique `AgentEvent.trace_id` in the batch becomes one `traces`
row, keyed by that UUID. The events for a trace_id are stored as a
JSONB array in `traces.events` in arrival order; the row's
`started_at` / `ended_at` are derived from the min / max event
timestamps.

Why upsert (not pure insert)
----------------------------
A real OTel collector typically flushes spans for a trace in waves as
they complete — the chat span lands first, the tool span lands a
second later. Two HTTP batches can carry events for the same trace_id.
We `INSERT ... ON CONFLICT (id) DO UPDATE` so the second batch appends
its events to the existing row's JSONB array instead of erroring out
or creating a duplicate. JSONB `||` concatenation preserves the order
within each side; the union is the per-batch arrival order, which is
what the inspection UI walks.

Why `ingest_source = 'otlp'`
----------------------------
Trace provenance matters downstream: kernel-emitted events are
structurally trusted (TraceCollector enforces shape via the typed
constructor), whereas ingested events were translated from an external
OTel span stream and may carry `ownevo.error_class` claims, partial
fields, or vendor extensions whose provenance is unattested. The
column is added in migration `0018_traces_ingest_source.sql`.

Why a per-trace event cap (saturation)
--------------------------------------
The append-on-conflict path means a malicious or buggy external
collector that keeps POSTing batches with the same `trace_id` can
grow one row's `events` JSONB array without bound — eventually OOM
on the JSON parser, query path, or wire. We cap each trace at
`_MAX_EVENTS_PER_TRACE` (10 000) events. Once a trace saturates,
further batches for that trace_id are dropped at the persist layer
and surfaced in the response's `saturated_trace_ids` list so callers
can detect the saturation without a separate query. The cap is a
safety net, not a precision boundary: under concurrent writes the
final count may overshoot slightly, which is fine.

Why a per-batch transaction
---------------------------
A single OTLP batch may carry events for multiple trace_ids. The
upserts run sequentially; without a transaction, a mid-batch failure
(e.g. an unexpected DB error on row 5 of 10) would leave the table
half-written and the HTTP response would still claim partial success.
Wrapping the whole batch in `conn.transaction()` makes persistence
all-or-nothing: either every trace in the batch lands and the
response is truthful, or nothing lands and the route returns 500
with the table unchanged.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING
from uuid import UUID

from .mapper import DecodedBatch

if TYPE_CHECKING:
    import asyncpg
    from ownevo_format import AgentEvent

_INGEST_SOURCE = "otlp"

# Generous cap — a real agent run produces tens to hundreds of events,
# and even a long multi-turn trace rarely exceeds the low thousands.
# 10 000 covers any plausible legitimate trace and bounds the worst-case
# row size to roughly a few MiB of JSONB.
_MAX_EVENTS_PER_TRACE = 10_000


class _UpsertOutcome(Enum):
    CREATED = auto()
    APPENDED = auto()
    SATURATED = auto()


@dataclass(frozen=True)
class PersistResult:
    """Summary of what `persist_decoded_batch` actually wrote.

    `trace_ids` lists every trace_id touched by the batch. `created`
    and `appended` split that list between fresh inserts and existing
    rows extended in place; `saturated` lists trace_ids that were
    dropped because they would push the per-trace event count past
    `_MAX_EVENTS_PER_TRACE`. The trio lets the route log a meaningful
    summary and surface saturation back to the caller without
    re-querying the table.
    """

    trace_ids: list[UUID]
    created: list[UUID]
    appended: list[UUID]
    saturated: list[UUID]


async def persist_decoded_batch(
    conn: asyncpg.Connection,
    batch: DecodedBatch,
    *,
    workflow_id: str | None = None,
) -> PersistResult:
    """Upsert every trace in the batch and return the touched trace_ids.

    All upserts in the batch run inside a single transaction so a
    mid-batch DB error rolls back every prior write; the response and
    the table state stay consistent.

    When `workflow_id` is set (resolved from the receiver token or an
    explicit query param), it is stamped onto freshly created rows and
    backfilled onto existing rows that have none yet — an existing
    binding is never overwritten.

    No-op (returns empty result) when the batch has zero events — the
    receiver may emit an empty batch when every span was unknown or
    skipped, and an empty-INSERT round-trip is wasted work.
    """
    if not batch.events:
        return PersistResult(trace_ids=[], created=[], appended=[], saturated=[])

    groups = _group_by_trace_id(batch.events)
    created: list[UUID] = []
    appended: list[UUID] = []
    saturated: list[UUID] = []
    async with conn.transaction():
        for trace_id, events in groups.items():
            outcome = await _upsert_one_trace(
                conn, trace_id, events, workflow_id=workflow_id
            )
            if outcome is _UpsertOutcome.CREATED:
                created.append(trace_id)
            elif outcome is _UpsertOutcome.APPENDED:
                appended.append(trace_id)
            else:
                saturated.append(trace_id)
    saturated_set = set(saturated)
    touched = [t for t in groups if t not in saturated_set]
    return PersistResult(
        trace_ids=touched,
        created=created,
        appended=appended,
        saturated=saturated,
    )


def _group_by_trace_id(
    events: Sequence[AgentEvent],
) -> dict[UUID, list[AgentEvent]]:
    """Group while preserving relative order inside each group.

    The mapper emits events in span-visit order, which is close enough
    to wall-clock order for the inspection UI; we keep that ordering
    rather than sorting by timestamp (timestamps can collide on
    sub-millisecond spans, and re-sorting would mask exporter bugs).
    """
    grouped: dict[UUID, list[AgentEvent]] = defaultdict(list)
    for ev in events:
        grouped[ev.trace_id].append(ev)
    return grouped


async def _upsert_one_trace(
    conn: asyncpg.Connection,
    trace_id: UUID,
    events: Sequence[AgentEvent],
    *,
    workflow_id: str | None = None,
) -> _UpsertOutcome:
    """INSERT, append, or skip based on the per-trace event cap.

    Returns `CREATED` on a fresh insert, `APPENDED` when an existing
    row's events array was extended, or `SATURATED` when the row would
    grow past `_MAX_EVENTS_PER_TRACE` and was therefore left
    untouched.

    The cap check is a SELECT followed by a conditional INSERT. The
    enclosing transaction holds a row-level lock once the conditional
    UPDATE fires, but two batches that both pass the pre-check
    concurrently can both append — the cap is a safety net against
    runaway growth, not a precise hard limit. Worst-case overshoot
    under contention is small relative to the cap itself.
    """
    existing_count = await conn.fetchval(
        "SELECT COALESCE(jsonb_array_length(events), 0) "
        "FROM traces WHERE id = $1",
        trace_id,
    )
    existing_count = int(existing_count) if existing_count is not None else 0
    if existing_count + len(events) > _MAX_EVENTS_PER_TRACE:
        return _UpsertOutcome.SATURATED

    events_json = json.dumps([e.model_dump(mode="json") for e in events])
    timestamps = [e.timestamp for e in events]
    started_at = min(timestamps)
    ended_at = max(timestamps)

    row = await conn.fetchrow(
        """
        INSERT INTO traces (
            id, events, started_at, ended_at, ingest_source, workflow_id
        )
        VALUES ($1, $2::jsonb, $3, $4, $5, $6)
        ON CONFLICT (id) DO UPDATE SET
            events     = traces.events || EXCLUDED.events,
            started_at = LEAST(traces.started_at, EXCLUDED.started_at),
            ended_at   = GREATEST(
                COALESCE(traces.ended_at, EXCLUDED.ended_at),
                EXCLUDED.ended_at
            ),
            workflow_id = COALESCE(traces.workflow_id, EXCLUDED.workflow_id)
        RETURNING (xmax = 0) AS was_inserted
        """,
        trace_id,
        events_json,
        started_at,
        ended_at,
        _INGEST_SOURCE,
        workflow_id,
    )
    if row is None:
        # INSERT ... RETURNING always returns a row; None here would indicate
        # a driver or schema mismatch, not a normal condition.
        raise RuntimeError("INSERT ... RETURNING returned no row — internal error")
    # `xmax = 0` is the classic Postgres trick to distinguish an INSERT
    # from an UPDATE on a single upsert RETURNING — `xmax = 0` means
    # the row was freshly inserted; a non-zero xmax means a pre-existing
    # row was updated.
    return _UpsertOutcome.CREATED if row["was_inserted"] else _UpsertOutcome.APPENDED
