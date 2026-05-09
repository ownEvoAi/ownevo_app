"""W6 — one-shot bootstrap for `make m5-replay-30day`.

Three preflight steps that bit four times during the v4 30-day replay
setup and twice during v6 / v7:

  1. Create the target database (if it doesn't exist).
  2. Apply the migration files (`0001_substrate.sql`, `0002_*.sql`).
  3. Seed each condition's workflow row with the chosen baseline skill
     version's content as the parent (via ``seed_baseline``).

Idempotent: re-running on an already-bootstrapped DB is a no-op (DB
exists → skipped; migrations are guarded by ``IF NOT EXISTS`` /
``CREATE TABLE`` semantics already; seed_baseline skips skills whose
head version is identical to the on-disk file).

Usage
-----
::

    OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:54330/ownevo_30day_v7 \\
        python scripts/m5_replay_bootstrap.py \\
            --workflow-prefix m5-30day-v7 --conditions a,c,d --skill-version v2

The companion ``make m5-replay-bootstrap`` target wraps this with
``REPLAY_BOOTSTRAP_ARGS`` for forwarding flags.

Exit codes
----------
* ``0`` — bootstrap completed (or all steps were already done)
* ``2`` — ``OWNEVO_DATABASE_URL`` not set
* ``3`` — could not connect to admin DB to issue CREATE DATABASE
* ``4`` — migration file apply failed (SQL error)
* ``5`` — seed step failed
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from scripts.seed_m5_baseline import seed_baseline  # noqa: E402

ENV_DB_URL = "OWNEVO_DATABASE_URL"
MIGRATIONS_DIR = _KERNEL_ROOT / "migrations"
DEFAULT_CONDITIONS = "a,c,d"
DEFAULT_PREFIX = "m5-30day"
SUPPORTED_SKILL_VERSIONS = ("v1", "v2")
DEFAULT_SKILL_VERSION = "v1"


@dataclass(frozen=True)
class CliArgs:
    workflow_prefix: str
    conditions: tuple[str, ...]  # uppercase, e.g. ("A", "C", "D")
    skill_version: str
    admin_db_url: str | None  # None → derive from OWNEVO_DATABASE_URL
    drop_first: bool


def _conditions_arg(s: str) -> tuple[str, ...]:
    parts = [p.strip().upper() for p in s.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError(
            "--conditions must list at least one (e.g. 'a,c,d')"
        )
    for p in parts:
        if p not in ("A", "B", "C", "D"):
            raise argparse.ArgumentTypeError(f"unknown condition {p!r}")
    return tuple(parts)


def parse_args(argv: list[str]) -> CliArgs:
    parser = argparse.ArgumentParser(
        prog="m5_replay_bootstrap",
        description="Bootstrap the DB + migrations + workflow seeds for `make m5-replay-30day`.",
    )
    parser.add_argument(
        "--workflow-prefix",
        default=DEFAULT_PREFIX,
        help=(
            "Prefix for per-condition workflow_ids — full id is "
            f"f'{{prefix}}-{{condition.lower()}}'. Default: {DEFAULT_PREFIX!r}."
        ),
    )
    parser.add_argument(
        "--conditions",
        type=_conditions_arg,
        default=_conditions_arg(DEFAULT_CONDITIONS),
        help=(
            "Comma-separated condition letters to seed workflow rows for "
            "(default: 'a,c,d' — A=frozen, C=loop autonomous, D=loop+judge; "
            "B is deferred)."
        ),
    )
    parser.add_argument(
        "--skill-version",
        choices=SUPPORTED_SKILL_VERSIONS,
        default=DEFAULT_SKILL_VERSION,
        help=(
            "Which baseline skill version to register as each workflow's "
            f"parent. Default: {DEFAULT_SKILL_VERSION!r}."
        ),
    )
    parser.add_argument(
        "--admin-db-url",
        default=None,
        help=(
            "DB URL to use for the CREATE DATABASE step (must point at "
            "an existing DB the user can connect to — typically the "
            "'postgres' DB on the same server). If unset, derived from "
            f"${ENV_DB_URL} by replacing the dbname with 'postgres'."
        ),
    )
    parser.add_argument(
        "--drop-first",
        action="store_true",
        help=(
            "DROP DATABASE before CREATE — wipes prior state. Use only "
            "for clean-slate bootstraps. Refuses to drop the 'ownevo' "
            "main DB as a guardrail."
        ),
    )
    ns = parser.parse_args(argv)
    return CliArgs(
        workflow_prefix=ns.workflow_prefix,
        conditions=ns.conditions,
        skill_version=ns.skill_version,
        admin_db_url=ns.admin_db_url,
        drop_first=ns.drop_first,
    )


def _derive_admin_url(target_url: str) -> str:
    """Replace the path component (dbname) of ``target_url`` with ``/postgres``
    so we have an admin URL to issue CREATE DATABASE against."""
    parsed = urlparse(target_url)
    return urlunparse(parsed._replace(path="/postgres"))


def _dbname_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = parsed.path.lstrip("/")
    if not name:
        raise ValueError(f"could not extract dbname from {url!r}")
    if not re.match(r"^[A-Za-z0-9_]+$", name):
        raise ValueError(f"dbname has unsafe characters: {name!r}")
    return name


async def _ensure_database(target_url: str, admin_url: str, drop_first: bool) -> str:
    """Create (or recreate) the target database. Returns the dbname."""
    dbname = _dbname_from_url(target_url)
    if drop_first and dbname == "ownevo":
        raise RuntimeError(
            "refusing to drop the main 'ownevo' DB — pass a different dbname"
        )
    admin = await asyncpg.connect(admin_url, timeout=10)
    try:
        if drop_first:
            # TERMINATE other connections so DROP can proceed.
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                dbname,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            print(f"bootstrap: dropped existing database {dbname!r}", file=sys.stderr)
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", dbname
        )
        if exists:
            print(f"bootstrap: database {dbname!r} already exists", file=sys.stderr)
        else:
            await admin.execute(f'CREATE DATABASE "{dbname}"')
            print(f"bootstrap: created database {dbname!r}", file=sys.stderr)
    finally:
        await admin.close()
    return dbname


async def _apply_migrations(target_url: str) -> int:
    """Apply every `*.sql` in `migrations/` against the target DB. Returns
    the number of migration files applied (or 0 if none)."""
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        print(
            f"warning: no migration files found in {MIGRATIONS_DIR}",
            file=sys.stderr,
        )
        return 0
    conn = await asyncpg.connect(target_url, timeout=10)
    try:
        for sql_path in sql_files:
            sql = sql_path.read_text()
            try:
                await conn.execute(sql)
            except asyncpg.PostgresError as exc:
                # Most migration scripts use IF NOT EXISTS; an error here
                # usually means the migration already ran (e.g. CREATE
                # TYPE without IF NOT EXISTS). Surface but don't crash if
                # the substrate already exists.
                msg = str(exc).lower()
                already_done = (
                    "already exists" in msg
                    or "duplicate object" in msg
                    or "duplicate_object" in msg
                )
                if not already_done:
                    raise
                print(
                    f"bootstrap: migration {sql_path.name} already applied "
                    f"(skipped: {exc})",
                    file=sys.stderr,
                )
            else:
                print(f"bootstrap: applied {sql_path.name}", file=sys.stderr)
    finally:
        await conn.close()
    return len(sql_files)


async def _seed_workflows(
    target_url: str,
    *,
    workflow_prefix: str,
    conditions: tuple[str, ...],
    skill_version: str,
) -> dict[str, int]:
    """Seed each per-condition workflow with the chosen skill version's
    parent skills. Returns a {condition: n_skills_registered} map."""
    conn = await asyncpg.connect(target_url, timeout=10)
    out: dict[str, int] = {}
    try:
        for cond in conditions:
            workflow_id = f"{workflow_prefix}-{cond.lower()}"
            result = await seed_baseline(
                conn,
                workflow_id=workflow_id,
                skill_version=skill_version,
            )
            out[cond] = len(result.registered)
            print(
                f"bootstrap: seeded workflow={workflow_id} "
                f"registered={len(result.registered)} skipped={len(result.skipped)}",
                file=sys.stderr,
            )
    finally:
        await conn.close()
    return out


async def main_async(args: CliArgs) -> int:
    target_url = os.environ.get(ENV_DB_URL)
    if not target_url:
        print(
            f"error: {ENV_DB_URL} is not set; bootstrap needs the "
            "target DB URL to know what to create.",
            file=sys.stderr,
        )
        return 2
    admin_url = args.admin_db_url or _derive_admin_url(target_url)
    try:
        dbname = await _ensure_database(target_url, admin_url, args.drop_first)
    except (asyncpg.PostgresError, OSError) as exc:
        print(f"error: could not connect to admin DB: {exc}", file=sys.stderr)
        return 3
    try:
        n_migrations = await _apply_migrations(target_url)
    except asyncpg.PostgresError as exc:
        print(f"error: migration apply failed: {exc}", file=sys.stderr)
        return 4
    try:
        registered = await _seed_workflows(
            target_url,
            workflow_prefix=args.workflow_prefix,
            conditions=args.conditions,
            skill_version=args.skill_version,
        )
    except asyncpg.PostgresError as exc:
        print(f"error: seed step failed: {exc}", file=sys.stderr)
        return 5
    print(
        f"bootstrap complete: db={dbname!r} migrations={n_migrations} "
        f"workflows_seeded={len(registered)} skill_version={args.skill_version!r}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv) if argv is not None else sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
