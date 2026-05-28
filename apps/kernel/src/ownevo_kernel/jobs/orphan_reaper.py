"""Close iteration rows stuck in 'running' state across a kernel restart.

An iteration run is in-process work: the API request task drives the
agent solver and the proposer, and only writes the final state column at
the end of phase 3. If the kernel restarts mid-iteration, the request
task is cancelled and the row stays in 'running' forever. The workflow's
one-iteration-at-a-time guard then rejects every subsequent run on that
workflow with HTTP 409 until an operator manually edits the row.

This module's `reap_orphaned_iterations` is called once during the
FastAPI lifespan startup, after the pool is open but before the app
begins accepting requests. Any iteration still in 'running' state must
by definition be orphaned (no in-flight task can complete it after a
restart), so we close each as 'sandbox-error' and record an audit
entry. The next iteration request on the workflow then proceeds.

Resuming the partial run is not feasible: the LLM completions and the
mid-cycle outcomes were never persisted, so there is nothing to pick up
from. Failing the row matches what the request would have done on any
exception path inside the runner today.

Multi-workspace concern: the iterations table is under FORCE ROW LEVEL
SECURITY (migration 0034), so a single unscoped sweep would see zero
rows. We iterate the global, non-RLS `workspaces` index and run the
sweep once per workspace under its bound GUC via
`acquire_workspace_conn`. Workspaces with no orphaned rows are a no-op.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..audit.writer import append_audit_entry
from ..tenant_session import acquire_workspace_conn
from ..types import AuditKind, IterationState

if TYPE_CHECKING:
    import asyncpg

_log = logging.getLogger(__name__)

REAPER_ACTOR = "kernel-orphan-reaper"
REAPER_REASON = "orphaned-by-restart"


async def reap_orphaned_iterations(pool: asyncpg.Pool) -> int:
    """Close all iteration rows stuck in 'running' state.

    Returns the total number of rows closed across all workspaces.
    Errors in a single workspace are logged and do not block reaping of
    the remaining workspaces — a poisoned tenant must not prevent the
    kernel from booting.
    """
    workspace_ids = await _list_active_workspaces(pool)
    total = 0
    for workspace_id in workspace_ids:
        try:
            reaped = await _reap_in_workspace(pool, workspace_id)
        except Exception:  # noqa: BLE001 — a failing workspace must not block boot
            _log.exception(
                "orphan reaper: workspace %s failed; continuing",
                workspace_id,
            )
            continue
        total += reaped
    if total:
        _log.warning(
            "orphan reaper: closed %d orphaned iteration row(s) at startup",
            total,
        )
    return total


async def _list_active_workspaces(pool: asyncpg.Pool) -> list[str]:
    """Return non-deleted workspace ids.

    `workspaces` is the global tenancy index and not under RLS, so a plain
    pool connection without a GUC bind returns every row.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM workspaces WHERE deleted_at IS NULL"
        )
    return [row["id"] for row in rows]


async def _reap_in_workspace(pool: asyncpg.Pool, workspace_id: str) -> int:
    """Close orphaned iterations in one workspace and audit each row.

    Runs the UPDATE and the per-row audit inserts in one transaction so a
    crash mid-sweep leaves the rows visible to the next boot's reaper
    instead of half-closed.
    """
    async with (
        acquire_workspace_conn(pool, workspace_id) as conn,
        conn.transaction(),
    ):
        stuck = await conn.fetch(
            """
            UPDATE iterations
            SET state = $1::iteration_state,
                ended_at = now()
            WHERE state = $2::iteration_state
            RETURNING id, workflow_id, iteration_index, started_at
            """,
            IterationState.SANDBOX_ERROR.value,
            IterationState.RUNNING.value,
        )
        for row in stuck:
            await append_audit_entry(
                conn,
                kind=AuditKind.ITERATION_REAPED,
                actor=REAPER_ACTOR,
                related_id=row["id"],
                payload={
                    "workflow_id": row["workflow_id"],
                    "iteration_index": row["iteration_index"],
                    "started_at": row["started_at"].isoformat(),
                    "previous_state": IterationState.RUNNING.value,
                    "new_state": IterationState.SANDBOX_ERROR.value,
                    "reason": REAPER_REASON,
                },
            )
        return len(stuck)


__all__ = ["reap_orphaned_iterations", "REAPER_ACTOR", "REAPER_REASON"]
