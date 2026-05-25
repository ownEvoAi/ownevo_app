"""DB-backed isolation tests for row-level security (migration 0034).

These prove the enforcement switch actually isolates tenants: a connection
bound to workspace A cannot read, update, or delete workspace B's rows; a
connection with no workspace bound sees nothing and cannot write; inserts
auto-stamp the active workspace; and a soft-deleted workspace becomes
unbindable, leaving its rows physically present but unreachable.

Skipped in CI (no Postgres service); run locally with OWNEVO_DATABASE_URL set.
The `rls_db` fixture (conftest) binds the default workspace, mirroring get_conn;
each test switches workspaces with set_workspace, which re-scopes RLS on the
same connection because the policy keys off the session GUC.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.tenant_session import (
    DEFAULT_WORKSPACE_ID,
    WorkspaceDeletedError,
    current_workspace,
    set_workspace,
    soft_delete_workspace,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; DB-backed test",
)


async def _make_workspace(rls_db: asyncpg.Connection, ws_id: str) -> None:
    # workspaces is the tenant registry and carries no RLS, so this insert
    # succeeds regardless of the workspace currently bound to the connection.
    await rls_db.execute(
        "INSERT INTO workspaces (id, name) VALUES ($1, $2)", ws_id, ws_id
    )


async def _insert_workflow(rls_db: asyncpg.Connection, wf_id: str) -> None:
    await rls_db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, 'd', '{}'::jsonb)",
        wf_id,
    )


async def _seed_proposal_chain(rls_db: asyncpg.Connection, prefix: str) -> str:
    """Seed the workflow→skill→version→iteration→proposal chain. Returns the
    proposal id. Every row auto-stamps the connection's active workspace; FK
    checks bypass RLS, so the chain stays internally consistent."""
    wf = f"{prefix}-wf"
    await _insert_workflow(rls_db, wf)
    skill = f"{prefix}-skill"
    await rls_db.execute(
        "INSERT INTO skills (id, kind, workflow_id) VALUES ($1, 'instruction', $2)",
        skill,
        wf,
    )
    version_id = await rls_db.fetchval(
        "INSERT INTO skill_versions (skill_id, version_seq, content, created_by) "
        "VALUES ($1, 1, 'c', 'nl-gen') RETURNING id",
        skill,
    )
    iteration_id = await rls_db.fetchval(
        "INSERT INTO iterations (workflow_id, iteration_index, parent_skill_version_id) "
        "VALUES ($1, 1, $2) RETURNING id",
        wf,
        version_id,
    )
    return await rls_db.fetchval(
        "INSERT INTO proposals "
        "(iteration_id, skill_id, proposed_content, plain_language_summary) "
        "VALUES ($1, $2, 'c', 's') RETURNING id::text",
        iteration_id,
        skill,
    )


async def test_workflows_isolated_between_workspaces(rls_db: asyncpg.Connection) -> None:
    await _make_workspace(rls_db, "wsa")
    await _make_workspace(rls_db, "wsb")

    await set_workspace(rls_db, "wsa")
    await _insert_workflow(rls_db, "wf-a")
    await set_workspace(rls_db, "wsb")
    await _insert_workflow(rls_db, "wf-b")

    # Each workspace sees only its own workflow.
    assert [r["id"] for r in await rls_db.fetch("SELECT id FROM workflows")] == ["wf-b"]
    await set_workspace(rls_db, "wsa")
    assert [r["id"] for r in await rls_db.fetch("SELECT id FROM workflows")] == ["wf-a"]


async def test_insert_auto_stamps_active_workspace(rls_db: asyncpg.Connection) -> None:
    await _make_workspace(rls_db, "wsa")
    await set_workspace(rls_db, "wsa")
    await _insert_workflow(rls_db, "wf-a")
    # No workspace_id passed in the INSERT — the column default stamps the GUC.
    stamped = await rls_db.fetchval("SELECT workspace_id FROM workflows WHERE id = 'wf-a'")
    assert stamped == "wsa"


async def test_unbound_connection_sees_nothing_and_cannot_write(
    rls_db: asyncpg.Connection,
) -> None:
    # Seed a row under the (fixture-bound) default workspace first.
    await _insert_workflow(rls_db, "wf-default")
    # Clear the session GUC the way asyncpg does on pool release.
    await rls_db.execute("RESET ALL")
    assert await current_workspace(rls_db) is None

    # With no workspace bound, the isolation policy matches no rows.
    assert await rls_db.fetch("SELECT id FROM workflows") == []
    # And a write fails closed: the column default resolves to NULL (GUC unset),
    # so the NOT NULL workspace_id rejects the row before it can be stored.
    with pytest.raises(asyncpg.PostgresError):
        await _insert_workflow(rls_db, "wf-orphan")


async def test_cannot_update_or_delete_other_workspace_rows(
    rls_db: asyncpg.Connection,
) -> None:
    await _make_workspace(rls_db, "wsa")
    await _make_workspace(rls_db, "wsb")
    await set_workspace(rls_db, "wsa")
    await _insert_workflow(rls_db, "wf-a")

    # From B, A's row is invisible: UPDATE/DELETE match zero rows rather than
    # silently mutating another tenant's data.
    await set_workspace(rls_db, "wsb")
    updated = await rls_db.execute("UPDATE workflows SET description = 'x' WHERE id = 'wf-a'")
    assert updated == "UPDATE 0"
    deleted = await rls_db.execute("DELETE FROM workflows WHERE id = 'wf-a'")
    assert deleted == "DELETE 0"

    # A's row is untouched.
    await set_workspace(rls_db, "wsa")
    assert await rls_db.fetchval("SELECT description FROM workflows WHERE id = 'wf-a'") == "d"


async def test_traces_isolated_between_workspaces(rls_db: asyncpg.Connection) -> None:
    await _make_workspace(rls_db, "wsa")
    await _make_workspace(rls_db, "wsb")

    await set_workspace(rls_db, "wsa")
    await rls_db.execute(
        "INSERT INTO traces (events, started_at) VALUES ('[]'::jsonb, now())"
    )
    await set_workspace(rls_db, "wsb")
    assert await rls_db.fetchval("SELECT count(*) FROM traces") == 0
    await set_workspace(rls_db, "wsa")
    assert await rls_db.fetchval("SELECT count(*) FROM traces") == 1


async def test_proposals_isolated_between_workspaces(rls_db: asyncpg.Connection) -> None:
    await _make_workspace(rls_db, "wsa")
    await _make_workspace(rls_db, "wsb")

    await set_workspace(rls_db, "wsa")
    proposal_a = await _seed_proposal_chain(rls_db, "a")
    await set_workspace(rls_db, "wsb")
    await _seed_proposal_chain(rls_db, "b")

    # B sees only its own proposal, not A's.
    ids_b = [r["id"] for r in await rls_db.fetch("SELECT id::text AS id FROM proposals")]
    assert proposal_a not in ids_b
    assert len(ids_b) == 1


async def test_audit_entries_isolated_between_workspaces(
    rls_db: asyncpg.Connection,
) -> None:
    await _make_workspace(rls_db, "wsa")
    await _make_workspace(rls_db, "wsb")

    await set_workspace(rls_db, "wsa")
    await rls_db.execute(
        "INSERT INTO audit_entries (kind, payload, actor) "
        "VALUES ('eval-case-added', '{}'::jsonb, 'test')"
    )
    # B cannot read A's append-only audit trail.
    await set_workspace(rls_db, "wsb")
    assert await rls_db.fetchval("SELECT count(*) FROM audit_entries") == 0
    await set_workspace(rls_db, "wsa")
    assert await rls_db.fetchval("SELECT count(*) FROM audit_entries") == 1


async def test_soft_deleted_workspace_is_unbindable(rls_db: asyncpg.Connection) -> None:
    await _make_workspace(rls_db, "doomed")
    assert await soft_delete_workspace(rls_db, "doomed") is True
    with pytest.raises(WorkspaceDeletedError):
        await set_workspace(rls_db, "doomed")
    # Idempotent: a second soft delete finds no live row.
    assert await soft_delete_workspace(rls_db, "doomed") is False


async def test_soft_delete_makes_rows_unreachable(rls_db: asyncpg.Connection) -> None:
    await _make_workspace(rls_db, "tmp")
    await set_workspace(rls_db, "tmp")
    await _insert_workflow(rls_db, "wf-tmp")

    # Soft-delete from a different (still-bindable) workspace.
    await set_workspace(rls_db, DEFAULT_WORKSPACE_ID)
    assert await soft_delete_workspace(rls_db, "tmp") is True

    # The workspace can no longer be bound, so nothing can re-enter its scope.
    with pytest.raises(WorkspaceDeletedError):
        await set_workspace(rls_db, "tmp")
    # The row was retained (not hard-deleted) but is unreachable from any other
    # workspace — the effective cascade is "unreachable", not "erased".
    assert (
        await rls_db.fetchval("SELECT count(*) FROM workflows WHERE id = 'wf-tmp'") == 0
    )
