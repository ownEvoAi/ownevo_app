"""Per-connection statement_timeout on pool-acquired connections.

The pool's ``setup`` callback re-applies the GUC on every acquire,
because asyncpg's pool runs ``DISCARD ALL`` on release (which clears
``SET`` state). These tests pin both halves: the GUC is present on the
first acquire, survives a round-trip, and a query that exceeds it
raises ``QueryCanceledError`` rather than hanging forever.

Gated on ``OWNEVO_DATABASE_URL`` — the env-var parsing piece runs
unit-only in ``test_db_config.py``.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR, open_pool

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


def _admin_url() -> str:
    base = os.environ[ENV_VAR]
    if "/" not in base.rsplit("@", 1)[-1]:
        return base + "/postgres"
    return base.rsplit("/", 1)[0] + "/postgres"


@pytest.fixture
async def fresh_db():
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
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=$1 AND pid<>pg_backend_pid()",
                dbname,
            )
            await admin.execute(f'DROP DATABASE "{dbname}"')
        finally:
            await admin.close()


async def _statement_timeout(conn: asyncpg.Connection) -> str:
    """Return the current statement_timeout GUC formatted by Postgres
    (e.g. ``'250ms'``, ``'0'`` when unset)."""
    return await conn.fetchval("SHOW statement_timeout")


async def test_setup_applies_statement_timeout_on_acquire(
    fresh_db: str,
) -> None:
    pool = await open_pool(fresh_db, statement_timeout_ms=250)
    try:
        async with pool.acquire() as conn:
            assert await _statement_timeout(conn) == "250ms"
    finally:
        await pool.close()


async def test_zero_disables_statement_timeout(fresh_db: str) -> None:
    """statement_timeout_ms=0 must skip the SET entirely, leaving the
    server default in place (which is ``0`` on this Postgres install)."""
    pool = await open_pool(fresh_db, statement_timeout_ms=0)
    try:
        async with pool.acquire() as conn:
            assert await _statement_timeout(conn) == "0"
    finally:
        await pool.close()


async def test_timeout_cancels_long_query(fresh_db: str) -> None:
    """A query that exceeds the cap surfaces as QueryCanceledError —
    NOT a hang. ``pg_sleep`` is the cheapest way to exercise this."""
    pool = await open_pool(fresh_db, statement_timeout_ms=200)
    try:
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.QueryCanceledError):
                # 5 seconds is well past the 200ms cap; if the timeout
                # weren't applied this test would hang the suite.
                await conn.fetchval("SELECT pg_sleep(5)")
    finally:
        await pool.close()


async def test_timeout_reapplies_after_release(fresh_db: str) -> None:
    """asyncpg's pool issues DISCARD ALL when a connection is released,
    which clears every SET. The setup callback re-applies the GUC on
    the next acquire so the cap holds for every checkout, not just the
    first one. Two sequential acquires of the same underlying connection
    must both see the timeout."""
    pool = await open_pool(fresh_db, min_size=1, max_size=1, statement_timeout_ms=150)
    try:
        async with pool.acquire() as conn:
            assert await _statement_timeout(conn) == "150ms"
        async with pool.acquire() as conn:
            assert await _statement_timeout(conn) == "150ms"
    finally:
        await pool.close()
