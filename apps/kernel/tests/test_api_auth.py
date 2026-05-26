"""End-to-end auth tests for the request principal + membership gate.

Exercise the three resolution outcomes through a real ConnDep route
(`GET /api/workflows`): dev-auth fallback (200), a valid assertion for a
non-member (403), and no credentials with dev-auth disabled (401).
"""

from __future__ import annotations

import os

import httpx
import pytest
from ownevo_kernel.api._internal_auth import (
    DEV_USER_ID,
    INTERNAL_AUTH_KEY_ENV,
    mint_workspace_assertion,
)
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.tenant_session import DEFAULT_WORKSPACE_ID

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests (see infra/README.md)",
)


async def test_dev_auth_fallback_resolves_default_workspace(
    api_client: httpx.AsyncClient,
) -> None:
    """No assertion + OWNEVO_DEV_AUTH=true → seeded dev principal, 200."""
    resp = await api_client.get("/api/workflows")
    assert resp.status_code == 200


async def test_valid_assertion_for_non_member_is_forbidden(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correctly signed assertion still gets 403 if the user isn't a member."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, "test-key")
    token = mint_workspace_assertion(
        user_id="stranger",
        workspace_id=DEFAULT_WORKSPACE_ID,
        ttl_seconds=300,
        signing_key="test-key",
    )
    resp = await api_client.get(
        "/api/workflows", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


async def test_invalid_assertion_is_unauthorized(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bearer token that fails verification is 401, not 403."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, "test-key")
    token = mint_workspace_assertion(
        user_id="alice",
        workspace_id=DEFAULT_WORKSPACE_ID,
        ttl_seconds=300,
        signing_key="a-different-key",
    )
    resp = await api_client.get(
        "/api/workflows", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


async def test_no_credentials_fails_closed_when_dev_auth_off(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With dev-auth disabled and no assertion, the request is rejected (401)."""
    monkeypatch.delenv("OWNEVO_DEV_AUTH", raising=False)
    resp = await api_client.get("/api/workflows")
    assert resp.status_code == 401


async def test_valid_assertion_for_member_returns_ok(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid signed assertion for the seeded dev member resolves to 200.

    Exercises the full bearer-assertion path end-to-end through get_principal
    + verify_workspace_assertion + the membership gate in get_conn.
    """
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, "test-key")
    monkeypatch.delenv("OWNEVO_DEV_AUTH", raising=False)
    token = mint_workspace_assertion(
        user_id=DEV_USER_ID,
        workspace_id=DEFAULT_WORKSPACE_ID,
        ttl_seconds=300,
        signing_key="test-key",
    )
    resp = await api_client.get(
        "/api/workflows", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200


async def test_bearer_with_missing_signing_key_is_unauthorized(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deployment misconfiguration: bearer presented but key env var absent → 401."""
    monkeypatch.delenv("OWNEVO_DEV_AUTH", raising=False)
    monkeypatch.delenv(INTERNAL_AUTH_KEY_ENV, raising=False)
    resp = await api_client.get(
        "/api/workflows", headers={"Authorization": "Bearer any.token"}
    )
    assert resp.status_code == 401
