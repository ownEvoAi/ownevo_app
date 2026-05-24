"""Run all pending SQL migrations in order.

Tracks applied migrations in a `schema_migrations` table so it is safe
to run multiple times (idempotent). Used as the Fly.io release_command
and can be run manually:

    uv run --package ownevo-kernel --extra api python apps/kernel/scripts/migrate.py

Env:
    OWNEVO_DATABASE_URL  postgres connection string (required)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


async def run() -> None:
    db_url = os.environ.get("OWNEVO_DATABASE_URL")
    if not db_url:
        print("ERROR: OWNEVO_DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename   TEXT        PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        applied = {
            r["filename"]
            for r in await conn.fetch(
                "SELECT filename FROM schema_migrations ORDER BY filename"
            )
        }

        pending = sorted(
            f for f in MIGRATIONS_DIR.glob("*.sql") if f.name not in applied
        )

        if not pending:
            print("No pending migrations.")
            return

        for path in pending:
            print(f"  applying {path.name} ...", end=" ", flush=True)
            sql = path.read_text()
            # Two cases require running outside a transaction:
            #
            #   ALTER TYPE ... ADD VALUE
            #     Cannot run inside a transaction on Postgres < 16.
            #
            #   -- ownevo:no-txn annotation
            #     Migrations that include VALIDATE CONSTRAINT or
            #     CREATE INDEX CONCURRENTLY must run outside a transaction.
            #     VALIDATE CONSTRAINT only gets the lighter SHARE UPDATE
            #     EXCLUSIVE lock when run outside any transaction; inside a
            #     transaction the ACCESS EXCLUSIVE lock from the preceding
            #     ADD CONSTRAINT NOT VALID is still held. CREATE INDEX
            #     CONCURRENTLY refuses to run inside a transaction block
            #     entirely.
            #
            # In both cases the SQL runs directly on the connection, then
            # the migration record is written in its own short transaction.
            needs_no_txn = (
                "ADD VALUE" in sql.upper()
                or "-- ownevo:no-txn" in sql
            )
            if needs_no_txn:
                # Execute each statement on its own. A multi-statement
                # string is sent via the simple query protocol, which
                # Postgres wraps in an implicit transaction block —
                # CREATE INDEX CONCURRENTLY refuses to run inside one and
                # VALIDATE CONSTRAINT loses its lighter SHARE UPDATE
                # EXCLUSIVE lock. Single statements stay in autocommit.
                from ownevo_kernel.db import split_sql_statements

                for statement in split_sql_statements(sql):
                    await conn.execute(statement)
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES ($1)",
                        path.name,
                    )
            else:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES ($1)",
                        path.name,
                    )
            print("done")

        print(f"Applied {len(pending)} migration(s).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
