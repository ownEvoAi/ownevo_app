"""Tests for the internal workspace provisioning endpoint.

DB-backed tests (create workspace, verify membership, unknown user) require a
live Postgres instance and are automatically skipped when OWNEVO_DATABASE_URL
is not in the environment.  The token auth tests run without a database.
"""

from __future__ import annotations

import os

import httpx
import pytest
from httpx import ASGITransport

from ownevo_kernel.api.app import create_app
from ownevo_kernel.api._internal_auth import INTERNAL_AUTH_KEY_ENV
from ownevo_kernel.api.deps import get_pool

_KEY = "test-internal-service-key"
_DB_ENV = "OWNEVO_DATABASE_URL"
_BASE = "http://api.test"
_ENDPOINT = "/api/internal/workspaces"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_db_client(monkeypatch, *, key: str | None = _KEY):
    """Build a no-pool test client, optionally with the service key set.

    Overrides ``get_pool`` with a stub so FastAPI's dependency resolution
    does not raise before the auth check runs. The auth tests all return
    401/503 before the pool is actually used.
    """
    if key is not None:
        monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, key)
    else:
        monkeypatch.delenv(INTERNAL_AUTH_KEY_ENV, raising=False)
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: None  # pool never called on auth failure
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url=_BASE)


# ---------------------------------------------------------------------------
# Auth tests (no DB required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workspace_missing_token(monkeypatch):
    async with _no_db_client(monkeypatch) as client:
        r = await client.post(_ENDPOINT, json={"user_id": "dev-user", "name": "Acme"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_workspace_wrong_token(monkeypatch):
    async with _no_db_client(monkeypatch) as client:
        r = await client.post(
            _ENDPOINT,
            headers={"authorization": "Bearer wrong-key"},
            json={"user_id": "dev-user", "name": "Acme"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_workspace_key_unset(monkeypatch):
    async with _no_db_client(monkeypatch, key=None) as client:
        r = await client.post(
            _ENDPOINT,
            headers={"authorization": f"Bearer {_KEY}"},
            json={"user_id": "dev-user", "name": "Acme"},
        )
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------

_db_skip = pytest.mark.skipif(
    _DB_ENV not in os.environ,
    reason=f"{_DB_ENV} not set — skipping DB-backed tests",
)


@_db_skip
@pytest.mark.asyncio
async def test_create_workspace_success(api_client: httpx.AsyncClient, monkeypatch):
    """Creating a workspace returns workspace_id + name and writes the owner row."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    r = await api_client.post(
        _ENDPOINT,
        headers={"authorization": f"Bearer {_KEY}"},
        json={"user_id": "dev-user", "name": "Test Corp"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Test Corp"
    ws_id = data["workspace_id"]
    assert ws_id.startswith("ws_")

    # Verify the owner membership by re-syncing the dev-user principal.
    r2 = await api_client.post(
        "/api/internal/auth/sync",
        headers={"authorization": f"Bearer {_KEY}"},
        json={
            "provider": "dev",
            "provider_sub": "dev-user",
            "email": "dev@ownevo.local",
        },
    )
    assert r2.status_code == 200
    memberships = {w["id"]: w for w in r2.json()["workspaces"]}
    assert ws_id in memberships
    assert memberships[ws_id]["role"] == "owner"


@_db_skip
@pytest.mark.asyncio
async def test_create_workspace_unknown_user(api_client: httpx.AsyncClient, monkeypatch):
    """Provisioning a workspace for a non-existent user returns 422."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    r = await api_client.post(
        _ENDPOINT,
        headers={"authorization": f"Bearer {_KEY}"},
        json={"user_id": "usr_doesnotexist", "name": "Ghost Corp"},
    )
    assert r.status_code == 422


@_db_skip
@pytest.mark.asyncio
async def test_create_workspace_name_validation(api_client: httpx.AsyncClient, monkeypatch):
    """Names exceeding 80 characters are rejected by Pydantic before hitting the DB."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    long_name = "A" * 81
    r = await api_client.post(
        _ENDPOINT,
        headers={"authorization": f"Bearer {_KEY}"},
        json={"user_id": "dev-user", "name": long_name},
    )
    assert r.status_code == 422
