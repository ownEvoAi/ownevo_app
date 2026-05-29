"""`/api/traces` + `/api/workflows/{id}/traces` — trace inspection.

W7 slice 8 (PLAN row 7.1.9). Drives the per-trace step inspection
page; closes the LangSmith / LangFuse parallel for the workspace UI.

Trace events are stored as a JSONB array in `traces.events` (one row
per trace, full event stream inline). For MVP volume this is fine;
Phase-2 ClickHouse migration is the path if customer trace volume
pushes it. The list endpoint computes `event_count` + `kind_counts`
from the JSONB array via the `jsonb_array_elements` lateral; both
endpoints are read-only.

Both list endpoints are keyset-paginated on the `(started_at DESC, id
DESC)` ordering: a `limit` (default and max 500) bounds the page, and an
opaque `cursor` token (the last row's `started_at` + `id`, base64url
encoded) walks to the next/older page. The default preserves the prior
500-row cap, but truncation is now explicit — a full page returns a
`next_cursor` instead of silently dropping the rest. Table partitioning
and a retention policy on the high-volume `traces` JSONB table remain
Phase-2 work (see SCHEMA.md).
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, status

from ..deps import ConnDep
from ..jsonb import decode_jsonb_array, decode_jsonb_obj
from ..models import TraceDetail, TraceList, TraceSummary

workflow_traces_router = APIRouter(
    prefix="/api/workflows", tags=["traces"],
)
trace_router = APIRouter(prefix="/api/traces", tags=["traces"])

# Default == max: a page returns up to 500 rows (the prior hard cap), and a
# caller wanting smaller pages passes a smaller `limit` and walks `cursor`.
_DEFAULT_TRACE_LIMIT = 500
_MAX_TRACE_LIMIT = 500

# Shared SELECT body for the two list endpoints — only the WHERE clause
# (workflow filter + keyset predicate) and the LIMIT differ between them.
_TRACE_LIST_SELECT = """
    SELECT
        t.id,
        t.workflow_id,
        t.iteration_id,
        i.iteration_index,
        t.skill_version_id,
        t.started_at,
        t.ended_at,
        COALESCE(jsonb_array_length(t.events), 0)       AS event_count,
        COALESCE(
            (
                SELECT jsonb_object_agg(kind, cnt)
                FROM (
                    SELECT
                        evt->>'type' AS kind,
                        COUNT(*)::int AS cnt
                    FROM jsonb_array_elements(t.events) evt
                    GROUP BY evt->>'type'
                ) k
            ),
            '{}'::jsonb
        )                                                AS kind_counts
    FROM traces t
    LEFT JOIN iterations i ON i.id = t.iteration_id
