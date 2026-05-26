"""Shared pytest fixtures for DB-backed integration tests."""

from __future__ import annotations

import os
import socket
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
from ownevo_kernel.tenant_session import DEFAULT_WORKSPACE_ID, set_workspace


def _is_local_port_open(port: int, *, timeout: float = 0.25) -> bool:
    """Return True if a TCP connection to localhost:port succeeds quickly.

    Used by `requires_ollama` / `requires_lms` to gate integration tests that
    need a real local LLM daemon. CI hosts without these services will skip
    those tests; developer machines running them will exercise the full path.
    """
    try:
        with socket.create_connection(("localhost", port), timeout=timeout):
            return True
    except OSError:
        return False


# Skip-markers for tests that need a real local LLM daemon. Apply at the test
# function or class level: `@requires_ollama` / `@requires_lms`. Tests that
# only mock httpx do NOT need these markers — they should run unconditionally.
requires_ollama = pytest.mark.skipif(
    not _is_local_port_open(11434),
    reason="Ollama daemon not running on localhost:11434",
)
requires_lms = pytest.mark.skipif(
    not _is_local_port_open(1234),
    reason="LM Studio not running on localhost:1234",
)


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

# The test suite runs under dev-auth: API requests carry no signed identity
# assertion, so they resolve to the seeded dev principal in the default
# workspace (see api/_internal_auth.py). `setdefault` lets a single test opt
# out (e.g. to exercise the fail-closed 401 path) by clearing the var.
os.environ.setdefault("OWNEVO_DEV_AUTH", "true")


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
        # Bind the connection to the default workspace, mirroring what
        # get_conn does for every request connection. Migration 0034 forces
        # row-level security on every scoped table, so an unbound connection
        # would see no rows and reject every insert. Isolation tests that need
        # a different tenant call set_workspace explicitly to switch.
        await set_workspace(conn, DEFAULT_WORKSPACE_ID)
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
async def rls_db(db: asyncpg.Connection):
    """A connection that genuinely exercises row-level security.

    The `db` fixture connects as the local owning/superuser role, and Postgres
    exempts superusers (and BYPASSRLS roles) from RLS even when a table is
    FORCE'd — so isolation can only be proven from a plain, unprivileged role.
    This fixture creates one, grants it table/sequence access, and SET ROLEs a
    fresh connection to it. That mirrors production, where the kernel connects
    as a non-superuser application role.
    """
    dbname = await db.fetchval("SELECT current_database()")
    role = f"rls_test_{dbname.rsplit('_', 1)[-1]}"
    await db.execute(f'CREATE ROLE "{role}" NOSUPERUSER NOBYPASSRLS')
    await db.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    await db.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE "
        f'ON ALL TABLES IN SCHEMA public TO "{role}"'
    )
    await db.execute(
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"'
    )

    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'SET ROLE "{role}"')
        # Bind the default workspace, mirroring get_conn / the db fixture so
        # the connection can read and write before a test switches workspaces.
        await set_workspace(conn, DEFAULT_WORKSPACE_ID)
        yield conn
    finally:
        await conn.close()
        # A role can't be dropped while it still holds GRANTs; DROP OWNED BY
        # revokes them (and drops anything it owns) in this database first.
        await db.execute(f'DROP OWNED BY "{role}"')
        await db.execute(f'DROP ROLE IF EXISTS "{role}"')


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
