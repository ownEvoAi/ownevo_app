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


@pytest.mark.asyncio
async def test_list_invites_missing_token(monkeypatch):
    async with _no_db_client(monkeypatch) as client:
        r = await client.get(
            "/api/internal/workspaces/ws_x/invites",
            params={"actor_user_id": "u1"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_members_missing_token(monkeypatch):
    async with _no_db_client(monkeypatch) as client:
        r = await client.get(
            "/api/internal/workspaces/ws_x/members",
            params={"actor_user_id": "u1"},
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
async def test_redeem_invite_rejects_different_email(
    api_client: httpx.AsyncClient, monkeypatch
):
    """A user signed in with a different email cannot consume the invite.

    The email gate fires before the redeemed/expired/revoked checks, so a
    forwarded invite URL never lands the wrong person in a workspace they
    were not invited to.
    """
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "owner4@test.local")
    invitee_id = await _create_user(api_client, "first4@test.local")
    different_id = await _create_user(api_client, "second4@test.local")
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
    # Pre-redemption: different-email account is rejected on the email gate.
    pre = await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": different_id},
    )
    assert pre.status_code == 400
    assert pre.json()["detail"]["code"] == "invite_email_mismatch"
    # And still rejected after the rightful invitee redeems it.
    await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": invitee_id},
    )
    post = await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": token, "redeemer_user_id": different_id},
    )
    assert post.status_code == 400
    assert post.json()["detail"]["code"] == "invite_email_mismatch"


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


# ---------------------------------------------------------------------------
# List pending invites + list members
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_list_pending_invites_filters_inactive(
    api_client: httpx.AsyncClient, monkeypatch
):
    """Pending list excludes redeemed, revoked, and expired invites.

    Four invites are minted; one is redeemed, one is revoked, one is
    backdated past its expiry, and one is left untouched. Only the last
    should appear in the pending list.
    """
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "list-owner@test.local")
    redeemer_id = await _create_user(api_client, "list-redeemer@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "ListPending")

    async def _mint(email: str) -> dict:
        r = await api_client.post(
            f"/api/internal/workspaces/{ws_id}/invites",
            headers=_AUTH_HEADERS,
            json={
                "inviter_user_id": owner_id,
                "invited_email": email,
                "role": "member",
            },
        )
        assert r.status_code == 201, r.text
        return r.json()

    redeemed = await _mint("list-redeemer@test.local")
    revoked = await _mint("list-revoked@test.local")
    expired = await _mint("list-expired@test.local")
    fresh = await _mint("list-fresh@test.local")

    # Drive the lifecycle: redeem one, revoke one, backdate one into the past.
    await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": redeemed["token"], "redeemer_user_id": redeemer_id},
    )
    await api_client.post(
        f"/api/internal/invites/{revoked['invite_id']}/revoke",
        headers=_AUTH_HEADERS,
        json={"actor_user_id": owner_id},
    )
    # Direct DB nudge: drop the expiry into the past. Tests own the schema in
    # this fixture, so a one-row UPDATE is fine and avoids the otherwise
    # 24h-minimum TTL on the mint endpoint.
    pool = api_client._transport.app.state.pool  # type: ignore[attr-defined]
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE workspace_invites SET expires_at = now() - interval '1 minute' "
            "WHERE id = $1",
            expired["invite_id"],
        )

    r = await api_client.get(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        params={"actor_user_id": owner_id},
    )
    assert r.status_code == 200, r.text
    pending_ids = [i["invite_id"] for i in r.json()["invites"]]
    assert pending_ids == [fresh["invite_id"]]
    only = r.json()["invites"][0]
    assert only["invited_email"] == "list-fresh@test.local"
    assert only["role"] == "member"
    assert only["invited_by_user_id"] == owner_id
    assert only["invited_by_email"] == "list-owner@test.local"


@_db_skip
@pytest.mark.asyncio
async def test_list_pending_invites_requires_admin(
    api_client: httpx.AsyncClient, monkeypatch
):
    """A non-member cannot enumerate workspace invites."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "list-admin-owner@test.local")
    outsider_id = await _create_user(api_client, "list-admin-outsider@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "AdminGate")

    r = await api_client.get(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        params={"actor_user_id": outsider_id},
    )
    assert r.status_code == 403


@_db_skip
@pytest.mark.asyncio
async def test_list_members_returns_owner_then_others(
    api_client: httpx.AsyncClient, monkeypatch
):
    """Members are ordered owner → admin → member, oldest-first within role."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "lm-owner@test.local")
    admin_email = "lm-admin@test.local"
    member_email = "lm-member@test.local"
    admin_id = await _create_user(api_client, admin_email)
    member_id = await _create_user(api_client, member_email)
    ws_id = await _create_workspace(api_client, owner_id, "ListMembers")

    async def _invite_and_redeem(email: str, role: str, user_id: str) -> None:
        mint = await api_client.post(
            f"/api/internal/workspaces/{ws_id}/invites",
            headers=_AUTH_HEADERS,
            json={
                "inviter_user_id": owner_id,
                "invited_email": email,
                "role": role,
            },
        )
        assert mint.status_code == 201, mint.text
        rd = await api_client.post(
            "/api/internal/invites/redeem",
            headers=_AUTH_HEADERS,
            json={"token": mint.json()["token"], "redeemer_user_id": user_id},
        )
        assert rd.status_code == 200, rd.text

    await _invite_and_redeem(member_email, "member", member_id)
    await _invite_and_redeem(admin_email, "admin", admin_id)

    r = await api_client.get(
        f"/api/internal/workspaces/{ws_id}/members",
        headers=_AUTH_HEADERS,
        params={"actor_user_id": member_id},
    )
    assert r.status_code == 200, r.text
    rows = r.json()["members"]
    assert [m["role"] for m in rows] == ["owner", "admin", "member"]
    assert [m["user_id"] for m in rows] == [owner_id, admin_id, member_id]


@_db_skip
@pytest.mark.asyncio
async def test_list_members_requires_membership(
    api_client: httpx.AsyncClient, monkeypatch
):
    """A non-member is refused even the member list."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "lm-gate-owner@test.local")
    outsider_id = await _create_user(api_client, "lm-gate-outsider@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "MemberGate")

    r = await api_client.get(
        f"/api/internal/workspaces/{ws_id}/members",
        headers=_AUTH_HEADERS,
        params={"actor_user_id": outsider_id},
    )
    assert r.status_code == 403
