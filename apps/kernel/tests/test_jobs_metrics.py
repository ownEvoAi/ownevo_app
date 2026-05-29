"""Cross-workspace job-count aggregation for the `/metrics` endpoint.

DB-gated. `aggregate_job_counts` binds each workspace's GUC and counts that
workspace's jobs under RLS, then sums. Correctness depends on RLS actually
filtering, so these tests run under a non-superuser role (a superuser bypasses
RLS and every per-workspace count would see every workspace's rows). The pure
`render_metrics` label formatting is unit-tested in test_api_health_metrics.py.
"""

from __future__ import annotations

import contextlib
import json
import os
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.jobs import aggregate_job_counts
from ownevo_kernel.tenant_session import DEFAULT_WORKSPACE_ID, acquire_workspace_conn

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


@contextlib.asynccontextmanager
async def _rls_pool_for_db(db: asyncpg.Connection):
    """Pool whose connections run as an unprivileged, NOBYPASSRLS role — so the
    per-workspace counts are actually filtered by RLS (a superuser would not be).
    Mirrors the helper in test_orphan_reaper.py.
    """
    dbname = await db.fetchval("SELECT current_database()")
    role = f"rls_jobmetrics_{dbname.rsplit('_', 1)[-1]}"
    role_exists = await db.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
    )
    if not role_exists:
        await db.execute(f'CREATE ROLE "{role}" NOSUPERUSER NOBYPASSRLS')
    await db.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    await db.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'
    )
    await db.execute(
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"'
    )

    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))

    async def _setup(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET ROLE "{role}"')

    pool = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, setup=_setup)
        yield pool
    finally:
        if pool is not None:
            with contextlib.suppress(Exception):
                await pool.close()
        await db.execute(f'DROP OWNED BY "{role}"')
        await db.execute(f'DROP ROLE IF EXISTS "{role}"')


async def _insert_job(
    conn: asyncpg.Connection, workflow_id: str, status: str
) -> None:
    await conn.execute(
        """
        INSERT INTO jobs (kind, payload, status)
        VALUES ('run_iteration', $1::jsonb, $2::job_status)
        """,
        json.dumps({"workflow_id": workflow_id}),
        status,
    )


async def test_aggregate_counts_empty(db: asyncpg.Connection) -> None:
    async with _rls_pool_for_db(db) as pool:
        counts = await aggregate_job_counts(pool)
    # Always returns the reported keys, defaulting to 0.
    assert counts == {"queued": 0, "running": 0, "failed": 0}


async def test_aggregate_counts_sums_across_workspaces(
    db: asyncpg.Connection,
) -> None:
    # Second workspace (workspaces is global / not RLS'd).
    await db.execute(
        "INSERT INTO workspaces (id, name) VALUES ($1, $2)", "ws-b", "Workspace B"
    )
    async with _rls_pool_for_db(db) as pool:
        async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
            await _insert_job(conn, "wf-1", "queued")
            await _insert_job(conn, "wf-2", "queued")
            await _insert_job(conn, "wf-3", "failed")
        async with acquire_workspace_conn(pool, "ws-b") as conn:
            await _insert_job(conn, "wf-b1", "running")
            await _insert_job(conn, "wf-b2", "failed")
        # 'succeeded' is intentionally not reported as a gauge.
        async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
            await _insert_job(conn, "wf-done", "succeeded")

        counts = await aggregate_job_counts(pool)

    assert counts == {"queued": 2, "running": 1, "failed": 2}
