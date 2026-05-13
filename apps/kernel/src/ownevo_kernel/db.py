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


async def open_pool(
    url: str | None = None,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Open a connection pool. Caller is responsible for closing it."""
    return await asyncpg.create_pool(
        dsn=url or database_url(),
        min_size=min_size,
        max_size=max_size,
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


async def migrate(conn: asyncpg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Run all migrations against `conn`. Returns the list of files applied.

    Used by tests to bootstrap a fresh database. Production migration runner
    will track applied versions in a `schema_migrations` table — out of
    scope for the W1 substrate.

    Files containing `ALTER TYPE ... ADD VALUE` must run outside a transaction
    block (Postgres requirement). Those files are executed directly; all other
    files run via conn.execute() which asyncpg wraps in an implicit transaction.
    """
    applied: list[str] = []
    for path in migration_files(directory):
        sql = path.read_text()
        if "ADD VALUE" in sql.upper():
            # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
            # Use autocommit by executing outside any transaction context.
            await conn.execute("COMMIT")
            await conn.execute(sql)
        else:
            await conn.execute(sql)
        applied.append(path.name)
    return applied
