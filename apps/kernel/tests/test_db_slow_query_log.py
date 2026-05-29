"""Slow-query logging: the per-query decision (unit) + a live slow query.

The threshold/formatting decision is unit-tested against a hand-built
``LoggedQuery`` with no connection. The end-to-end "a real slow query emits a
WARNING" path is gated on ``OWNEVO_DATABASE_URL`` so it skips in the unit-only
CI job rather than erroring.
"""

from __future__ import annotations

import asyncio
import logging
import os

import pytest
from asyncpg.connection import LoggedQuery
from ownevo_kernel.db import (
    _MAX_LOGGED_QUERY_CHARS,
    DEFAULT_SLOW_QUERY_MS,
    ENV_VAR,
    SLOW_QUERY_MS_ENV,
    _make_query_logger,
    _slow_query_callback,
    open_pool,
    slow_query_ms_from_env,
)

LOGGER_NAME = "ownevo.db.slow_query"

requires_db = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


def _record(query: str = "SELECT 1", elapsed: float | None = 2.0) -> LoggedQuery:
    return LoggedQuery(
        query=query,
        args=(),
        timeout=None,
        elapsed=elapsed,
        exception=None,
        conn_addr=None,
        conn_params=None,
    )


# ---------------------------------------------------------------------------
# Env parsing
# ---------------------------------------------------------------------------


def test_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SLOW_QUERY_MS_ENV, raising=False)
    assert slow_query_ms_from_env() == DEFAULT_SLOW_QUERY_MS


def test_zero_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """``0`` is a valid opt-out, distinct from unset (which means default)."""
    monkeypatch.setenv(SLOW_QUERY_MS_ENV, "0")
    assert slow_query_ms_from_env() == 0


def test_blank_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SLOW_QUERY_MS_ENV, "")
    assert slow_query_ms_from_env() == DEFAULT_SLOW_QUERY_MS


def test_invalid_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SLOW_QUERY_MS_ENV, "soon")
    with pytest.raises(ValueError, match="not a valid integer"):
        slow_query_ms_from_env()


# ---------------------------------------------------------------------------
# Factory contract
# ---------------------------------------------------------------------------


def test_make_query_logger_none_when_disabled() -> None:
    """Threshold <= 0 means no init callback at all, so the per-query timing
    wrapper inside asyncpg is never even entered."""
    assert _make_query_logger(0) is None
    assert _make_query_logger(-5) is None


def test_make_query_logger_returns_callable_when_enabled() -> None:
    assert callable(_make_query_logger(1000))


async def test_open_pool_rejects_negative_slow_query() -> None:
    with pytest.raises(ValueError, match="slow_query_ms=.* must be >= 0"):
        await open_pool("postgresql://unused", slow_query_ms=-1)


# ---------------------------------------------------------------------------
# Per-query decision (no DB)
# ---------------------------------------------------------------------------


def test_logs_when_over_threshold(caplog: pytest.LogCaptureFixture) -> None:
    callback = _slow_query_callback(1000)  # 1s
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        callback(_record(query="SELECT pg_sleep(2)", elapsed=2.0))
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    # Structured fields the JSON formatter will surface as first-class keys.
    assert rec.slow_query is True
    assert rec.elapsed_ms == 2000.0
    assert rec.threshold_ms == 1000
    assert "pg_sleep" in rec.query


def test_silent_under_threshold(caplog: pytest.LogCaptureFixture) -> None:
    callback = _slow_query_callback(1000)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        callback(_record(elapsed=0.01))  # 10ms, well under 1000ms
    assert caplog.records == []


def test_none_elapsed_does_not_log(caplog: pytest.LogCaptureFixture) -> None:
    """A query that never completed (no elapsed) is not a slow query."""
    callback = _slow_query_callback(10)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        callback(_record(elapsed=None))
    assert caplog.records == []


def test_failed_query_does_not_log(caplog: pytest.LogCaptureFixture) -> None:
    """A query that raised an exception is not logged as slow even if it
    took a long time — the elapsed reflects e.g. a lock timeout, not a
    genuine slow-running statement, so logging it as slow would mislead."""
    callback = _slow_query_callback(10)
    record = LoggedQuery(
        query="SELECT 1",
        args=(),
        timeout=None,
        elapsed=5.0,
        exception=RuntimeError("connection reset"),
        conn_addr=None,
        conn_params=None,
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        callback(record)
    assert caplog.records == []


def test_query_whitespace_collapsed(caplog: pytest.LogCaptureFixture) -> None:
    callback = _slow_query_callback(10)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        callback(_record(query="SELECT\n   1\n  FROM   t", elapsed=0.5))
    assert caplog.records[0].query == "SELECT 1 FROM t"


def test_long_query_truncated(caplog: pytest.LogCaptureFixture) -> None:
    """One accidental multi-kilobyte statement can't blow up the log line."""
    callback = _slow_query_callback(10)
    long_query = "SELECT " + ("x," * 400) + "1"
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        callback(_record(query=long_query, elapsed=0.5))
    logged = caplog.records[0].query
    assert logged.endswith("...")
    assert len(logged) == _MAX_LOGGED_QUERY_CHARS + len("...")


def test_args_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Argument values can carry tenant data or secrets, so only the
    parameterised query text is logged — never the bound arguments."""
    callback = _slow_query_callback(10)
    record = LoggedQuery(
        query="SELECT * FROM users WHERE email = $1",
        args=("secret@example.com",),
        timeout=None,
        elapsed=0.5,
        exception=None,
        conn_addr=None,
        conn_params=None,
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        callback(record)
    rec = caplog.records[0]
    assert "secret@example.com" not in rec.getMessage()
    assert "secret@example.com" not in rec.query
    assert not hasattr(rec, "args") or "secret@example.com" not in str(rec.args)


# ---------------------------------------------------------------------------
# Live slow query (DB-backed)
# ---------------------------------------------------------------------------


@requires_db
async def test_live_slow_query_emits_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A real query that crosses the threshold logs a WARNING. asyncpg fires a
    synchronous query logger via loop.call_soon after the query returns, so we
    yield to the loop once before asserting."""
    pool = await open_pool(
        os.environ[ENV_VAR], min_size=1, max_size=1, slow_query_ms=50
    )
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT pg_sleep(0.2)")
            await asyncio.sleep(0.05)  # let the call_soon logger callback run
        slow = [r for r in caplog.records if getattr(r, "slow_query", False)]
        assert slow, "expected a slow-query WARNING for pg_sleep(0.2)"
        assert "pg_sleep" in slow[0].query
        assert slow[0].elapsed_ms >= 150
    finally:
        await pool.close()


@requires_db
async def test_fast_query_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A trivially fast query stays under any sane threshold — no WARNING."""
    pool = await open_pool(
        os.environ[ENV_VAR], min_size=1, max_size=1, slow_query_ms=1000
    )
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            await asyncio.sleep(0.05)
        assert [r for r in caplog.records if getattr(r, "slow_query", False)] == []
    finally:
        await pool.close()
