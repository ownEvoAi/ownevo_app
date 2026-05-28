"""Startup reaper for iteration rows stranded in 'running' state.

The reaper runs once during the FastAPI lifespan startup. Any iteration
still in 'running' state after a kernel restart is orphaned (its
in-process driver task is gone) and must be closed so the workflow's
one-iteration-at-a-time guard does not block all future runs on that
workflow.

These tests exercise the reaper directly against a per-test Postgres DB.
The pool created here mirrors the production wire-up (min_size=1,
max_size=2). Each iteration row is seeded as 'running' with no ended_at
to mimic the post-INSERT pre-LLM state the runner leaves behind during
phase 1.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.jobs import REAPER_ACTOR, REAPER_REASON, reap_orphaned_iterations
from ownevo_kernel.tenant_session import (
    DEFAULT_WORKSPACE_ID,
    acquire_workspace_conn,
)
from ownevo_kernel.types import IterationState

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


async def _pool_for_db(db: asyncpg.Connection) -> asyncpg.Pool:
    dbname = await db.fetchval("SELECT current_database()")
    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2)


async def _seed_workflow(conn: asyncpg.Connection, workflow_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, $2, '{}'::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        workflow_id,
        f"test workflow {workflow_id}",
    )


async def _seed_iteration(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    iteration_index: int,
    state: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, ended_at)
        VALUES ($1, $2, $3::iteration_state,
                CASE WHEN $3 = 'running' THEN NULL ELSE now() END)
        """,
        workflow_id,
        iteration_index,
        state,
    )


async def test_reaper_closes_running_iteration(db: asyncpg.Connection) -> None:
    """A stuck 'running' row is rewritten to 'sandbox-error' with ended_at set."""
    await _seed_workflow(db, "wf-reap-running")
    await _seed_iteration(
        db,
        workflow_id="wf-reap-running",
        iteration_index=0,
        state="running",
    )

    pool = await _pool_for_db(db)
    try:
        reaped = await reap_orphaned_iterations(pool)
    finally:
        await pool.close()

    assert reaped == 1
    row = await db.fetchrow(
        "SELECT state, ended_at FROM iterations WHERE workflow_id = $1",
        "wf-reap-running",
    )
    assert row["state"] == IterationState.SANDBOX_ERROR.value
    assert row["ended_at"] is not None


async def test_reaper_leaves_terminal_states_alone(db: asyncpg.Connection) -> None:
    """gate-pass / gate-blocked / sandbox-error rows must not be touched."""
    await _seed_workflow(db, "wf-reap-terminal")
    terminal_states = [
        "gate-pass",
        "gate-blocked-regression",
        "gate-blocked-no-improvement",
        "sandbox-error",
    ]
    for idx, state in enumerate(terminal_states):
        await _seed_iteration(
            db,
            workflow_id="wf-reap-terminal",
            iteration_index=idx,
            state=state,
        )

    pool = await _pool_for_db(db)
    try:
        reaped = await reap_orphaned_iterations(pool)
    finally:
        await pool.close()

    assert reaped == 0
    rows = await db.fetch(
        "SELECT state FROM iterations WHERE workflow_id = $1 ORDER BY iteration_index",
        "wf-reap-terminal",
    )
    observed = [r["state"] for r in rows]
    assert observed == terminal_states


async def test_reaper_writes_audit_entry_per_orphan(db: asyncpg.Connection) -> None:
    """Each reaped row produces an iteration-reaped audit entry carrying the
    workflow id, iteration index, and reason — so the action is exportable
    in the hash-chained audit log."""
    await _seed_workflow(db, "wf-reap-audit")
    await _seed_iteration(
        db,
        workflow_id="wf-reap-audit",
        iteration_index=3,
        state="running",
    )
    iteration_id = await db.fetchval(
        "SELECT id FROM iterations WHERE workflow_id = $1",
        "wf-reap-audit",
    )

    pool = await _pool_for_db(db)
    try:
        await reap_orphaned_iterations(pool)
    finally:
        await pool.close()

    audit_row = await db.fetchrow(
        """
        SELECT kind, actor, related_id, payload
        FROM audit_entries
        WHERE related_id = $1 AND kind = 'iteration-reaped'
        """,
        iteration_id,
    )
    assert audit_row is not None
    assert audit_row["actor"] == REAPER_ACTOR
    payload = audit_row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["workflow_id"] == "wf-reap-audit"
    assert payload["iteration_index"] == 3
    assert payload["reason"] == REAPER_REASON
    assert payload["previous_state"] == IterationState.RUNNING.value
    assert payload["new_state"] == IterationState.SANDBOX_ERROR.value


