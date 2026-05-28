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


@pytest.mark.asyncio
async def test_preview_invite_missing_token(monkeypatch):
    async with _no_db_client(monkeypatch) as client:
        r = await client.get(
            "/api/internal/invites/preview",
            params={"token": "x.y", "actor_user_id": "u1"},
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
    """Only owners and admins can enumerate workspace invites.

    Both a non-member (outsider) and a workspace member with role='member'
    must be refused — the gate checks the role value, not just presence.
    """
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "list-admin-owner@test.local")
    outsider_id = await _create_user(api_client, "list-admin-outsider@test.local")
    member_id = await _create_user(api_client, "list-admin-member@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "AdminGate")

    # Invite and redeem so member_id is a workspace member with role='member'.
    mint = await api_client.post(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        json={
            "inviter_user_id": owner_id,
            "invited_email": "list-admin-member@test.local",
            "role": "member",
        },
    )
    assert mint.status_code == 201, mint.text
    await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": mint.json()["token"], "redeemer_user_id": member_id},
    )

    # Non-member gets 403.
    r_outsider = await api_client.get(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        params={"actor_user_id": outsider_id},
    )
    assert r_outsider.status_code == 403

    # Workspace member (role='member') also gets 403 — the gate requires admin/owner.
    r_member = await api_client.get(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        params={"actor_user_id": member_id},
    )
    assert r_member.status_code == 403


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


# ---------------------------------------------------------------------------
# Preview endpoint
# ---------------------------------------------------------------------------


async def _mint_invite(
    api_client: httpx.AsyncClient,
    *,
    ws_id: str,
    inviter_id: str,
    email: str,
    role: str = "member",
) -> dict:
    r = await api_client.post(
        f"/api/internal/workspaces/{ws_id}/invites",
        headers=_AUTH_HEADERS,
        json={"inviter_user_id": inviter_id, "invited_email": email, "role": role},
    )
    assert r.status_code == 201, r.text
    return r.json()


