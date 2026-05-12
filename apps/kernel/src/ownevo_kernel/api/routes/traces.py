"""`/api/traces` + `/api/workflows/{id}/traces` — trace inspection.

W7 slice 8 (PLAN row 7.1.9). Drives the per-trace step inspection
page; closes the LangSmith / LangFuse parallel for the workspace UI.

Trace events are stored as a JSONB array in `traces.events` (one row
per trace, full event stream inline). For MVP volume this is fine;
Phase-2 ClickHouse migration is the path if customer trace volume
pushes it. The list endpoint computes `event_count` + `kind_counts`
from the JSONB array via the `jsonb_array_elements` lateral; both
endpoints are read-only.

Pagination is intentionally absent — the demo workspace has at most a
few hundred traces per workflow. TODO-18 covers keyset pagination if
real customers push the count.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, status

from ..deps import ConnDep
from ..jsonb import decode_jsonb_array, decode_jsonb_obj
from ..models import TraceDetail, TraceList, TraceSummary

workflow_traces_router = APIRouter(
    prefix="/api/workflows", tags=["traces"],
)
trace_router = APIRouter(prefix="/api/traces", tags=["traces"])


@trace_router.get("", response_model=TraceList)
async def list_all_traces(conn: ConnDep) -> TraceList:
    """Workspace-scoped trace list.

    Same shape as `/api/workflows/{id}/traces` but unscoped — every
    trace across every workflow, newest first. Drives the workspace
    Traces tab (mock parity: s26-rk7p3/15-traces.html). Returns
    workflow_id=None at the top level because the list spans multiple
    workflows; the per-row workflow_id is still populated.
    """
    rows = await conn.fetch(
        """
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
        ORDER BY t.started_at DESC, t.id DESC
        LIMIT 500
        """,
    )
    items = [_row_to_summary(r) for r in rows]
    # Workspace-scoped list has no single workflow_id at the top level —
    # the response model requires one, so we return an empty string. The
    # web UI doesn't rely on the top-level workflow_id when rendering the
    # workspace traces list.
    return TraceList(workflow_id="", items=items)


@workflow_traces_router.get(
    "/{workflow_id}/traces", response_model=TraceList,
)
async def list_workflow_traces(workflow_id: str, conn: ConnDep) -> TraceList:
    """Return every trace for a workflow, newest first.

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

    rows = await conn.fetch(
        """
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
        WHERE t.workflow_id = $1
        ORDER BY t.started_at DESC, t.id DESC
        """,
        workflow_id,
    )
    items = [_row_to_summary(r) for r in rows]
    return TraceList(workflow_id=workflow_id, items=items)


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


