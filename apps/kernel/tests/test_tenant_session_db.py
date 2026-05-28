"""DB-backed tests for the workspace substrate (migration 0033) + session GUC.

Skipped in CI (no Postgres service); run locally with OWNEVO_DATABASE_URL set.
Covers: the workspaces table is seeded, every scoped table gained a NOT NULL
workspace_id defaulting to 'default', and set_workspace/current_workspace
round-trip the session GUC against a real server.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.tenant_session import (
    DEFAULT_WORKSPACE_ID,
    UnknownWorkspaceError,
    connect_workspace_conn,
    current_workspace,
    set_workspace,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; DB-backed test",
)

# Every workspace-scoped table migration 0033 retrofits. Demo-mode tables are
# intentionally excluded (global rate-limiting state, not workspace data).
SCOPED_TABLES = [
    "workflows",
    "skills",
    "skill_versions",
    "skill_deployments",
    "eval_cases",
    "failure_clusters",
    "traces",
    "iterations",
    "iteration_case_outputs",
    "proposals",
    "approvals",
    "meta_evals",
    "learnings",
    "captured_sandbox_runs",
    "receiver_tokens",
    "integration_credentials",
    "audit_entries",
]


async def test_default_workspace_seeded(db: asyncpg.Connection) -> None:
    name = await db.fetchval(
        "SELECT name FROM workspaces WHERE id = $1", DEFAULT_WORKSPACE_ID
    )
    assert name == "Default workspace"


@pytest.mark.parametrize("table", SCOPED_TABLES)
async def test_table_has_workspace_id_column(
    db: asyncpg.Connection, table: str
) -> None:
    row = await db.fetchrow(
        """
        SELECT is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = $1 AND column_name = 'workspace_id'
        """,
        table,
    )
    assert row is not None, f"{table} missing workspace_id"
    assert row["is_nullable"] == "NO"
    # Migration 0034 replaced the literal 'default' default with the session
    # GUC, so an insert auto-stamps the active workspace (and fails closed when
    # the GUC is unset, since current_setting then yields NULL).
    assert "current_setting" in (row["column_default"] or "")


async def test_insert_backfills_default_workspace(db: asyncpg.Connection) -> None:
    await db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, $2, $3::jsonb)",
        "wf-tenant-test",
        "scope check",
        "{}",
    )
    workspace_id = await db.fetchval(
        "SELECT workspace_id FROM workflows WHERE id = $1", "wf-tenant-test"
    )
    assert workspace_id == DEFAULT_WORKSPACE_ID


async def test_set_and_current_workspace_round_trip(db: asyncpg.Connection) -> None:
    # The db fixture binds the default workspace, mirroring get_conn.
    assert await current_workspace(db) == DEFAULT_WORKSPACE_ID
    await db.execute(
        "INSERT INTO workspaces (id, name) VALUES ('acme', 'Acme')"
    )
    await set_workspace(db, "acme")
    assert await current_workspace(db) == "acme"


def _dsn_for(dbname: str) -> str:
    parsed = urlparse(os.environ[ENV_VAR])
    return urlunparse(parsed._replace(path=f"/{dbname}"))


async def test_connect_workspace_conn_yields_bound_connection(
    db: asyncpg.Connection,
) -> None:
    # Open a brand-new connection via the helper against the same test DB and
    # confirm the GUC was set before the yield -- a real round-trip rather than
    # a recorder stub.
    dbname = await db.fetchval("SELECT current_database()")
    async with connect_workspace_conn(_dsn_for(dbname), DEFAULT_WORKSPACE_ID) as conn:
        assert await current_workspace(conn) == DEFAULT_WORKSPACE_ID


async def test_connect_workspace_conn_refuses_unknown_workspace(
    db: asyncpg.Connection,
) -> None:
    dbname = await db.fetchval("SELECT current_database()")
    with pytest.raises(UnknownWorkspaceError):
        async with connect_workspace_conn(_dsn_for(dbname), "ghost"):
            pytest.fail("body should not run when bind fails")  # pragma: no cover
