"""Deployment-wide job-queue depth counts for the `/metrics` endpoint.

`jobs` is FORCE ROW LEVEL SECURITY, so an unscoped connection sees no rows —
but a Prometheus scrape wants a count across the whole deployment, not one
workspace. Like the orphan reaper and the worker, we enumerate the global
(non-RLS) `workspaces` index and bind each workspace's GUC via
`acquire_workspace_conn` before counting, then sum across workspaces.

Cost is one grouped COUNT per active workspace per scrape — acceptable at the
current workspace count. If that grows, the optimization path is a
`SECURITY DEFINER` aggregate that reads `jobs` once across all tenants.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from ..tenant_session import WorkspaceBindError, acquire_workspace_conn
from .queue import count_jobs_by_status

if TYPE_CHECKING:
    import asyncpg

# Statuses surfaced as gauges: `queued` is backlog, `running` is in-flight, and
# `failed` is the retries-exhausted alert signal. `succeeded` is omitted — it
# grows monotonically and carries no operational signal.
REPORTED_STATUSES = ("queued", "running", "failed")


async def aggregate_job_counts(pool: asyncpg.Pool) -> dict[str, int]:
    """Sum job rows by status across every active workspace.

    Returns a dict keyed by `REPORTED_STATUSES` (each defaulting to 0), so the
    caller always has the full set of gauges to emit even when the table is
    empty.
    """
    counts = {status: 0 for status in REPORTED_STATUSES}

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM workspaces WHERE deleted_at IS NULL"
        )
    workspace_ids = [row["id"] for row in rows]

    for workspace_id in workspace_ids:
        # A workspace may be soft-deleted between the enumeration query above
        # and this bind.  Skip it so one deletion doesn't silence all gauges.
        with contextlib.suppress(WorkspaceBindError):
            async with acquire_workspace_conn(pool, workspace_id) as conn:
                per_ws = await count_jobs_by_status(conn)
            for status, n in per_ws.items():
                if status in counts:
                    counts[status] += n
    return counts


__all__ = ["aggregate_job_counts", "REPORTED_STATUSES"]
