"""Async Postgres connection + migration helpers.

The kernel reads/writes the schema defined in `apps/kernel/migrations/`.
This module is the only place that opens a connection pool — everything
else takes a pool or a connection by parameter.

Connection string lives in `OWNEVO_DATABASE_URL`. For local dev use the
compose file under `infra/`:

    OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:5432/ownevo

`migrate()` runs the SQL files in lexicographic order against a connection
that already exists (so tests can spin up a fresh database, run migrations,
and tear down without round-tripping through compose).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

ENV_VAR = "OWNEVO_DATABASE_URL"

# Pool sizing and per-connection statement timeout. Defaults are tuned for a
# single Next.js dev server in front of the kernel; production deployments
# override via env to match their actual concurrency footprint.
POOL_MIN_SIZE_ENV = "OWNEVO_DB_POOL_MIN_SIZE"
POOL_MAX_SIZE_ENV = "OWNEVO_DB_POOL_MAX_SIZE"
STATEMENT_TIMEOUT_MS_ENV = "OWNEVO_DB_STATEMENT_TIMEOUT_MS"

DEFAULT_POOL_MIN_SIZE = 1
DEFAULT_POOL_MAX_SIZE = 10
# 30s caps any single query so one runaway statement can't pin a connection
# indefinitely. The orphan reaper (jobs/orphan_reaper.py) handles the
# coarser case of an entire iteration row stuck across a restart.
DEFAULT_STATEMENT_TIMEOUT_MS = 30_000

# `apps/kernel/migrations/` relative to this file (src/ownevo_kernel/db.py)
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def database_url() -> str:
    """Return the configured database URL or raise."""
    url = os.environ.get(ENV_VAR)
    if not url:
        raise RuntimeError(
            f"{ENV_VAR} not set. See infra/README.md for the dev compose stack.",
        )
    return url


def _parse_non_negative_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name}={raw!r} is not a valid integer"
        ) from exc
    if value < 0:
        raise ValueError(
            f"{name}={raw!r} must be >= 0"
        )
    return value


def pool_min_size_from_env() -> int:
    value = _parse_non_negative_int_env(POOL_MIN_SIZE_ENV, DEFAULT_POOL_MIN_SIZE)
    if value < 1:
        raise ValueError(
            f"{POOL_MIN_SIZE_ENV}={value} must be >= 1; a zero floor "
            "means every request pays the connect handshake."
        )
    return value


def pool_max_size_from_env() -> int:
    value = _parse_non_negative_int_env(POOL_MAX_SIZE_ENV, DEFAULT_POOL_MAX_SIZE)
    if value < 1:
        raise ValueError(
            f"{POOL_MAX_SIZE_ENV}={value} must be >= 1"
        )
    return value


def statement_timeout_ms_from_env() -> int:
    """Per-connection statement_timeout in milliseconds. ``0`` disables it."""
    return _parse_non_negative_int_env(
        STATEMENT_TIMEOUT_MS_ENV, DEFAULT_STATEMENT_TIMEOUT_MS
    )


def _make_setup(statement_timeout_ms: int):
    """Build an asyncpg pool ``setup`` callback that applies session GUCs.

    asyncpg's pool runs ``DISCARD ALL`` (which includes ``RESET ALL``) when
    a connection is released, so any ``SET`` issued on an earlier acquire
    is wiped. The ``setup`` callback runs after the reset and before the
    next caller sees the connection, which is exactly when this needs to
    re-apply.
    """
    if statement_timeout_ms <= 0:
        return None

    timeout_value = f"{statement_timeout_ms}ms"

    async def _setup(conn: asyncpg.Connection) -> None:
        await conn.execute(
            "SELECT set_config('statement_timeout', $1, false)", timeout_value
        )

    return _setup


async def open_pool(
    url: str | None = None,
    *,
    min_size: int | None = None,
    max_size: int | None = None,
    statement_timeout_ms: int | None = None,
) -> asyncpg.Pool:
    """Open a connection pool. Caller is responsible for closing it.

    ``min_size`` / ``max_size`` / ``statement_timeout_ms`` default to the
    values parsed from ``OWNEVO_DB_POOL_MIN_SIZE`` / ``..._MAX_SIZE`` /
    ``OWNEVO_DB_STATEMENT_TIMEOUT_MS``. Pass an explicit value to override
    (used by tests that need a tighter or looser cap than production).
    """
    if min_size is None:
        min_size = pool_min_size_from_env()
    if max_size is None:
        max_size = pool_max_size_from_env()
    if statement_timeout_ms is None:
        statement_timeout_ms = statement_timeout_ms_from_env()
    if min_size > max_size:
        raise ValueError(
            f"pool min_size ({min_size}) > max_size ({max_size}); set "
            f"{POOL_MIN_SIZE_ENV}/{POOL_MAX_SIZE_ENV} consistently."
        )
    return await asyncpg.create_pool(
        dsn=url or database_url(),
        min_size=min_size,
        max_size=max_size,
        setup=_make_setup(statement_timeout_ms),
    )


@asynccontextmanager
async def pool_scope(
    url: str | None = None,
    **kwargs: int,
) -> AsyncIterator[asyncpg.Pool]:
    """`async with pool_scope() as pool:` — opens and closes for you."""
    pool = await open_pool(url, **kwargs)
    try:
        yield pool
    finally:
        await pool.close()


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """All `*.sql` files in lexicographic order. The numeric prefix
    (`0001_`, `0002_`, ...) is the contract."""
    return sorted(directory.glob("*.sql"))


def split_sql_statements(sql: str) -> list[str]:
    """Split a migration file into individual statements.

    Needed only for `-- ownevo:no-txn` migrations: asyncpg sends a
    multi-statement string via the simple query protocol, which Postgres
    wraps in an implicit transaction block. `CREATE INDEX CONCURRENTLY`
    and `VALIDATE CONSTRAINT` then fail or silently lose their lighter
    lock. Executing each statement on its own keeps every one in
    autocommit.

    The splitter strips `--` line comments and splits on `;`. Migration
    SQL in this repo is deliberately simple (no semicolons inside string
    literals or dollar-quoted bodies), so a naive split is safe here —
    it is NOT a general-purpose SQL parser.
    """
    no_comments = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    return [stmt.strip() for stmt in no_comments.split(";") if stmt.strip()]


async def migrate(conn: asyncpg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Run all migrations against `conn`. Returns the list of files applied.

    Used by tests to bootstrap a fresh database. Production migration runner
    (`apps/kernel/scripts/migrate.py`) additionally tracks applied versions
    in a `schema_migrations` table; that's overkill for the per-test fresh-DB
    pattern, so we re-apply every file from scratch each time.

    Two file shapes require running outside a transaction block:

      * `ALTER TYPE ... ADD VALUE` — Postgres < 16 forbids enum value
        additions inside a transaction.
      * `-- ownevo:no-txn` annotated migrations — VALIDATE CONSTRAINT and
        CREATE INDEX CONCURRENTLY also cannot run inside a transaction
        (or only get the lighter SHARE UPDATE EXCLUSIVE lock when they
        don't). The annotation marks the file as needing autocommit.

    Both cases are detected up-front; for those files we COMMIT any
    implicit transaction asyncpg has open before executing.
    """
    applied: list[str] = []
    for path in migration_files(directory):
        sql = path.read_text()
        needs_no_txn = (
            "ADD VALUE" in sql.upper()
            or "-- ownevo:no-txn" in sql
        )
        if needs_no_txn:
            # Run each statement on its own so none is wrapped in an
            # implicit transaction (CREATE INDEX CONCURRENTLY refuses to
            # run inside one; VALIDATE CONSTRAINT loses its lighter lock).
            await conn.execute("COMMIT")
            for statement in split_sql_statements(sql):
                await conn.execute(statement)
        else:
            await conn.execute(sql)
        applied.append(path.name)
    return applied
