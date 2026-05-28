"""CI-runnable unit tests for tenant_session — no database required.

A recorder connection captures the SQL `set_workspace` issues, so the
GUC name and parameter binding are verified without a live Postgres. The
round-trip behaviour against a real server lives in
`test_tenant_session_db.py` (skipped when OWNEVO_DATABASE_URL is unset).
"""

from __future__ import annotations

from typing import Any

import pytest
from ownevo_kernel import tenant_session
from ownevo_kernel.tenant_session import (
    DEFAULT_WORKSPACE_ID,
    WORKSPACE_GUC,
    UnknownWorkspaceError,
    WorkspaceDeletedError,
    connect_workspace_conn,
    current_workspace,
    set_workspace,
)

_LIVE_WORKSPACE = object()  # sentinel: stub a live (non-deleted) workspace row


class _RecorderConn:
    """Minimal asyncpg-shaped stub recording the calls set_workspace issues.

    `set_workspace` first reads the workspace row (fetchrow) to confirm it
    exists and is not soft-deleted, then issues set_config. The stub returns a
    live workspace row by default; pass `fetchrow_return=None` for a missing
    workspace or a dict with a non-null `deleted_at` for a soft-deleted one.
    """

    def __init__(
        self, fetchval_return: Any = None, fetchrow_return: Any = _LIVE_WORKSPACE
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._fetchval_return = fetchval_return
        self._fetchrow_return = (
            {"deleted_at": None}
            if fetchrow_return is _LIVE_WORKSPACE
            else fetchrow_return
        )

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return "SELECT 1"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return self._fetchval_return

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return self._fetchrow_return


def _set_config_calls(conn: _RecorderConn) -> list[tuple[str, tuple[Any, ...]]]:
    return [(sql, args) for sql, args in conn.calls if "set_config" in sql]


async def test_set_workspace_issues_set_config() -> None:
    conn = _RecorderConn()
    await set_workspace(conn, "acme")  # type: ignore[arg-type]
    set_config = _set_config_calls(conn)
    assert len(set_config) == 1
    assert set_config[0][1] == (WORKSPACE_GUC, "acme")


@pytest.mark.parametrize("bad", ["", "   "])
async def test_set_workspace_rejects_empty(bad: str) -> None:
    conn = _RecorderConn()
    with pytest.raises(ValueError, match="non-empty"):
        await set_workspace(conn, bad)  # type: ignore[arg-type]
    assert conn.calls == []


async def test_set_workspace_rejects_unknown_workspace() -> None:
    # No workspace row -> bind is refused and no GUC is set.
    conn = _RecorderConn(fetchrow_return=None)
    with pytest.raises(UnknownWorkspaceError):
        await set_workspace(conn, "ghost")  # type: ignore[arg-type]
    assert _set_config_calls(conn) == []


async def test_set_workspace_rejects_deleted_workspace() -> None:
    # A soft-deleted workspace is unbindable -> its rows stay unreachable.
    import datetime

    conn = _RecorderConn(fetchrow_return={"deleted_at": datetime.datetime.now()})
    with pytest.raises(WorkspaceDeletedError):
        await set_workspace(conn, "gone")  # type: ignore[arg-type]
    assert _set_config_calls(conn) == []


async def test_set_workspace_passes_padded_value_verbatim() -> None:
    # workspace_id.strip() guards against all-whitespace IDs, but a value
    # like '  acme  ' passes the guard and is stored verbatim. Document this
    # so any future strip-on-write change is an explicit decision.
    conn = _RecorderConn()
    await set_workspace(conn, "  acme  ")  # type: ignore[arg-type]
    assert _set_config_calls(conn)[0][1][1] == "  acme  "


async def test_current_workspace_returns_none_when_unset() -> None:
    # Empty string is what Postgres returns for a custom GUC after RESET ALL;
    # current_workspace should normalize it to None so callers can use
    # `if current_workspace(conn)` as a presence check.
    conn = _RecorderConn(fetchval_return="")
    result = await current_workspace(conn)  # type: ignore[arg-type]
    assert result is None


async def test_current_workspace_returns_value_when_set() -> None:
    conn = _RecorderConn(fetchval_return="acme")
    result = await current_workspace(conn)  # type: ignore[arg-type]
    assert result == "acme"


async def test_current_workspace_returns_none_for_none() -> None:
    # Postgres returns NULL (Python None) when missing_ok=true and the GUC
    # was never set; both None and '' should surface as None.
    conn = _RecorderConn(fetchval_return=None)
    result = await current_workspace(conn)  # type: ignore[arg-type]
    assert result is None


def test_default_workspace_id_is_stable() -> None:
    # The migration backfills every row to this literal; drift would orphan
    # existing rows from their workspace once RLS is enabled.
    assert DEFAULT_WORKSPACE_ID == "default"


class _StubConnect:
    """Drop-in for ``asyncpg.connect`` that returns a _RecorderConn.

    Records the db_url each call received so tests can assert the helper
    forwarded its argument verbatim. Tracks closure so leak tests can verify
    the connection is torn down on success and on error.
    """

    def __init__(self, fetchrow_return: Any = _LIVE_WORKSPACE) -> None:
        self.urls: list[str] = []
        self.fetchrow_return = fetchrow_return
        self.conns: list[_RecorderConn] = []

    async def __call__(self, db_url: str) -> _RecorderConn:
        self.urls.append(db_url)
        conn = _RecorderConn(fetchrow_return=self.fetchrow_return)
        conn.closed = False  # type: ignore[attr-defined]

        async def close() -> None:
            conn.closed = True  # type: ignore[attr-defined]

        conn.close = close  # type: ignore[attr-defined,method-assign]
        self.conns.append(conn)
        return conn


async def test_connect_workspace_conn_binds_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubConnect()
    monkeypatch.setattr(tenant_session.asyncpg, "connect", stub)
    async with connect_workspace_conn("postgres://stub", "acme") as conn:
        assert stub.urls == ["postgres://stub"]
        # The helper must have bound the workspace before yielding.
        assert _set_config_calls(conn)[0][1] == (WORKSPACE_GUC, "acme")  # type: ignore[arg-type]
    assert stub.conns[0].closed is True  # type: ignore[attr-defined]


async def test_connect_workspace_conn_closes_on_body_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubConnect()
    monkeypatch.setattr(tenant_session.asyncpg, "connect", stub)
    with pytest.raises(RuntimeError, match="boom"):
        async with connect_workspace_conn("postgres://stub", "acme"):
            raise RuntimeError("boom")
    assert stub.conns[0].closed is True  # type: ignore[attr-defined]


async def test_connect_workspace_conn_closes_on_bind_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unknown workspace -> set_workspace raises; the helper must still close
    # the connection so the script doesn't leak a socket on misconfiguration.
    stub = _StubConnect(fetchrow_return=None)
    monkeypatch.setattr(tenant_session.asyncpg, "connect", stub)
    with pytest.raises(UnknownWorkspaceError):
        async with connect_workspace_conn("postgres://stub", "ghost"):
            pytest.fail("body should not run when bind fails")  # pragma: no cover
    assert stub.conns[0].closed is True  # type: ignore[attr-defined]
