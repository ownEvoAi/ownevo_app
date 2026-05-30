"""`action_run_iteration` now enqueues a durable job instead of spawning a
fire-and-forget task, so trigger-fired iterations survive a kernel restart.

DB-gated: the action's contract is the row it writes to the `jobs` table.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.tenant_session import (
    DEFAULT_WORKSPACE_ID,
    acquire_workspace_conn,
)
from ownevo_kernel.triggers.actions import action_enqueue_clustering, action_run_iteration

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


async def _pool_for_db(db: asyncpg.Connection) -> asyncpg.Pool:
    dbname = await db.fetchval("SELECT current_database()")
    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2)


async def _jobs_for(pool: asyncpg.Pool, workflow_id: str) -> list[asyncpg.Record]:
    async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
        return await conn.fetch(
            "SELECT * FROM jobs WHERE payload->>'workflow_id' = $1", workflow_id
        )


async def test_run_iteration_enqueues_a_job(db: asyncpg.Connection) -> None:
    pool = await _pool_for_db(db)
    try:
        await action_run_iteration(pool, "wf-trigger", DEFAULT_WORKSPACE_ID)
        rows = await _jobs_for(pool, "wf-trigger")
        assert len(rows) == 1
        assert rows[0]["kind"] == "run_iteration"
        assert rows[0]["status"] == "queued"
    finally:
        await pool.close()


async def test_run_iteration_dedupes_concurrent_triggers(
    db: asyncpg.Connection,
) -> None:
    """A second trigger for a workflow with an active job adds no duplicate —
    the same one-iteration-at-a-time intent the manual run button enforces."""
    pool = await _pool_for_db(db)
    try:
        await action_run_iteration(pool, "wf-trigger-dup", DEFAULT_WORKSPACE_ID)
        await action_run_iteration(pool, "wf-trigger-dup", DEFAULT_WORKSPACE_ID)
        rows = await _jobs_for(pool, "wf-trigger-dup")
        assert len(rows) == 1
    finally:
        await pool.close()


async def test_enqueue_clustering_inserts_a_job(db: asyncpg.Connection) -> None:
    pool = await _pool_for_db(db)
    try:
        job_id = await action_enqueue_clustering(pool, "wf-cluster", DEFAULT_WORKSPACE_ID)
        assert job_id is not None
        rows = await _jobs_for(pool, "wf-cluster")
        assert len(rows) == 1
        assert rows[0]["kind"] == "run_clustering"
        assert rows[0]["status"] == "queued"
    finally:
        await pool.close()


async def test_enqueue_clustering_dedupes_concurrent_triggers(
    db: asyncpg.Connection,
) -> None:
    """A second enqueue for a workflow with an active clustering job is a no-op."""
    pool = await _pool_for_db(db)
    try:
        first_id = await action_enqueue_clustering(pool, "wf-cluster-dup", DEFAULT_WORKSPACE_ID)
        second_id = await action_enqueue_clustering(pool, "wf-cluster-dup", DEFAULT_WORKSPACE_ID)
        assert first_id is not None
        assert second_id is None
        rows = await _jobs_for(pool, "wf-cluster-dup")
        assert len(rows) == 1
    finally:
        await pool.close()
