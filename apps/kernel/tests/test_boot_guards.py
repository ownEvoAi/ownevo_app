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
from ownevo_kernel.api._internal_auth import (
    DEPLOY_ENV_VAR,
    DEV_AUTH_ENV,
    INTERNAL_AUTH_KEY_ENV,
    PRODUCTION_ENV_VALUE,
)
from ownevo_kernel.api.app import create_app
from ownevo_kernel.api.deps import get_principal
from ownevo_kernel.secrets import generate_master_key
from ownevo_kernel.secrets.encrypted_field import MASTER_KEY_ENV


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


# --- Production-environment guards ---
#
# Triggered by OWNEVO_ENV=production. Independent of the dev-auth + signing-key
# guard above: they cover prod misconfigurations the existing guard misses,
# such as a prod deploy with dev-auth still on but no signing key (every
# request would 401 silently) or a prod deploy missing the credentials master
# key (would crash at the first integration write, not at boot).


@pytest.mark.asyncio
async def test_production_with_dev_auth_refuses_to_start(monkeypatch):
    """OWNEVO_ENV=production + OWNEVO_DEV_AUTH=true must raise at startup,
    even when OWNEVO_INTERNAL_AUTH_KEY is unset (the dev-only-shape combo
    the older guard does not catch)."""
    monkeypatch.setenv(DEPLOY_ENV_VAR, PRODUCTION_ENV_VALUE)
    monkeypatch.setenv(DEV_AUTH_ENV, "true")
    monkeypatch.delenv(INTERNAL_AUTH_KEY_ENV, raising=False)

    app = create_app()

    with pytest.raises(RuntimeError, match=DEV_AUTH_ENV):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_production_without_signing_key_refuses_to_start(monkeypatch):
    """OWNEVO_ENV=production must require OWNEVO_INTERNAL_AUTH_KEY."""
    monkeypatch.setenv(DEPLOY_ENV_VAR, PRODUCTION_ENV_VALUE)
    monkeypatch.delenv(DEV_AUTH_ENV, raising=False)
    monkeypatch.delenv(INTERNAL_AUTH_KEY_ENV, raising=False)
    monkeypatch.setenv(MASTER_KEY_ENV, generate_master_key())

    app = create_app()

    with pytest.raises(RuntimeError, match=INTERNAL_AUTH_KEY_ENV):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_production_without_credentials_master_key_refuses_to_start(monkeypatch):
    """OWNEVO_ENV=production must require OWNEVO_CREDENTIALS_MASTER_KEY."""
    monkeypatch.setenv(DEPLOY_ENV_VAR, PRODUCTION_ENV_VALUE)
    monkeypatch.delenv(DEV_AUTH_ENV, raising=False)
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, "stub-prod-key")
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)

    app = create_app()

    with pytest.raises(RuntimeError, match=MASTER_KEY_ENV):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_production_with_all_secrets_passes_guard(monkeypatch):
    """OWNEVO_ENV=production with dev-auth off and both required secrets set
    must not trip the prod guard. The lifespan continues to fail on the DB
    pool open, which is a separate, expected error."""
    monkeypatch.setenv(DEPLOY_ENV_VAR, PRODUCTION_ENV_VALUE)
    monkeypatch.delenv(DEV_AUTH_ENV, raising=False)
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, "stub-prod-key")
    monkeypatch.setenv(MASTER_KEY_ENV, "stub-fernet-key")
    monkeypatch.delenv("OWNEVO_DATABASE_URL", raising=False)

    app = create_app()

    with pytest.raises(RuntimeError) as exc_info:
        async with app.router.lifespan_context(app):
            pass

    err = str(exc_info.value)
    assert DEPLOY_ENV_VAR not in err
    assert DEV_AUTH_ENV not in err
    assert "OWNEVO_DATABASE_URL" in err


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
