"""`GET /api/jobs` — read-only view of the durable job queue for a workspace.

The connection is workspace-bound by `ConnDep` (the request principal's
workspace), and `jobs` is under row-level security, so this returns only the
caller's workspace's jobs with no explicit `workspace_id` filter.

Bounded by `limit` (max 200): a recent-jobs view for operability, not a full
export. The queue is low-volume, so there is no cursor pagination here — unlike
the trace endpoints, a single bounded page is the use case. The response also
carries per-status `counts` over the whole workspace so the UI can show queue
depth (queued backlog, in-flight, failed) without a second request.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ...jobs.queue import count_jobs_by_status
from ..deps import ConnDep
from ..models import JobList, JobSummary

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
# Mirrors the job_status enum (migration 0040). An out-of-set `?status=` value
# is rejected with 422 before it reaches SQL.
_VALID_STATUSES = frozenset({"queued", "running", "succeeded", "failed"})


@router.get("", response_model=JobList)
async def list_jobs(
    conn: ConnDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> JobList:
    """Most-recent jobs for the workspace, newest first, with depth counts."""
    if status is not None and status not in _VALID_STATUSES:
        # 422 to match FastAPI's own query-validation failures (e.g. a bad limit).
        raise HTTPException(status_code=422, detail="Invalid status filter")

    params: list[object] = []
    where = ""
    if status is not None:
        params.append(status)
        where = f"WHERE status = ${len(params)}::job_status"
    params.append(limit)

    # Both queries share one READ ONLY transaction so `counts` and `items`
    # reflect the same snapshot — without this, a job claimed between the two
    # statements can make the failed count contradict the returned rows.
    async with conn.transaction(readonly=True):
        counts = await count_jobs_by_status(conn)
        rows = await conn.fetch(
            f"""
            SELECT
                id,
                kind,
                status,
                attempts,
                max_attempts,
                payload->>'workflow_id' AS workflow_id,
                last_error,
                claimed_by,
                available_at,
                created_at,
                updated_at
            FROM jobs
            {where}
            ORDER BY created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )

    items = [
        JobSummary(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            workflow_id=row["workflow_id"],
            last_error=row["last_error"],
            claimed_by=row["claimed_by"],
            available_at=row["available_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]
    return JobList(items=items, counts=counts)
