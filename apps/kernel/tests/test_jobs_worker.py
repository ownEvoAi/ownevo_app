"""`JobWorker` — claims, runs, heartbeats, retries, and re-queues jobs.

DB-gated. The worker is driven through one `_tick()` per test (deterministic:
no reliance on the poll cadence) with a stub handler substituted for the real
`run_iteration` so no LLM is involved. The handler-substitution point is the
worker's `_dispatch` table, the same seam a future job kind would extend.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.jobs import JobWorker, enqueue_job
from ownevo_kernel.tenant_session import (
    DEFAULT_WORKSPACE_ID,
    acquire_workspace_conn,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


async def _pool_for_db(db: asyncpg.Connection) -> asyncpg.Pool:
    dbname = await db.fetchval("SELECT current_database()")
    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2)


def _stub_handler(record):
    """Build a dispatch handler that records its calls and returns `result`.

    The worker calls handlers as ``handler(job_record, workspace_id)``.
    """
    async def handler(job, workspace_id):
        payload = job["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        record.append((payload["workflow_id"], workspace_id))
        return {"ran": payload["workflow_id"]}

    return handler


def _raising_handler():
    async def handler(job, workspace_id):
        raise RuntimeError("handler blew up")

    return handler


async def _seed_running_stale(pool: asyncpg.Pool, workflow_id: str) -> str:
    """Insert a job already 'running' with a long-lapsed heartbeat — the state
    a worker that died mid-run leaves behind."""
    async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
        return await conn.fetchval(
            """
            INSERT INTO jobs
                (kind, payload, status, attempts, claimed_by,
                 claimed_at, heartbeat_at)
            VALUES ('run_iteration', $1::jsonb, 'running', 1, 'dead-worker',
                    now() - interval '10 minutes', now() - interval '10 minutes')
            RETURNING id
            """,
            json.dumps({"workflow_id": workflow_id}),
        )


async def _status(pool: asyncpg.Pool, job_id) -> asyncpg.Record:
    async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
        return await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)


async def test_worker_claims_and_runs_queued_job(db: asyncpg.Connection) -> None:
    pool = await _pool_for_db(db)
    try:
        async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
            job_id = await enqueue_job(
                conn, kind="run_iteration", payload={"workflow_id": "wf-run"}
            )
        worker = JobWorker(pool, heartbeat_interval=3600.0)  # no ping mid-test
        calls: list = []
        worker._dispatch["run_iteration"] = _stub_handler(calls)

        await worker._tick()

        assert calls == [("wf-run", DEFAULT_WORKSPACE_ID)]
        row = await _status(pool, job_id)
        assert row["status"] == "succeeded"
        assert '"ran": "wf-run"' in row["result"]
    finally:
        await pool.close()


async def test_worker_failure_requeues_for_retry(db: asyncpg.Connection) -> None:
    pool = await _pool_for_db(db)
    try:
        async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
            job_id = await enqueue_job(
                conn, kind="run_iteration", payload={"workflow_id": "wf-fail"}
            )
        worker = JobWorker(pool, heartbeat_interval=3600.0)
        worker._dispatch["run_iteration"] = _raising_handler()

        await worker._tick()

        row = await _status(pool, job_id)
        # One attempt made, two remain (default max_attempts=3) -> re-queued.
        assert row["status"] == "queued"
        assert row["attempts"] == 1
        assert row["last_error"] is not None and "blew up" in row["last_error"]
    finally:
        await pool.close()


async def test_worker_requeues_stale_then_runs_it(db: asyncpg.Connection) -> None:
    """A single tick re-queues a dead worker's job and then claims+runs it —
    the restart-recovery path end to end."""
    pool = await _pool_for_db(db)
    try:
        job_id = await _seed_running_stale(pool, "wf-stale")
        worker = JobWorker(pool, heartbeat_interval=3600.0, stale_after_seconds=90.0)
        calls: list = []
        worker._dispatch["run_iteration"] = _stub_handler(calls)

        await worker._tick()

        assert calls == [("wf-stale", DEFAULT_WORKSPACE_ID)]
        row = await _status(pool, job_id)
        assert row["status"] == "succeeded"
        # Claimed again after the re-queue, so attempts went 1 -> 2.
        assert row["attempts"] == 2
    finally:
        await pool.close()


async def test_worker_tick_is_noop_when_queue_empty(db: asyncpg.Connection) -> None:
    pool = await _pool_for_db(db)
    try:
        worker = JobWorker(pool)
        calls: list = []
        worker._dispatch["run_iteration"] = _stub_handler(calls)
        await worker._tick()  # no jobs — must not raise
        assert calls == []
    finally:
        await pool.close()


async def test_terminal_failure_emits_structured_log(
    db: asyncpg.Connection, caplog
) -> None:
    """When a job exhausts its retries the worker logs an ERROR carrying
    structured fields (the log-based alert signal), not just a message."""
    pool = await _pool_for_db(db)
    try:
        async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
            await enqueue_job(
                conn,
                kind="run_iteration",
                payload={"workflow_id": "wf-doomed"},
                max_attempts=1,  # first failure is terminal
            )
        worker = JobWorker(pool, heartbeat_interval=3600.0)
        worker._dispatch["run_iteration"] = _raising_handler()

        with caplog.at_level(logging.ERROR, logger="ownevo_kernel.jobs.worker"):
            await worker._tick()

        terminal = [
            r for r in caplog.records if getattr(r, "job_failed_terminal", False)
        ]
        assert len(terminal) == 1
        rec = terminal[0]
        assert rec.workflow_id == "wf-doomed"
        assert rec.kind == "run_iteration"
        assert rec.attempts == 1
        assert rec.last_error is not None and "blew up" in rec.last_error
    finally:
        await pool.close()


async def test_worker_start_stop_is_graceful(db: asyncpg.Connection) -> None:
    pool = await _pool_for_db(db)
    try:
        worker = JobWorker(pool, poll_interval=0.05)
        await worker.start()
        await worker.start()  # idempotent — second start is a no-op
        await worker.stop(timeout=5.0)
        assert worker._task is None
        # stop() again is safe.
        await worker.stop(timeout=5.0)
    finally:
        await pool.close()
