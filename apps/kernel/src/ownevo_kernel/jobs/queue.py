"""Durable job-queue DB helpers (table defined in migration 0040).

These functions are the only writers/readers of the `jobs` table. Each takes
a connection that is already bound to a workspace (via `tenant_session`), so
every statement runs under row-level security against the active workspace —
`jobs` is FORCE ROW LEVEL SECURITY, so an unbound connection sees no rows and
cannot insert.

Lifecycle of a row:

    enqueue_job        -> 'queued'
    claim_next_job     -> 'running'  (attempts += 1, claimed_by/heartbeat set)
    heartbeat_job      -> liveness ping while 'running'
    complete_job       -> 'succeeded'
    fail_job           -> 'queued' (retry, with backoff) or 'failed' (exhausted)
    requeue_stale_jobs -> 'running' with a lapsed heartbeat back to 'queued'

`requeue_stale_jobs` is what makes the queue durable across a restart: a
worker that dies mid-job leaves its row 'running' with a heartbeat that stops
advancing; the next poll re-queues it for another worker to claim.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg

# Default retry ceiling for a newly enqueued job. A job that fails this many
# times (counting from the first claim) becomes terminally 'failed' rather
# than looping forever — a poison job cannot wedge the worker.
DEFAULT_MAX_ATTEMPTS = 3


async def enqueue_job(
    conn: asyncpg.Connection,
    *,
    kind: str,
    payload: dict[str, Any],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> UUID | None:
    """Insert a queued job, idempotent per active (workspace, kind, workflow).

    Returns the new job id, or ``None`` when an active (queued or running)
    job for the same workflow already exists — the partial unique index
    `jobs_active_per_workflow_idx` makes the insert a no-op in that case, so
    a burst of triggers for one workflow enqueues a single job.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO jobs (kind, payload, max_attempts)
        VALUES ($1::job_kind, $2::jsonb, $3)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        kind,
        json.dumps(payload),
        max_attempts,
    )
    return row["id"] if row is not None else None


async def claim_next_job(
    conn: asyncpg.Connection,
    *,
    claimed_by: str,
) -> asyncpg.Record | None:
    """Atomically claim the oldest ready job, or return ``None`` if none.

    `FOR UPDATE SKIP LOCKED` lets concurrent workers (or future kernel
    instances) claim distinct rows without blocking each other. The claim
    increments `attempts` so retries are bounded even if the worker dies
    before reaching `fail_job`.
    """
    return await conn.fetchrow(
        """
        UPDATE jobs
        SET status = 'running',
            claimed_by = $1,
            claimed_at = now(),
            heartbeat_at = now(),
            attempts = attempts + 1,
            updated_at = now()
        WHERE id = (
            SELECT id FROM jobs
            WHERE status = 'queued' AND available_at <= now()
            ORDER BY available_at, created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING *
        """,
        claimed_by,
    )


async def heartbeat_job(conn: asyncpg.Connection, job_id: UUID) -> None:
    """Advance a running job's heartbeat so it is not seen as stale."""
    await conn.execute(
        """
        UPDATE jobs
        SET heartbeat_at = now(), updated_at = now()
        WHERE id = $1 AND status = 'running'
        """,
        job_id,
    )


async def complete_job(
    conn: asyncpg.Connection,
    job_id: UUID,
    *,
    result: dict[str, Any] | None = None,
) -> None:
    """Mark a job succeeded and store its result payload."""
    await conn.execute(
        """
        UPDATE jobs
        SET status = 'succeeded',
            result = $2::jsonb,
            last_error = NULL,
            updated_at = now()
        WHERE id = $1
        """,
        job_id,
        json.dumps(result) if result is not None else None,
    )


async def fail_job(
    conn: asyncpg.Connection,
    job_id: UUID,
    *,
    error: str,
    backoff_seconds: float = 0.0,
) -> bool:
    """Record a failed attempt; retry with backoff or fail terminally.

    Returns ``True`` when the job was re-queued for another attempt, ``False``
    when its `attempts` reached `max_attempts` and it became terminally
    'failed'. The decision is made in SQL against the row's own counters so it
    stays correct regardless of which worker calls it.
    """
    row = await conn.fetchrow(
        """
        UPDATE jobs
        SET status = CASE
                WHEN attempts < max_attempts THEN 'queued'::job_status
                ELSE 'failed'::job_status
            END,
            available_at = CASE
                WHEN attempts < max_attempts
                THEN now() + make_interval(secs => $2)
                ELSE available_at
            END,
            claimed_by = NULL,
            last_error = $3,
            updated_at = now()
        WHERE id = $1
        RETURNING status
        """,
        job_id,
        float(backoff_seconds),
        error,
    )
    return bool(row is not None and row["status"] == "queued")


async def requeue_stale_jobs(
    conn: asyncpg.Connection,
    *,
    stale_after_seconds: float,
) -> int:
    """Re-queue running jobs whose heartbeat lapsed (their worker died).

    Returns the number of rows re-queued. `attempts` is deliberately left
    untouched (it was already incremented at claim time), so a job that keeps
    crashing its worker still exhausts `max_attempts` and ends 'failed'
    instead of being retried forever. The threshold must comfortably exceed
    the worker's heartbeat interval so a live worker's job is never stolen.
    """
    requeued = await conn.fetchval(
        """
        WITH stale AS (
            UPDATE jobs
            SET status = 'queued',
                claimed_by = NULL,
                available_at = now(),
                updated_at = now()
            WHERE status = 'running'
              AND heartbeat_at < now() - make_interval(secs => $1)
            RETURNING 1
        )
        SELECT count(*) FROM stale
        """,
        float(stale_after_seconds),
    )
    return int(requeued or 0)


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "enqueue_job",
    "claim_next_job",
    "heartbeat_job",
    "complete_job",
    "fail_job",
    "requeue_stale_jobs",
]
