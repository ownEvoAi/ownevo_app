"""Tests for the internal workspace-invite endpoints.

DB-backed tests cover the full mint → redeem → revoke lifecycle and the main
failure modes (wrong inviter role, redeemed-twice, revoked, expired). Pure
token tests do not need a database and run on every CI machine.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest
from httpx import ASGITransport

from ownevo_kernel.api._internal_auth import INTERNAL_AUTH_KEY_ENV
from ownevo_kernel.api._workspace_invites import (
    InviteTokenInvalid,
    mint_invite_token,
    verify_invite_token,
)
from ownevo_kernel.api.app import create_app
from ownevo_kernel.api.deps import get_pool

_KEY = "test-internal-service-key"
_DB_ENV = "OWNEVO_DATABASE_URL"
_BASE = "http://api.test"


# ---------------------------------------------------------------------------
# Token unit tests (no DB)
# ---------------------------------------------------------------------------


def test_mint_and_verify_roundtrip():
    token = mint_invite_token(invite_id="abc-123", ttl_seconds=60, signing_key=_KEY)
    assert verify_invite_token(token, _KEY) == "abc-123"


def test_verify_rejects_bad_signature():
    token = mint_invite_token(invite_id="abc-123", ttl_seconds=60, signing_key=_KEY)
    with pytest.raises(InviteTokenInvalid, match="bad signature"):
        verify_invite_token(token, "wrong-key")


def test_verify_rejects_expired_token():
    # Backdate the issued-at so the computed expiry is already past.
    token = mint_invite_token(
        invite_id="abc", ttl_seconds=10, signing_key=_KEY, issued_at=int(time.time()) - 100
    )
    with pytest.raises(InviteTokenInvalid, match="expired"):
        verify_invite_token(token, _KEY)


def test_verify_rejects_workspace_assertion_token():
    """A workspace-assertion token has {u,w,e} claims and no 'k' field — it
    must not validate as an invite token even when signed with the same key."""
    from ownevo_kernel.api._internal_auth import mint_workspace_assertion

    assertion = mint_workspace_assertion(
        user_id="u1", workspace_id="ws_1", ttl_seconds=60, signing_key=_KEY
    )
    with pytest.raises(InviteTokenInvalid):
        verify_invite_token(assertion, _KEY)


def test_verify_rejects_malformed():
    with pytest.raises(InviteTokenInvalid):
        verify_invite_token("not-a-token", _KEY)


# ---------------------------------------------------------------------------
# Auth tests (no DB)
# ---------------------------------------------------------------------------


def _no_db_client(monkeypatch, *, key: str | None = _KEY):
    if key is not None:
        monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, key)
    else:
        monkeypatch.delenv(INTERNAL_AUTH_KEY_ENV, raising=False)
    app = create_app()
    app.dependency_overrides[get_pool] = lambda: None
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url=_BASE)


@pytest.mark.asyncio
async def test_create_invite_missing_token(monkeypatch):
    async with _no_db_client(monkeypatch) as client:
        r = await client.post(
            "/api/internal/workspaces/ws_x/invites",
            json={
                "inviter_user_id": "u1",
                "invited_email": "a@b.com",
                "role": "member",
            },
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_redeem_invite_missing_token(monkeypatch):
    async with _no_db_client(monkeypatch) as client:
        r = await client.post(
            "/api/internal/invites/redeem",
            json={"token": "x.y", "redeemer_user_id": "u1"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_revoke_invite_missing_token(monkeypatch):
    async with _no_db_client(monkeypatch) as client:
        r = await client.post(
            "/api/internal/invites/abc/revoke",
            json={"actor_user_id": "u1"},
        )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# DB-backed lifecycle tests
# ---------------------------------------------------------------------------


_db_skip = pytest.mark.skipif(
    _DB_ENV not in os.environ,
    reason=f"{_DB_ENV} not set — skipping DB-backed tests",
)

_AUTH_HEADERS = {"authorization": f"Bearer {_KEY}"}


async def _create_user(api_client: httpx.AsyncClient, email: str) -> str:
    """Provision a user via the auth-sync endpoint and return its id."""
    r = await api_client.post(
        "/api/internal/auth/sync",
        headers=_AUTH_HEADERS,
        json={
            "provider": "test",
            "provider_sub": email,
            "email": email,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["user_id"]


async def _create_workspace(api_client: httpx.AsyncClient, user_id: str, name: str) -> str:
    r = await api_client.post(
        "/api/internal/workspaces",
        headers=_AUTH_HEADERS,
        json={"user_id": user_id, "name": name},
    )
    assert r.status_code == 201, r.text
    return r.json()["workspace_id"]


@_db_skip
@pytest.mark.asyncio
async def test_invite_full_lifecycle(api_client: httpx.AsyncClient, monkeypatch):
    """End-to-end: owner mints invite → invitee redeems → joins workspace."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "owner@test.local")
    invitee_id = await _create_user(api_client, "invitee@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "Lifecycle Test")

    mint = await api_client.post(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        json={
            "inviter_user_id": owner_id,
            "invited_email": "invitee@test.local",
            "role": "member",
        },
    )
    assert mint.status_code == 201, mint.text
    token = mint.json()["token"]

    redeem = await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": invitee_id},
    )
    assert redeem.status_code == 200, redeem.text
    data = redeem.json()
    assert data["workspace_id"] == ws_id
    assert data["workspace_name"] == "Lifecycle Test"
    assert data["role"] == "member"

    # Invitee can now sync and see the new workspace in their memberships.
    sync = await api_client.post(
        "/api/internal/auth/sync",
        headers=_AUTH_HEADERS,
        json={
            "provider": "test",
            "provider_sub": "invitee@test.local",
            "email": "invitee@test.local",
        },
    )
    assert sync.status_code == 200
    ws_ids = [w["id"] for w in sync.json()["workspaces"]]
    assert ws_id in ws_ids


