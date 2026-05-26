"""Workspace-scoped database sessions for multi-tenant isolation.

Every workspace-scoped domain table carries a ``workspace_id`` column (added
by migration ``0033_workspace_substrate.sql``) and, once
``0034_workspace_rls_enforcement.sql`` is applied, a row-level-security policy
that constrains reads and writes to the workspace named by the Postgres
session GUC ``app.workspace_id``.

This module is the single place that binds that GUC to a connection. Binding
goes through ``set_workspace`` (used by the request-scoped ``get_conn``
dependency) or ``acquire_workspace_conn`` (used by background workers and
scripts that acquire connections directly from the pool). Both refuse to bind
to a workspace that does not exist or has been soft-deleted, so a stale or
forged workspace id can never widen scope.

The value is set at session scope via ``set_config(name, value, is_local =>
false)``. asyncpg runs ``RESET ALL`` when a pooled connection is released,
which clears the custom GUC, so a workspace set on one request never leaks to
the next checkout of the same connection. A connection on which the GUC was
never set sees zero rows in every scoped table and cannot insert into any of
them once RLS is enforced -- unscoped access fails closed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# Custom namespaced GUC. The ``app.`` prefix makes it a valid user-defined
# setting Postgres accepts without a prior definition.
WORKSPACE_GUC = "app.workspace_id"

# The workspace every pre-retrofit row was backfilled into. Used as the active
# workspace until per-request workspace resolution lands.
DEFAULT_WORKSPACE_ID = "default"


class WorkspaceBindError(Exception):
    """A workspace could not be bound to a connection."""


class UnknownWorkspaceError(WorkspaceBindError):
    """No workspace row exists for the requested id."""


class WorkspaceDeletedError(WorkspaceBindError):
    """The requested workspace exists but has been soft-deleted."""


class WorkspaceMembershipError(WorkspaceBindError):
    """The authenticated user is not a member of the requested workspace."""


async def set_workspace(conn: asyncpg.Connection, workspace_id: str) -> None:
    """Bind ``conn`` to ``workspace_id`` for the remainder of its session.

    Validates the workspace before binding: an empty id raises ValueError, an
    id with no workspace row raises UnknownWorkspaceError, and a soft-deleted
    workspace raises WorkspaceDeletedError. Validating here -- the one chokepoint
    every connection passes through -- is what makes a soft-deleted workspace
    unbindable, so its rows become unreachable without being physically deleted.

    The ``workspaces`` table carries no RLS policy, so this lookup succeeds
    regardless of any workspace previously bound to the connection.
    """
    if not workspace_id or not workspace_id.strip():
        raise ValueError("workspace_id must be a non-empty string")
    row = await conn.fetchrow(
        "SELECT deleted_at FROM workspaces WHERE id = $1", workspace_id
    )
    if row is None:
        raise UnknownWorkspaceError(f"workspace {workspace_id!r} does not exist")
    if row["deleted_at"] is not None:
        raise WorkspaceDeletedError(f"workspace {workspace_id!r} is deleted")
    await conn.execute("SELECT set_config($1, $2, false)", WORKSPACE_GUC, workspace_id)


async def current_workspace(conn: asyncpg.Connection) -> str | None:
    """Return the workspace bound to ``conn``, or None if unset."""
    value = await conn.fetchval("SELECT current_setting($1, true)", WORKSPACE_GUC)
    return value or None


@asynccontextmanager
async def acquire_workspace_conn(
    pool: asyncpg.Pool,
    workspace_id: str,
    *,
    user_id: str | None = None,
) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a pooled connection already bound to ``workspace_id``.

    The workspace-scoped equivalent of ``pool.acquire()`` for code outside the
    request lifecycle -- background workers (iteration runner, auto-clustering,
    capturing sandbox) and scripts. Under RLS a raw ``pool.acquire()`` yields a
    connection with no workspace GUC, which sees nothing and writes nothing;
    routing every such acquire through this helper guarantees the GUC is set
    before the first query. Raises the same errors as ``set_workspace`` for a
    missing or deleted workspace.

    When called from a request context, pass ``user_id`` to enforce the same
    membership gate that ``get_conn`` applies. Background workers that operate
    at the system level (no per-user context) leave ``user_id`` unset.
    Raises ``WorkspaceMembershipError`` when the user is not a member.
    """
    async with pool.acquire() as conn:
        if user_id is not None:
            member = await conn.fetchval(
                "SELECT 1 FROM workspace_members wm "
                "JOIN workspaces w ON w.id = wm.workspace_id "
                "WHERE wm.user_id = $1 AND wm.workspace_id = $2 "
                "AND w.deleted_at IS NULL",
                user_id,
                workspace_id,
            )
            if member is None:
                raise WorkspaceMembershipError(
                    f"user {user_id!r} is not a member of workspace {workspace_id!r}"
                )
        await set_workspace(conn, workspace_id)
        yield conn


async def soft_delete_workspace(conn: asyncpg.Connection, workspace_id: str) -> bool:
    """Mark ``workspace_id`` deleted. Returns True if a live row was updated.

    Soft delete rather than a cascading hard delete: ``audit_entries`` is
    append-only (WORM) and cannot be row-deleted, and retaining the data keeps
    the operation reversible. Once ``deleted_at`` is set, ``set_workspace``
    refuses to bind the workspace, so no session can reach its rows -- the
    effective cascade is "unreachable", not "erased". Idempotent: a
    second call returns False because the row is no longer live.

    ``workspaces`` carries no RLS policy, so this is a registry-level operation
    independent of the caller's bound workspace.
    """
    result = await conn.execute(
        "UPDATE workspaces SET deleted_at = now() "
        "WHERE id = $1 AND deleted_at IS NULL",
        workspace_id,
    )
    return result.endswith("1")
