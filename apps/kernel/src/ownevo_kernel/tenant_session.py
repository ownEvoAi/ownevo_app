"""Workspace-scoped database sessions for multi-tenant isolation.

Every workspace-scoped domain table carries a ``workspace_id`` column (added
by migration ``0033_workspace_substrate.sql``). This module sets the Postgres
session GUC ``app.workspace_id`` on a connection so that row-level security
policies can scope reads and writes to the active workspace.

Until RLS is enabled the GUC is inert plumbing — nothing filters on it yet —
but wiring it in now keeps turning enforcement on a one-migration change
rather than a sweep across every query.

The value is set at session scope via ``set_config(name, value, is_local =>
false)``. asyncpg runs ``RESET ALL`` when a pooled connection is released,
which clears the custom GUC, so a workspace set on one request never leaks to
the next checkout of the same connection.
"""

from __future__ import annotations

import asyncpg

# Custom namespaced GUC. The ``app.`` prefix makes it a valid user-defined
# setting Postgres accepts without a prior definition.
WORKSPACE_GUC = "app.workspace_id"

# The workspace every pre-retrofit row was backfilled into. Used as the active
# workspace until per-request workspace resolution lands.
DEFAULT_WORKSPACE_ID = "default"


async def set_workspace(conn: asyncpg.Connection, workspace_id: str) -> None:
    """Bind ``conn`` to ``workspace_id`` for the remainder of its session.

    Raises ValueError for an empty workspace id so a missing value can never
    silently widen scope once RLS is enabled.
    """
    if not workspace_id or not workspace_id.strip():
        raise ValueError("workspace_id must be a non-empty string")
    await conn.execute("SELECT set_config($1, $2, false)", WORKSPACE_GUC, workspace_id)


async def current_workspace(conn: asyncpg.Connection) -> str | None:
    """Return the workspace bound to ``conn``, or None if unset."""
    value = await conn.fetchval("SELECT current_setting($1, true)", WORKSPACE_GUC)
    return value or None
