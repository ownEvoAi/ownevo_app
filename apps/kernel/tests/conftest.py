"""Shared pytest fixtures for DB-backed integration tests."""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR, migrate


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
