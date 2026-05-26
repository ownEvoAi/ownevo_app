"""Tests for the internal web→kernel auth-sync endpoint.

Exercise the service-token gate and the user/identity upsert + membership
read through `POST /api/internal/auth/sync`.
"""

from __future__ import annotations

import os

import httpx
import pytest
from ownevo_kernel.api._internal_auth import INTERNAL_AUTH_KEY_ENV
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests (see infra/README.md)",
)

_KEY = "test-internal-service-key"


async def test_sync_existing_dev_identity_returns_default_membership(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seeded dev identity resolves to dev-user, owner of 'default'."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    resp = await api_client.post(
        "/api/internal/auth/sync",
        headers={"Authorization": f"Bearer {_KEY}"},
        json={
            "provider": "dev",
            "provider_sub": "dev-user",
            "email": "dev@ownevo.local",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "dev-user"
    assert body["workspaces"] == [
        {"id": "default", "name": "Default workspace", "role": "owner"}
    ]


async def test_sync_new_user_is_created_with_no_memberships(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A brand-new provider identity gets a fresh user and zero memberships.

    No auto-provisioned workspace — that is the explicit create/join step.
    """
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    resp = await api_client.post(
        "/api/internal/auth/sync",
        headers={"Authorization": f"Bearer {_KEY}"},
        json={
            "provider": "google",
            "provider_sub": "google-sub-123",
            "email": "newcomer@example.com",
            "display_name": "New Comer",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"].startswith("usr_")
    assert body["workspaces"] == []

    # Idempotent: the same identity resolves to the same user id.
    again = await api_client.post(
        "/api/internal/auth/sync",
        headers={"Authorization": f"Bearer {_KEY}"},
        json={
            "provider": "google",
            "provider_sub": "google-sub-123",
            "email": "newcomer@example.com",
        },
    )
    assert again.json()["user_id"] == body["user_id"]


async def test_sync_links_new_identity_to_existing_email(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new provider sub for an existing email links to that user, not a new one."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    resp = await api_client.post(
        "/api/internal/auth/sync",
        headers={"Authorization": f"Bearer {_KEY}"},
        json={
            "provider": "google",
            "provider_sub": "google-sub-for-dev",
            "email": "dev@ownevo.local",
        },
    )
    assert resp.status_code == 200
    # Linked to the seeded dev user (same email) rather than minting a new id.
    assert resp.json()["user_id"] == "dev-user"


async def test_sync_missing_token_is_unauthorized(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    resp = await api_client.post(
        "/api/internal/auth/sync",
        json={"provider": "dev", "provider_sub": "dev-user", "email": "x@y.z"},
    )
    assert resp.status_code == 401


async def test_sync_wrong_token_is_unauthorized(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    resp = await api_client.post(
        "/api/internal/auth/sync",
        headers={"Authorization": "Bearer not-the-key"},
        json={"provider": "dev", "provider_sub": "dev-user", "email": "x@y.z"},
    )
    assert resp.status_code == 401


async def test_sync_key_unset_is_unavailable(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No shared key configured → 503, distinct from an auth failure."""
    monkeypatch.delenv(INTERNAL_AUTH_KEY_ENV, raising=False)
    resp = await api_client.post(
        "/api/internal/auth/sync",
        headers={"Authorization": "Bearer anything"},
        json={"provider": "dev", "provider_sub": "dev-user", "email": "x@y.z"},
    )
    assert resp.status_code == 503