@_db_skip
@pytest.mark.asyncio
async def test_preview_invite_pending(api_client: httpx.AsyncClient, monkeypatch):
    """Fresh invite + matching viewer email → status=pending with full metadata."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "preview-owner@test.local")
    invitee_id = await _create_user(api_client, "preview-invitee@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "PreviewPending")

    minted = await _mint_invite(
        api_client,
        ws_id=ws_id,
        inviter_id=owner_id,
        email="preview-invitee@test.local",
        role="admin",
    )

    r = await api_client.get(
        "/api/internal/invites/preview",
        headers=_AUTH_HEADERS,
        params={"token": minted["token"], "actor_user_id": invitee_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["workspace_id"] == ws_id
    assert body["workspace_name"] == "PreviewPending"
    assert body["invited_email"] == "preview-invitee@test.local"
    assert body["role"] == "admin"
    assert body["invited_by_email"] == "preview-owner@test.local"


@_db_skip
@pytest.mark.asyncio
async def test_preview_invite_email_mismatch(
    api_client: httpx.AsyncClient, monkeypatch
):
    """Viewer signed in as the wrong account → status=email_mismatch.

    Metadata is still returned so the page can show "this invite is for X"
    while pointing the viewer at the right account to sign in with.
    """
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "pm-owner@test.local")
    wrong_id = await _create_user(api_client, "pm-wrong@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "PreviewMismatch")
    minted = await _mint_invite(
        api_client, ws_id=ws_id, inviter_id=owner_id, email="pm-right@test.local"
    )

    r = await api_client.get(
        "/api/internal/invites/preview",
        headers=_AUTH_HEADERS,
        params={"token": minted["token"], "actor_user_id": wrong_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "email_mismatch"
    assert body["invited_email"] == "pm-right@test.local"
    assert body["workspace_name"] == "PreviewMismatch"


@_db_skip
@pytest.mark.asyncio
async def test_preview_invite_revoked(api_client: httpx.AsyncClient, monkeypatch):
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "pr-owner@test.local")
    invitee_id = await _create_user(api_client, "pr-invitee@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "PreviewRevoked")
    minted = await _mint_invite(
        api_client, ws_id=ws_id, inviter_id=owner_id, email="pr-invitee@test.local"
    )
    await api_client.post(
        f"/api/internal/invites/{minted['invite_id']}/revoke",
        headers=_AUTH_HEADERS,
        json={"actor_user_id": owner_id},
    )

    r = await api_client.get(
        "/api/internal/invites/preview",
        headers=_AUTH_HEADERS,
        params={"token": minted["token"], "actor_user_id": invitee_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "revoked"


@_db_skip
@pytest.mark.asyncio
async def test_preview_invite_redeemed_by_me_vs_other(
    api_client: httpx.AsyncClient, monkeypatch
):
    """Redeemed status distinguishes the original redeemer from a passerby."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "prd-owner@test.local")
    invitee_id = await _create_user(api_client, "prd-invitee@test.local")
    other_id = await _create_user(api_client, "prd-other@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "PreviewRedeemed")
    minted = await _mint_invite(
        api_client, ws_id=ws_id, inviter_id=owner_id, email="prd-invitee@test.local"
    )
    await api_client.post(
        "/api/internal/invites/redeem",
        headers=_AUTH_HEADERS,
        json={"token": minted["token"], "redeemer_user_id": invitee_id},
    )

    # Original redeemer revisits the URL — should be told they're already in.
    r1 = await api_client.get(
        "/api/internal/invites/preview",
        headers=_AUTH_HEADERS,
        params={"token": minted["token"], "actor_user_id": invitee_id},
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "redeemed_by_me"

    # Another user with a different (non-matching) email gets redeemed_by_other,
    # not email_mismatch — the redemption gate dominates so the UI surfaces the
    # most useful state: the invite is gone, not "wrong account".
    r2 = await api_client.get(
        "/api/internal/invites/preview",
        headers=_AUTH_HEADERS,
        params={"token": minted["token"], "actor_user_id": other_id},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "redeemed_by_other"


@_db_skip
@pytest.mark.asyncio
async def test_preview_invite_expired(
    api_client: httpx.AsyncClient, monkeypatch
):
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "pe-owner@test.local")
    invitee_id = await _create_user(api_client, "pe-invitee@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "PreviewExpired")
    minted = await _mint_invite(
        api_client, ws_id=ws_id, inviter_id=owner_id, email="pe-invitee@test.local"
    )

    # Drag the row's expiry into the past so the row-side check fires. The
    # token signature is still valid; the preview should still return 200 (not
    # invite_invalid) because we want the page to render the expired state
    # with metadata rather than a generic "bad link" error.
    pool = api_client._transport.app.state.pool  # type: ignore[attr-defined]
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE workspace_invites SET expires_at = now() - interval '1 minute' "
            "WHERE id = $1",
            minted["invite_id"],
        )

    r = await api_client.get(
        "/api/internal/invites/preview",
        headers=_AUTH_HEADERS,
        params={"token": minted["token"], "actor_user_id": invitee_id},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "expired"


@_db_skip
@pytest.mark.asyncio
async def test_preview_invite_invalid_token(
    api_client: httpx.AsyncClient, monkeypatch
):
    """A token signed by a different key is rejected with invite_invalid."""
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    invitee_id = await _create_user(api_client, "pi-invitee@test.local")
    bogus = mint_invite_token(invite_id="not-real", ttl_seconds=60, signing_key="WRONG")

    r = await api_client.get(
        "/api/internal/invites/preview",
        headers=_AUTH_HEADERS,
        params={"token": bogus, "actor_user_id": invitee_id},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invite_invalid"


@_db_skip
@pytest.mark.asyncio
async def test_preview_invite_workspace_gone(
    api_client: httpx.AsyncClient, monkeypatch
):
    """Soft-deleting the workspace after mint → status=workspace_gone.

    The token is still cryptographically valid and the invite row still exists;
    the preview surfaces the deletion state rather than a generic error so the
    accept page can render a meaningful message.
    """
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "pwg-owner@test.local")
    invitee_id = await _create_user(api_client, "pwg-invitee@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "PreviewWorkspaceGone")
    minted = await _mint_invite(
        api_client, ws_id=ws_id, inviter_id=owner_id, email="pwg-invitee@test.local"
    )

    pool = api_client._transport.app.state.pool  # type: ignore[attr-defined]
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE workspaces SET deleted_at = now() WHERE id = $1", ws_id
        )

    r = await api_client.get(
        "/api/internal/invites/preview",
        headers=_AUTH_HEADERS,
        params={"token": minted["token"], "actor_user_id": invitee_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "workspace_gone"
    assert body["workspace_id"] == ws_id
    assert body["workspace_name"] == "PreviewWorkspaceGone"


@_db_skip
@pytest.mark.asyncio
async def test_preview_invite_unknown_actor(
    api_client: httpx.AsyncClient, monkeypatch
):
    """An actor_user_id with no matching users row → status=email_mismatch.

    This covers stale sessions (user deleted after JWT was minted): the
    preview treats a missing actor the same as a wrong-email actor so the
    page prompts the viewer to sign in with the invited address.
    """
    monkeypatch.setenv(INTERNAL_AUTH_KEY_ENV, _KEY)
    owner_id = await _create_user(api_client, "pua-owner@test.local")
    ws_id = await _create_workspace(api_client, owner_id, "PreviewUnknownActor")
    minted = await _mint_invite(
        api_client, ws_id=ws_id, inviter_id=owner_id, email="pua-invitee@test.local"
    )

    r = await api_client.get(
        "/api/internal/invites/preview",
        headers=_AUTH_HEADERS,
        params={"token": minted["token"], "actor_user_id": "does-not-exist"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "email_mismatch"
