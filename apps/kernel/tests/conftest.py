"""Shared pytest fixtures for DB-backed integration tests."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import httpx
import pytest
from httpx import ASGITransport
from ownevo_kernel.api.app import create_app
from ownevo_kernel.db import ENV_VAR, migrate
from ownevo_kernel.replay import CycleSummary


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


def stub_cycle(
    idx: int, *, val_score: float | None, n_prior_cases: int = 10
) -> CycleSummary:
    """Shared factory for CycleSummary test stubs."""
    return CycleSummary(
        cycle_index=idx,
        iteration_id=f"iter-{idx}",
        proposal_id=f"prop-{idx}",
        decision="gate-pass",
        val_score=val_score,
        best_ever_score_after=val_score,
        n_prior_cases=n_prior_cases,
        n_promotable=1,
        n_cluster_cases_added=1,
        judge_admitted=True,
    )


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


@pytest.fixture
async def api_client(db: asyncpg.Connection):
    """In-process FastAPI client bound to the per-test DB.

    Mirrors the per-file fixture that previously lived in
    test_api_proposals / test_api_workflows / test_api_audit. Tests that
    need both the raw connection (`db`) and the app (`api_client`) can
    depend on both — the pool here is independent so the two sessions
    don't share transaction state.
    """
    dbname = await db.fetchval("SELECT current_database()")
    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        app = create_app(pool=pool, cors_origins=[])
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=transport, base_url="http://api.test",
        ) as client:
            yield client
    finally:
        await pool.close()
