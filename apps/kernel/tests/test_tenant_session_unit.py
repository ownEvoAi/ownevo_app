"""CI-runnable unit tests for tenant_session — no database required.

A recorder connection captures the SQL `set_workspace` issues, so the
GUC name and parameter binding are verified without a live Postgres. The
round-trip behaviour against a real server lives in
`test_tenant_session_db.py` (skipped when OWNEVO_DATABASE_URL is unset).
"""

from __future__ import annotations

from typing import Any

import pytest
from ownevo_kernel.tenant_session import (
    DEFAULT_WORKSPACE_ID,
    WORKSPACE_GUC,
    set_workspace,
)


class _RecorderConn:
    """Minimal asyncpg-shaped stub recording execute() calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return "SELECT 1"


async def test_set_workspace_issues_set_config() -> None:
    conn = _RecorderConn()
    await set_workspace(conn, "acme")  # type: ignore[arg-type]
    assert len(conn.calls) == 1
    sql, args = conn.calls[0]
    assert "set_config" in sql
    assert args == (WORKSPACE_GUC, "acme")


@pytest.mark.parametrize("bad", ["", "   "])
async def test_set_workspace_rejects_empty(bad: str) -> None:
    conn = _RecorderConn()
    with pytest.raises(ValueError, match="non-empty"):
        await set_workspace(conn, bad)  # type: ignore[arg-type]
    assert conn.calls == []


def test_default_workspace_id_is_stable() -> None:
    # The migration backfills every row to this literal; drift would orphan
    # existing rows from their workspace once RLS is enabled.
    assert DEFAULT_WORKSPACE_ID == "default"
