"""Integration smoke tests for db.py.

These require a live Postgres + pgvector instance. By default they create
a throwaway database against the URL in `OWNEVO_DATABASE_URL`, run the
migrations, exercise the WORM contract, and drop the database.

Skipped when `OWNEVO_DATABASE_URL` is unset — keeps unit-only CI green.
Run locally with:

    cd infra && docker compose up -d
    OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:5432/ownevo \
        uv run pytest apps/kernel/tests/test_db.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR, migrate, migration_files, open_pool

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests (see infra/README.md)",
)


def _admin_url() -> str:
    """URL pointed at the `postgres` admin DB so we can CREATE/DROP test DBs."""
    base = os.environ[ENV_VAR]
    # asyncpg parses `postgresql://...` URLs natively; rewrite the database name.
    if "/" not in base.rsplit("@", 1)[-1]:
        return base + "/postgres"
    return base.rsplit("/", 1)[0] + "/postgres"


@pytest.fixture
async def fresh_db():
    """Create a uniquely-named database, yield its DSN, drop it after."""
    dbname = f"ownevo_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_admin_url())
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()

    base = os.environ[ENV_VAR]
    test_url = base.rsplit("/", 1)[0] + f"/{dbname}"

    try:
        yield test_url
    finally:
        admin = await asyncpg.connect(_admin_url())
        try:
            # Terminate any lingering connections to the test DB before dropping.
            await admin.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = $1 AND pid <> pg_backend_pid()
                """,
                dbname,
            )
            await admin.execute(f'DROP DATABASE "{dbname}"')
        finally:
            await admin.close()


async def test_migration_files_discovered():
    """We expect at least the substrate migration."""
    files = migration_files()
    assert any(f.name == "0001_substrate.sql" for f in files), [f.name for f in files]


async def test_migrate_creates_full_schema(fresh_db: str):
    """Apply migrations against a fresh DB; assert tables, enums, views."""
    conn = await asyncpg.connect(fresh_db)
    try:
        applied = await migrate(conn)
        assert "0001_substrate.sql" in applied

        tables = await conn.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'
            ORDER BY table_name
            """,
        )
        names = {r["table_name"] for r in tables}
        assert names == {
            "approvals",
            "audit_entries",
            "captured_sandbox_runs",
            "demo_budget_state",
            "demo_invite_revocations",
            "demo_usage",
            "eval_cases",
            "failure_clusters",
            "integration_credentials",
            "iteration_case_outputs",
            "iterations",
            "learnings",
            "mcp_oauth_clients",
            "mcp_oauth_states",
            "mcp_servers",
            "meta_evals",
            "proposals",
            "receiver_tokens",
            "skill_deployments",
            "skill_versions",
            "skills",
            "traces",
            "workflows",
        }

        enums = await conn.fetch(
            "SELECT typname FROM pg_type WHERE typtype='e' ORDER BY typname",
        )
        assert {r["typname"] for r in enums} == {
            "approver_type",
            "audit_kind",
            "iteration_state",
            "proposal_kind",
            "proposal_state",
            "provenance_kind",
            "sandbox_error_class",
            "skill_kind",
            "workflow_mode",
        }
    finally:
        await conn.close()


async def test_audit_entries_worm_blocks_update_delete_truncate(fresh_db: str):
    """D2 — audit log is append-only. INSERT ok; UPDATE/DELETE/TRUNCATE raise."""
    conn = await asyncpg.connect(fresh_db)
    try:
        await migrate(conn)

        await conn.execute(
            "INSERT INTO audit_entries (kind, payload, actor) "
            "VALUES ('schema-migration', '{\"v\":1}', 'test')",
        )
        count = await conn.fetchval("SELECT count(*) FROM audit_entries")
        assert count == 1

        for stmt in (
            "UPDATE audit_entries SET actor='hacker'",
            "DELETE FROM audit_entries",
            "TRUNCATE audit_entries",
        ):
            with pytest.raises(asyncpg.PostgresError, match="WORM"):
                await conn.execute(stmt)

        # Row is still there.
        count = await conn.fetchval("SELECT count(*) FROM audit_entries")
        assert count == 1
    finally:
        await conn.close()


async def test_pool_scope_round_trips(fresh_db: str):
    """`open_pool` returns a usable pool against the configured URL."""
    pool = await open_pool(fresh_db)
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval("SELECT 1")
            assert value == 1
    finally:
        await pool.close()