@_db_skip
@pytest.mark.asyncio
async def test_create_invite_requires_admin(api_client: httpx.AsyncClient, monkeypatch):
    """A user who is not a member of the workspace cannot mint an invite."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "owner2@test.local")
    outsider_id = await _create_user(api_client, "outsider@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "Adminless")

    r = await api_client.post(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        json={
            "inviter_user_id": outsider_id,
            "invited_email": "x@y.com",
            "role": "member",
        },
    )
    assert r.status_code == 403


@_db_skip
@pytest.mark.asyncio
async def test_redeem_invite_idempotent_for_same_user(
    api_client: httpx.AsyncClient, monkeypatch
):
    """Redeeming twice from the same user is a no-op success, not an error."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "owner3@test.local")
    invitee_id = await _create_user(api_client, "invitee3@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "Idempotent")

    mint = await api_client.post(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        json={
            "inviter_user_id": owner_id,
            "invited_email": "invitee3@test.local",
            "role": "admin",
        },
    )
    token = mint.json()["token"]

    r1 = await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": invitee_id},
    )
    r2 = await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": invitee_id},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json() == r1.json()


@_db_skip
@pytest.mark.asyncio
async def test_redeem_invite_rejects_second_user(
    api_client: httpx.AsyncClient, monkeypatch
):
    """A different user cannot consume an already-redeemed invite."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "owner4@test.local")
    first_id = await _create_user(api_client, "first4@test.local")
    second_id = await _create_user(api_client, "second4@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "OneShot")

    mint = await api_client.post(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        json={
            "inviter_user_id": owner_id,
            "invited_email": "first4@test.local",
            "role": "member",
        },
    )
    token = mint.json()["token"]
    await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": first_id},
    )
    r = await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": second_id},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invite_already_redeemed"


@_db_skip
@pytest.mark.asyncio
async def test_redeem_revoked_invite_fails(api_client: httpx.AsyncClient, monkeypatch):
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "owner5@test.local")
    invitee_id = await _create_user(api_client, "invitee5@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "Revoked")

    mint = await api_client.post(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        json={
            "inviter_user_id": owner_id,
            "invited_email": "invitee5@test.local",
            "role": "member",
        },
    )
    invite_id = mint.json()["invite_id"]
    token = mint.json()["token"]

    revoke = await api_client.post(
        f"/api/internal/invites/{invite_id}/revoke",
        headers=_AUTH_HEADERS,
        json={"actor_user_id": owner_id},
    )
    assert revoke.status_code == 204

    r = await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": invitee_id},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invite_revoked"


@_db_skip
@pytest.mark.asyncio
async def test_revoke_after_redeem_fails(api_client: httpx.AsyncClient, monkeypatch):
    """Revoking an already-consumed invite is a 409 — the membership row
    cannot be torn down by revoking the source invite (member removal is a
    separate operation, out of scope here)."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "owner6@test.local")
    invitee_id = await _create_user(api_client, "invitee6@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "PostRedeemRevoke")

    mint = await api_client.post(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        json={
            "inviter_user_id": owner_id,
            "invited_email": "invitee6@test.local",
            "role": "member",
        },
    )
    invite_id = mint.json()["invite_id"]
    token = mint.json()["token"]
    await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": invitee_id},
    )
    revoke = await api_client.post(
        f"/api/internal/invites/{invite_id}/revoke",
        headers=_AUTH_HEADERS,
        json={"actor_user_id": owner_id},
    )
    assert revoke.status_code == 409