async def test_reaper_isolates_workspaces(db: asyncpg.Connection) -> None:
    """A running row in workspace A is reaped without affecting workspace B's
    running row. Both rows are closed because both workspaces have an orphan,
    and each workspace's audit entry is visible only within that workspace's
    GUC scope (verified below via scoped connections).

    Note: the pool here connects as the local superuser, which bypasses
    FORCE ROW LEVEL SECURITY. The test therefore validates logical isolation
    (correct workspace_id stamping via the GUC default) rather than the RLS
    enforcement layer itself. RLS enforcement is covered by the rls_db
    fixture tests in test_rls_*.py.
    """
    pool = await _pool_for_db(db)
    try:
        # Workspace B in addition to the default.
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO workspaces (id, name) VALUES ($1, $2) "
                "ON CONFLICT (id) DO NOTHING",
                "ws-other",
                "Other workspace",
            )

        # Seed one orphan in each workspace via the GUC-bound connection so
        # the workspace_id default picks the bound workspace.
        async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
            await _seed_workflow(conn, "wf-ws-default")
            await _seed_iteration(
                conn,
                workflow_id="wf-ws-default",
                iteration_index=0,
                state="running",
            )
        async with acquire_workspace_conn(pool, "ws-other") as conn:
            await _seed_workflow(conn, "wf-ws-other")
            await _seed_iteration(
                conn,
                workflow_id="wf-ws-other",
                iteration_index=0,
                state="running",
            )

        reaped = await reap_orphaned_iterations(pool)
        assert reaped == 2

        # Both workspaces' rows are now terminal.
        async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
            row = await conn.fetchrow(
                "SELECT state FROM iterations WHERE workflow_id = $1",
                "wf-ws-default",
            )
            assert row["state"] == IterationState.SANDBOX_ERROR.value

            # The audit entry for this workspace's iteration must be visible
            # within the default workspace scope and must not bleed into the
            # other workspace's scope.
            default_iter_id = await conn.fetchval(
                "SELECT id FROM iterations WHERE workflow_id = $1",
                "wf-ws-default",
            )
            default_audit = await conn.fetchrow(
                "SELECT kind FROM audit_entries WHERE related_id = $1",
                default_iter_id,
            )
            assert default_audit is not None, "iteration-reaped entry missing in default workspace"

        async with acquire_workspace_conn(pool, "ws-other") as conn:
            row = await conn.fetchrow(
                "SELECT state FROM iterations WHERE workflow_id = $1",
                "wf-ws-other",
            )
            assert row["state"] == IterationState.SANDBOX_ERROR.value

            other_iter_id = await conn.fetchval(
                "SELECT id FROM iterations WHERE workflow_id = $1",
                "wf-ws-other",
            )
            other_audit = await conn.fetchrow(
                "SELECT kind FROM audit_entries WHERE related_id = $1",
                other_iter_id,
            )
            assert other_audit is not None, "iteration-reaped entry missing in ws-other"

            # The default workspace's iteration id must not appear in ws-other's scope.
            cross_leak = await conn.fetchrow(
                "SELECT id FROM audit_entries WHERE related_id = $1",
                default_iter_id,
            )
            assert cross_leak is None, "audit entry from default workspace leaked into ws-other"
    finally:
        await pool.close()


