"""Shared pytest fixtures for DB-backed integration tests."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR, migrate


def _load_dotenv() -> None:
    """Load `.env` (repo root) into os.environ if present.

    Stdlib-only — no python-dotenv dep. Existing env vars take precedence
    so a one-shot `KEY=foo pytest ...` invocation overrides the file. Lines
    are KEY=VALUE; `export ` prefix is stripped, surrounding single/double
    quotes are stripped, blank lines and `#` comments are ignored.

    Search order: cwd/.env, then walk up to find the repo root .env. Stops
    at the first `.env` found.
    """
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        # Stop at the repo root — don't pick up ~/.env or /root/.env.
        if (parent / ".git").exists() and parent != here.parent:
            break
        candidate = parent / ".env"
        if candidate.is_file():
            for raw in candidate.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in ('"', "'")
                ):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
            return


_load_dotenv()


def _admin_url() -> str:
    """Return the admin (postgres) DB URL, preserving query params (e.g. sslmode)."""
    raw = os.environ[ENV_VAR]
    parsed = urlparse(raw)
    # Replace only the path component with /postgres; keep scheme, host, port, query.
    admin = parsed._replace(path="/postgres")
    return urlunparse(admin)


@pytest.fixture
async def db():
    dbname = f"ownevo_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_admin_url())
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()

    parsed = urlparse(os.environ[ENV_VAR])
    test_url = urlunparse(parsed._replace(path=f"/{dbname}"))
    conn = None
    try:
        conn = await asyncpg.connect(test_url)
        await migrate(conn)
        yield conn
    finally:
        if conn is not None:
            await conn.close()
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
