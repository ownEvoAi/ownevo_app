"""Boot-time and per-request guard tests.

Verify that the kernel refuses to start (lifespan guard) and refuses to serve
(per-request guard in get_principal) when environment variables are in a
mutually-exclusive or dangerous combination, rather than silently allowing the
misconfiguration.

These tests do not require a database — they exercise the pre-pool-open
guard logic (lifespan) and the request-resolution guard (deps) only.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from ownevo_kernel.api._internal_auth import DEV_AUTH_ENV, INTERNAL_AUTH_KEY_ENV
from ownevo_kernel.api.app import create_app
from ownevo_kernel.api.deps import get_principal


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
    assert "OWNEVO_DATABASE_URL" in str(exc_info.value), (
        "Expected the DB-url missing error"
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
    assert "OWNEVO_DATABASE_URL" in str(exc_info.value), (
        "Expected the DB-url missing error"
    )


# --- Per-request guard in get_principal ---
#
# The lifespan guard fires once at startup. If OWNEVO_DEV_AUTH and
# OWNEVO_INTERNAL_AUTH_KEY are both injected into a running process (e.g.
# `kubectl set env` without a restart), the lifespan guard doesn't re-fire.
# get_principal() has a matching per-request check that closes this window.


def _request_no_auth() -> object:
    """Minimal mock of a FastAPI Request with no Authorization header."""
    return SimpleNamespace(headers={})


def test_get_principal_rejects_dev_auth_with_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_principal() must raise HTTP 500 when dev-auth and signing key are
    both present — even if the lifespan guard was bypassed at startup."""
    monkeypatch.setenv(DEV_AUTH_ENV, "true")
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, "live-production-key")

    with pytest.raises(HTTPException) as exc_info:
        get_principal(_request_no_auth())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 500
    assert "misconfiguration" in exc_info.value.detail


def test_get_principal_dev_auth_without_key_returns_dev_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal local-dev path: dev-auth on, no signing key → dev principal."""
    monkeypatch.setenv(DEV_AUTH_ENV, "true")
    monkeypatch.delenv(INTERNAL_AUTH_KEY_ENV, raising=False)

    principal = get_principal(_request_no_auth())  # type: ignore[arg-type]
    assert principal.user_id == "dev-user"