async def test_reaper_skips_soft_deleted_workspaces(db: asyncpg.Connection) -> None:
    """A soft-deleted workspace must not be enumerated by the reaper.

    `_list_active_workspaces` filters on `deleted_at IS NULL`, so
    `acquire_workspace_conn` (which refuses to bind a deleted workspace)
    is never called against a soft-deleted id. We seed a real 'running'
    iteration in the soft-deleted workspace (via the unscoped `db`
    connection, which bypasses the GUC constraint) and then verify that
    the reaper returns 0 AND leaves the row untouched — distinguishing
    "skipped because soft-deleted" from "nothing to reap".
    """
    pool = await _pool_for_db(db)
    try:
        # Create the workspace as active first so we can insert a workflow
        # and iteration row under it, then soft-delete it.
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO workspaces (id, name) VALUES ($1, $2) "
                "ON CONFLICT (id) DO NOTHING",
                "ws-soft-deleted",
                "Soft-deleted workspace",
            )

        # Seed a workflow and running iteration directly via the unscoped db
        # connection (bypasses workspace GUC, uses the literal workspace_id).
        await db.execute(
            "INSERT INTO workflows (id, description, spec, workspace_id) "
            "VALUES ($1, $2, '{}'::jsonb, $3) ON CONFLICT (id) DO NOTHING",
            "wf-soft-deleted",
            "test workflow wf-soft-deleted",
            "ws-soft-deleted",
        )
        await db.execute(
            "INSERT INTO iterations (workflow_id, iteration_index, state, ended_at, workspace_id) "
            "VALUES ($1, 0, 'running'::iteration_state, NULL, $2)",
            "wf-soft-deleted",
            "ws-soft-deleted",
        )

        # Now soft-delete the workspace.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE workspaces SET deleted_at = now() WHERE id = $1",
                "ws-soft-deleted",
            )

        # The reaper must complete without attempting to bind the
        # soft-deleted workspace (which would raise WorkspaceDeletedError
        # inside acquire_workspace_conn).
        reaped = await reap_orphaned_iterations(pool)
        assert reaped == 0

        # The orphaned iteration in the soft-deleted workspace must remain
        # in 'running' state — proving the filter excluded it rather than
        # silently swallowing an error.
        row = await db.fetchrow(
            "SELECT state, ended_at FROM iterations WHERE workflow_id = $1",
            "wf-soft-deleted",
        )
        assert row["state"] == "running", "soft-deleted workspace iteration was incorrectly reaped"
        assert row["ended_at"] is None
    finally:
        await pool.close()


async def test_reaper_is_idempotent(db: asyncpg.Connection) -> None:
    """Running the reaper twice in a row reaps zero rows on the second pass."""
    await _seed_workflow(db, "wf-reap-twice")
    await _seed_iteration(
        db,
        workflow_id="wf-reap-twice",
        iteration_index=0,
        state="running",
    )

    pool = await _pool_for_db(db)
    try:
        first = await reap_orphaned_iterations(pool)
        second = await reap_orphaned_iterations(pool)
    finally:
        await pool.close()

    assert first == 1
    assert second == 0


async def test_reaper_handles_no_active_workspaces(db: asyncpg.Connection) -> None:
    """Every workspace soft-deleted reduces the sweep to a no-op, not an error.

    Mirrors the case where every tenant has been off-boarded but their
    audit history still lives in the DB (soft delete is reversible, so
    rows stay physically present).
    """
    pool = await _pool_for_db(db)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE workspaces SET deleted_at = now() WHERE deleted_at IS NULL"
            )

        reaped = await reap_orphaned_iterations(pool)
        assert reaped == 0
    finally:
        await pool.close()


async def test_reaper_continues_on_workspace_error(db: asyncpg.Connection) -> None:
    """A failing workspace must not block reaping of the remaining workspaces.

    The per-workspace except-Exception-continue guard in
    `reap_orphaned_iterations` is exercised by patching `_reap_in_workspace`
    to raise for one workspace. The reaper must log and continue, returning
    the count from the successful workspace without propagating the error.
    """
    from unittest.mock import patch

    import ownevo_kernel.jobs.orphan_reaper as _mod

    pool = await _pool_for_db(db)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO workspaces (id, name) VALUES ($1, $2) "
                "ON CONFLICT (id) DO NOTHING",
                "ws-failing",
                "Failing workspace",
            )

        # Seed an orphan in the default workspace so the successful path
        # reaps one row even as the other workspace errors.
        async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
            await _seed_workflow(conn, "wf-error-test")
            await _seed_iteration(
                conn,
                workflow_id="wf-error-test",
                iteration_index=0,
                state="running",
            )

        original = _mod._reap_in_workspace

        async def _fail_for_ws_failing(p: asyncpg.Pool, workspace_id: str) -> int:
            if workspace_id == "ws-failing":
                raise RuntimeError("simulated workspace reap failure")
            return await original(p, workspace_id)

        with patch.object(_mod, "_reap_in_workspace", side_effect=_fail_for_ws_failing):
            reaped = await reap_orphaned_iterations(pool)

        # The successful workspace contributes its 1 row; the failing one is skipped.
        assert reaped == 1
    finally:
        await pool.close()