"""


def _encode_cursor(started_at: datetime, trace_id: UUID) -> str:
    """Opaque keyset cursor: the last row's sort key, base64url-encoded."""
    raw = f"{started_at.isoformat()}|{trace_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(token: str) -> tuple[datetime, UUID]:
    """Inverse of `_encode_cursor`. Raises 400 on a malformed token rather
    than leaking the decode error or silently returning the first page."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        ts_str, id_str = raw.rsplit("|", 1)
        return datetime.fromisoformat(ts_str), UUID(id_str)
    except ValueError as exc:  # bad base64, bad split, bad datetime, bad UUID
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination cursor",
        ) from exc


async def _fetch_trace_page(
    conn: asyncpg.Connection,
    *,
    workflow_id: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[TraceSummary], str | None]:
    """Fetch one keyset page newest-first, plus the cursor for the next page.

    Fetches `limit + 1` rows so a full page can be distinguished from the
    last page without a separate COUNT: the extra row (if present) means
    there is more, and the cursor is taken from the last *returned* row.
    """
    where: list[str] = []
    params: list[object] = []
    if workflow_id is not None:
        params.append(workflow_id)
        where.append(f"t.workflow_id = ${len(params)}")
    if cursor is not None:
        started_at, last_id = _decode_cursor(cursor)
        params.append(started_at)
        ts_placeholder = len(params)
        params.append(last_id)
        id_placeholder = len(params)
        # Row-value comparison against the DESC sort key: strictly-older rows.
        # The composite key makes this exact even when started_at ties.
        where.append(
            f"(t.started_at, t.id) < "
            f"(${ts_placeholder}::timestamptz, ${id_placeholder}::uuid)"
        )
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit + 1)
    limit_placeholder = len(params)

    query = (
        f"{_TRACE_LIST_SELECT} {where_sql} "
        f"ORDER BY t.started_at DESC, t.id DESC LIMIT ${limit_placeholder}"
    )
    rows = await conn.fetch(query, *params)

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_row_to_summary(r) for r in rows]
    next_cursor = (
        _encode_cursor(rows[-1]["started_at"], rows[-1]["id"])
        if has_more and rows
        else None
    )
    return items, next_cursor


@trace_router.get("", response_model=TraceList)
async def list_all_traces(
    conn: ConnDep,
    limit: int = Query(default=_DEFAULT_TRACE_LIMIT, ge=1, le=_MAX_TRACE_LIMIT),
    cursor: str | None = Query(default=None),
) -> TraceList:
    """Workspace-scoped trace list, newest first.

    Same shape as `/api/workflows/{id}/traces` but unscoped — every
    trace across every workflow. Drives the workspace Traces tab (mock
    parity: s26-rk7p3/15-traces.html). Returns workflow_id="" at the top
    level because the list spans multiple workflows; the per-row
    workflow_id is still populated.
    """
    items, next_cursor = await _fetch_trace_page(
        conn, workflow_id=None, cursor=cursor, limit=limit
    )
    # Workspace-scoped list has no single workflow_id at the top level and
    # the response model requires one, so we return an empty string. The
    # web UI doesn't rely on the top-level workflow_id when rendering the
    # workspace traces list.
    return TraceList(workflow_id="", items=items, next_cursor=next_cursor)


@workflow_traces_router.get(
    "/{workflow_id}/traces", response_model=TraceList,
)
async def list_workflow_traces(
    workflow_id: str,
    conn: ConnDep,
    limit: int = Query(default=_DEFAULT_TRACE_LIMIT, ge=1, le=_MAX_TRACE_LIMIT),
    cursor: str | None = Query(default=None),
) -> TraceList:
    """Return a workflow's traces, newest first.

    `event_count` + `kind_counts` are derived from `traces.events` so
    the list view can render quick triage signals (tool-heavy vs
    reasoning-heavy, monitor signals firing) without a per-row fetch
    of the full event stream.
    """
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1", workflow_id,
    )
    if not workflow_exists:
        # Static message — never reflect the user-supplied path param.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    items, next_cursor = await _fetch_trace_page(
        conn, workflow_id=workflow_id, cursor=cursor, limit=limit
    )
    return TraceList(
        workflow_id=workflow_id, items=items, next_cursor=next_cursor
    )


@trace_router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: UUID, conn: ConnDep) -> TraceDetail:
    """Full trace including the inline AgentEvent stream."""
    row = await conn.fetchrow(
        """
        SELECT
            t.id,
            t.workflow_id,
            t.iteration_id,
            i.iteration_index,
            t.skill_version_id,
            sv.skill_id,
            sv.version_seq                                   AS skill_version_seq,
            t.started_at,
            t.ended_at,
            t.metric_outputs,
            t.token_usage,
            t.events
        FROM traces t
        LEFT JOIN iterations i        ON i.id = t.iteration_id
        LEFT JOIN skill_versions sv   ON sv.id = t.skill_version_id
        WHERE t.id = $1
        """,
        trace_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trace not found",
        )

    events_raw: Any = decode_jsonb_array(row["events"])
    if not isinstance(events_raw, list):
        # Schema violation upstream — every trace must have an array,
        # even an empty one. Surface as 500 so the corrupted row is
        # visible in logs rather than silently rendering blank.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Trace events column is not a JSON array",
        )

    return TraceDetail(
        id=row["id"],
        workflow_id=row["workflow_id"],
        iteration_id=row["iteration_id"],
        iteration_index=row["iteration_index"],
        skill_version_id=row["skill_version_id"],
        skill_id=row["skill_id"],
        skill_version_seq=row["skill_version_seq"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        metric_outputs=decode_jsonb_obj(row["metric_outputs"]),
        token_usage=decode_jsonb_obj(row["token_usage"]),
        events=events_raw,
    )


def _row_to_summary(row: asyncpg.Record) -> TraceSummary:
    kc = decode_jsonb_obj(row["kind_counts"]) or {}
    return TraceSummary(
        id=row["id"],
        workflow_id=row["workflow_id"],
        iteration_id=row["iteration_id"],
        iteration_index=row["iteration_index"],
        skill_version_id=row["skill_version_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        event_count=int(row["event_count"]),
        kind_counts={k: int(v) for k, v in kc.items()},
    )


