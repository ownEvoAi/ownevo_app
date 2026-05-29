"""Durable job-queue DB helpers (`jobs` table, migration 0040).

DB-gated: every test needs a real Postgres because the queue's correctness is
in the SQL — `FOR UPDATE SKIP LOCKED` claiming, the active-job unique index,
the retry/exhaust decision, the stale-heartbeat re-queue, and RLS isolation.
Skipped in the unit-only CI job (no `OWNEVO_DATABASE_URL`).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.jobs import (
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    heartbeat_job,
    requeue_stale_jobs,
)
from ownevo_kernel.tenant_session import DEFAULT_WORKSPACE_ID, set_workspace

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


async def _dsn_for(db: asyncpg.Connection) -> str:
    dbname = await db.fetchval("SELECT current_database()")
    parsed = urlparse(os.environ[ENV_VAR])
    return urlunparse(parsed._replace(path=f"/{dbname}"))


async def _enqueue(db: asyncpg.Connection, workflow_id: str, **kw):
    return await enqueue_job(
        db, kind="run_iteration", payload={"workflow_id": workflow_id}, **kw
    )


# --------------------------------------------------------------------------
# enqueue + dedup
# --------------------------------------------------------------------------

async def test_enqueue_inserts_queued_job(db: asyncpg.Connection) -> None:
    job_id = await _enqueue(db, "wf-a")
    assert job_id is not None
    row = await db.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    assert row["status"] == "queued"
    assert row["kind"] == "run_iteration"
    assert row["attempts"] == 0
    assert row["workspace_id"] == DEFAULT_WORKSPACE_ID


async def test_enqueue_is_idempotent_per_workflow(db: asyncpg.Connection) -> None:
    """A second active job for the same workflow is a no-op (returns None)."""
    first = await _enqueue(db, "wf-dup")
    second = await _enqueue(db, "wf-dup")
    assert first is not None
    assert second is None
    count = await db.fetchval(
        "SELECT count(*) FROM jobs WHERE payload->>'workflow_id' = 'wf-dup'"
    )
    assert count == 1


async def test_enqueue_allows_new_job_after_terminal(db: asyncpg.Connection) -> None:
    """Once a workflow's job is terminal, a fresh one can be enqueued — the
    unique index only covers queued/running rows."""
    first = await _enqueue(db, "wf-again")
    await complete_job(db, first, result={"ok": True})
    second = await _enqueue(db, "wf-again")
    assert second is not None and second != first


# --------------------------------------------------------------------------
# claim
# --------------------------------------------------------------------------

async def test_claim_marks_running_and_increments_attempts(
    db: asyncpg.Connection,
) -> None:
    await _enqueue(db, "wf-claim")
    job = await claim_next_job(db, claimed_by="worker-1")
    assert job is not None
    assert job["status"] == "running"
    assert job["attempts"] == 1
    assert job["claimed_by"] == "worker-1"
    assert job["heartbeat_at"] is not None
    # Nothing left to claim.
    assert await claim_next_job(db, claimed_by="worker-1") is None


async def test_claim_returns_none_when_empty(db: asyncpg.Connection) -> None:
    assert await claim_next_job(db, claimed_by="worker-1") is None


async def test_claim_respects_available_at(db: asyncpg.Connection) -> None:
    """A job scheduled into the future (backoff) is not claimable yet."""
    job_id = await _enqueue(db, "wf-future")
    await db.execute(
        "UPDATE jobs SET available_at = now() + interval '1 hour' WHERE id = $1",
        job_id,
    )
    assert await claim_next_job(db, claimed_by="worker-1") is None


async def test_claim_skips_locked_row(db: asyncpg.Connection) -> None:
    """A row another transaction holds is skipped, not blocked on — the
    SKIP LOCKED guarantee that lets concurrent workers claim distinct jobs."""
    await _enqueue(db, "wf-lock")
    other = await asyncpg.connect(await _dsn_for(db))
    try:
        await set_workspace(other, DEFAULT_WORKSPACE_ID)
        tx = other.transaction()
        await tx.start()
        # Hold a row lock on the only queued job.
        held = await other.fetch(
            "SELECT id FROM jobs WHERE status = 'queued' FOR UPDATE SKIP LOCKED"
        )
        assert len(held) == 1
        # The claim must skip the locked row rather than block — nothing to take.
        assert await claim_next_job(db, claimed_by="worker-1") is None
        await tx.rollback()
        # Released — now claimable.
        assert await claim_next_job(db, claimed_by="worker-1") is not None
    finally:
        await other.close()


# --------------------------------------------------------------------------
# heartbeat / complete
# --------------------------------------------------------------------------

async def test_heartbeat_advances_timestamp(db: asyncpg.Connection) -> None:
    await _enqueue(db, "wf-hb")
    job = await claim_next_job(db, claimed_by="worker-1")
    await db.execute(
        "UPDATE jobs SET heartbeat_at = now() - interval '5 minutes' WHERE id = $1",
        job["id"],
    )
    await heartbeat_job(db, job["id"])
    age = await db.fetchval(
        "SELECT now() - heartbeat_at FROM jobs WHERE id = $1", job["id"]
    )
    assert age.total_seconds() < 5  # freshly pinged


async def test_complete_marks_succeeded_with_result(db: asyncpg.Connection) -> None:
    await _enqueue(db, "wf-done")
    job = await claim_next_job(db, claimed_by="worker-1")
    await complete_job(db, job["id"], result={"iteration_id": "abc"})
    row = await db.fetchrow("SELECT status, result FROM jobs WHERE id = $1", job["id"])
    assert row["status"] == "succeeded"
    assert '"iteration_id": "abc"' in row["result"]


# --------------------------------------------------------------------------
# fail: retry vs exhaust
# --------------------------------------------------------------------------

async def test_fail_requeues_with_backoff_when_attempts_remain(
    db: asyncpg.Connection,
) -> None:
    await _enqueue(db, "wf-retry", max_attempts=3)
    job = await claim_next_job(db, claimed_by="worker-1")  # attempts -> 1
    retried = await fail_job(
        db, job["id"], error="boom", backoff_seconds=60.0
    )
    assert retried is True
    row = await db.fetchrow(
        "SELECT status, claimed_by, last_error, available_at > now() AS deferred "
        "FROM jobs WHERE id = $1",
        job["id"],
    )
    assert row["status"] == "queued"
    assert row["claimed_by"] is None
    assert row["last_error"] == "boom"
    assert row["deferred"] is True


async def test_fail_marks_failed_when_attempts_exhausted(
    db: asyncpg.Connection,
) -> None:
    await _enqueue(db, "wf-dead", max_attempts=1)
    job = await claim_next_job(db, claimed_by="worker-1")  # attempts -> 1 == max
    retried = await fail_job(db, job["id"], error="fatal")
    assert retried is False
    row = await db.fetchrow(
        "SELECT status, last_error FROM jobs WHERE id = $1", job["id"]
    )
    assert row["status"] == "failed"
    assert row["last_error"] == "fatal"


# --------------------------------------------------------------------------
# stale re-queue
# --------------------------------------------------------------------------

async def test_requeue_stale_revives_dead_worker_job(db: asyncpg.Connection) -> None:
    await _enqueue(db, "wf-stale")
    job = await claim_next_job(db, claimed_by="dead-worker")
    # Simulate a worker that died: its heartbeat stopped advancing.
    await db.execute(
        "UPDATE jobs SET heartbeat_at = now() - interval '10 minutes' WHERE id = $1",
        job["id"],
    )
    n = await requeue_stale_jobs(db, stale_after_seconds=90.0)
    assert n == 1
    row = await db.fetchrow(
        "SELECT status, claimed_by, attempts FROM jobs WHERE id = $1", job["id"]
    )
    assert row["status"] == "queued"
    assert row["claimed_by"] is None
    # attempts is NOT reset — a poison job still exhausts its retries.
    assert row["attempts"] == 1


async def test_requeue_stale_leaves_live_jobs_alone(db: asyncpg.Connection) -> None:
    await _enqueue(db, "wf-live")
    job = await claim_next_job(db, claimed_by="worker-1")  # fresh heartbeat
    n = await requeue_stale_jobs(db, stale_after_seconds=90.0)
    assert n == 0
    status = await db.fetchval("SELECT status FROM jobs WHERE id = $1", job["id"])
    assert status == "running"


# --------------------------------------------------------------------------
# RLS isolation
# --------------------------------------------------------------------------

async def test_jobs_are_workspace_isolated(rls_db: asyncpg.Connection) -> None:
    """A job in one workspace is invisible from another. Uses the non-superuser
    `rls_db` connection so FORCE ROW LEVEL SECURITY is actually exercised (a
    superuser bypasses RLS)."""
    conn = rls_db  # bound to the default workspace
    job_id = await _enqueue(conn, "wf-rls")
    assert job_id is not None

    await conn.execute(
        "INSERT INTO workspaces (id, name) VALUES ($1, $2)", "ws-other", "Other"
    )
    await set_workspace(conn, "ws-other")
    assert await conn.fetchval("SELECT count(*) FROM jobs") == 0

    await set_workspace(conn, DEFAULT_WORKSPACE_ID)
    assert await conn.fetchval("SELECT count(*) FROM jobs") == 1
