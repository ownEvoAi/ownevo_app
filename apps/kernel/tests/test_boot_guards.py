"""Boot-time guard tests.

Verify that the FastAPI lifespan refuses to start when environment variables
are in a mutually-exclusive or dangerous combination, rather than silently
allowing the misconfiguration to serve requests.

These tests do not require a database — they exercise the pre-pool-open
guard logic only.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport
import httpx

from ownevo_kernel.api.app import create_app
from ownevo_kernel.api._internal_auth import DEV_AUTH_ENV, INTERNAL_AUTH_KEY_ENV


@pytest.mark.asyncio
async def test_dev_auth_with_signing_key_refuses_to_start(monkeypatch):
    """Starting the kernel with OWNEVO_DEV_AUTH=true alongside
    OWNEVO_INTERNAL_AUTH_KEY set must raise RuntimeError at lifespan startup.

    Without this guard an unauthenticated request would silently resolve to
    the seeded dev user, bypassing workspace isolation in production.
    """
    monkeypatch.setenv(DEV_AUTH_ENV, "true")
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, "some-production-key")

    app = create_app()
    transport = ASGITransport(app=app)

    with pytest.raises(RuntimeError, match=DEV_AUTH_ENV):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_dev_auth_without_signing_key_starts_normally(monkeypatch):
    """OWNEVO_DEV_AUTH=true without OWNEVO_INTERNAL_AUTH_KEY is the
    expected local-dev configuration — the lifespan must not raise.

    We cannot open a real DB pool here, so we only exercise the guard
    code path (the pool-open call will fail on missing DATABASE_URL, which
    is expected and separate from the guard being tested).
    """
    monkeypatch.setenv(DEV_AUTH_ENV, "true")
    monkeypatch.delenv(INTERNAL_AUTH_KEY_ENV, raising=False)
    monkeypatch.delenv("OWNEVO_DATABASE_URL", raising=False)

    app = create_app()

    # The guard should pass (no RuntimeError about dev-auth + key co-existence).
    # The lifespan will then fail trying to open a DB pool — that's a different
    # error, not the one under test.
    with pytest.raises(RuntimeError) as exc_info:
        async with app.router.lifespan_context(app):
            pass

    assert DEV_AUTH_ENV not in str(exc_info.value), (
        "Expected a DB-url error, not the dev-auth guard"
    )


@pytest.mark.asyncio
async def test_no_dev_auth_with_signing_key_starts_normally(monkeypatch):
    """The normal production configuration (key set, dev-auth off) must not
    trigger the guard."""
    monkeypatch.delenv(DEV_AUTH_ENV, raising=False)
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, "some-production-key")
    monkeypatch.delenv("OWNEVO_DATABASE_URL", raising=False)

    app = create_app()

    with pytest.raises(RuntimeError) as exc_info:
        async with app.router.lifespan_context(app):
            pass

    assert DEV_AUTH_ENV not in str(exc_info.value)
