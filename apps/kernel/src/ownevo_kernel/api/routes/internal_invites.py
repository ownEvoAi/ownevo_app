"""Internal workspace-invite endpoints.

Four operations make up the invite lifecycle. Each is authenticated by the
shared ``OWNEVO_INTERNAL_AUTH_KEY`` bearer token — the same credential the web
edge already presents for auth-sync and workspace provisioning. Authorization
(is this caller allowed to invite to this workspace? to revoke this invite?
to redeem on behalf of this user?) is layered on top by passing the acting
user id in the request, in line with the rest of the ``/api/internal/*``
surface.

  * ``POST /api/internal/workspaces/{workspace_id}/invites`` — mint
        Caller must be an owner or admin of the workspace. Inserts a row in
        ``workspace_invites``, mints a signed token addressing that row, and
        returns the token + accept URL. The system does not deliver the URL;
        the inviter sends it through their own channel.

  * ``POST /api/internal/invites/redeem`` — redeem
        Verifies token signature + expiry, looks up the row, checks
        revocation / prior-redemption / row expiry, and inserts the redeemer
        into ``workspace_members``. Idempotent for the original redeemer;
        rejects a different user attempting to consume an already-redeemed
        invite.

  * ``POST /api/internal/invites/{invite_id}/revoke`` — revoke
        Caller must be an owner or admin of the invite's workspace. Sets
        ``revoked_at``; a revoked invite can never be redeemed even if its
        token is still cryptographically valid.

  * ``GET /api/internal/workspaces/{workspace_id}/invites`` — list pending
        Caller must be an owner or admin. Returns invites that are neither
        redeemed, revoked, nor expired — the rows an admin page renders as
        "outstanding invites with a Revoke button next to them".

  * ``GET /api/internal/invites/preview`` — describe a token without consuming it
        Verifies the token signature and returns the addressed workspace +
        inviter + invite metadata + a viewer-relative status. Lets the accept
        page render workspace name / inviter / expiry up front and branch
        before the user clicks (expired, revoked, wrong-account, etc.) instead
        of surfacing the failure only on click. The token itself is the
        authorization — anyone holding it can preview, the same way they
        could redeem if their email matched.

``workspace_invites`` sits outside row-level security (see migration 0036) so
all endpoints use a plain pooled connection with no workspace GUC.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from .._internal_auth import INTERNAL_AUTH_KEY_ENV, require_service_token
from .._workspace_invites import (
    InviteTokenInvalid,
    mint_invite_token,
    verify_invite_token,
)
from ..deps import PoolDep

router = APIRouter(tags=["internal-invites"], include_in_schema=False)

# TTL bounds. A workspace invite is meant to be used in days, not minutes
# (admins may take a while to send the URL) and not months (stale invites
# become a security liability if an inviter leaves).
_MIN_TTL_DAYS = 1
_MAX_TTL_DAYS = 30
_DEFAULT_TTL_DAYS = 7

# Roles available to invite. 'owner' is reserved for the workspace creator;
# transferring ownership is a separate, deliberate operation.
_INVITE_ROLES = ("admin", "member")


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------


async def _require_workspace_admin(conn, *, workspace_id: str, user_id: str) -> None:
    """Refuse the request unless ``user_id`` is an owner or admin of the workspace.

    Reads ``workspace_members`` directly (non-RLS table). Raises 403 on
    insufficient role and 422 if the workspace does not exist or is
    soft-deleted, so a caller cannot probe for workspace existence by getting
    a 403.
    """
    workspace_live = await conn.fetchval(
        "SELECT 1 FROM workspaces WHERE id = $1 AND deleted_at IS NULL",
        workspace_id,
    )
    if not workspace_live:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="workspace not found",
        )
    role = await conn.fetchval(
        "SELECT role FROM workspace_members WHERE workspace_id = $1 AND user_id = $2",
        workspace_id,
        user_id,
    )
    if role not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="caller is not an admin of this workspace",
        )


def _require_signing_key() -> str:
    key = os.environ.get(INTERNAL_AUTH_KEY_ENV)
    if not key:
        # The same env-var gates the service-token check, so this is mostly
        # unreachable on a real deployment; covered for explicitness.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal signing key is not configured",
        )
    return key


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------


class CreateInviteRequest(BaseModel):
    """Mint-invite request. Email validation is a single-pass syntactic check
    (`local@host`); we do not RFC-validate because deliverability gates would
    add a heavy dep and we never deliver mail ourselves."""

    model_config = ConfigDict(extra="forbid")

    inviter_user_id: str = Field(min_length=1, max_length=255)
    invited_email: str = Field(min_length=3, max_length=320)
    role: str = Field()
    ttl_days: int = Field(default=_DEFAULT_TTL_DAYS, ge=_MIN_TTL_DAYS, le=_MAX_TTL_DAYS)


class CreateInviteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invite_id: str
    token: str
    expires_at: str  # ISO-8601 UTC


@router.post(
    "/api/internal/workspaces/{workspace_id}/invites",
    response_model=CreateInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite(
    workspace_id: str,
    body: CreateInviteRequest,
    request: Request,
    pool: PoolDep,
) -> CreateInviteResponse:
    """Mint a workspace invite. Caller (inviter) must be admin/owner."""
    require_service_token(request)
    if body.role not in _INVITE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"role must be one of {_INVITE_ROLES}; got {body.role!r}",
        )
    if "@" not in body.invited_email or body.invited_email.startswith("@") or body.invited_email.endswith("@"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invited_email must contain a local-part and a host",
        )
    signing_key = _require_signing_key()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=body.ttl_days)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _require_workspace_admin(
                conn, workspace_id=workspace_id, user_id=body.inviter_user_id
            )
            row = await conn.fetchrow(
                "INSERT INTO workspace_invites "
                "(workspace_id, invited_email, role, invited_by, expires_at) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                workspace_id,
                body.invited_email.strip().lower(),
                body.role,
                body.inviter_user_id,
                expires_at,
            )
    invite_id = str(row["id"])
    token = mint_invite_token(
        invite_id=invite_id,
        ttl_seconds=int(timedelta(days=body.ttl_days).total_seconds()),
        signing_key=signing_key,
    )
    return CreateInviteResponse(
        invite_id=invite_id,
        token=token,
        expires_at=expires_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Redeem
# ---------------------------------------------------------------------------


class RedeemInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=2048)
    redeemer_user_id: str = Field(min_length=1, max_length=255)


class RedeemInviteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    workspace_name: str
    role: str


def _invite_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": code, "message": message},
    )


@router.post("/api/internal/invites/redeem", response_model=RedeemInviteResponse)
async def redeem_invite(
    body: RedeemInviteRequest, request: Request, pool: PoolDep
) -> RedeemInviteResponse:
    """Add the redeemer to the workspace addressed by a valid invite token.

    Idempotent for the original redeemer: a repeat call returns the same
    success payload without erroring. A second user attempting to redeem an
    already-redeemed invite is rejected.
    """
    require_service_token(request)
    signing_key = _require_signing_key()
    try:
        invite_id = verify_invite_token(body.token, signing_key)
    except InviteTokenInvalid as exc:
        raise _invite_error("invite_invalid", str(exc)) from exc

    async with pool.acquire() as conn:
        async with conn.transaction():
            redeemer_email = await conn.fetchval(
                "SELECT email FROM users WHERE id = $1", body.redeemer_user_id
            )
            if redeemer_email is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="redeemer user not found",
                )
            invite = await conn.fetchrow(
                "SELECT id, workspace_id, invited_email, role, expires_at, "
                "       redeemed_at, redeemed_by, revoked_at "
                "FROM workspace_invites WHERE id = $1 FOR UPDATE",
                invite_id,
            )
            if invite is None:
                raise _invite_error("invite_invalid", "invite not found")
            if redeemer_email.lower() != invite["invited_email"]:
                raise _invite_error(
                    "invite_email_mismatch",
                    "this invite was addressed to a different email address",
                )
            if invite["revoked_at"] is not None:
                raise _invite_error("invite_revoked", "invite has been revoked")
            # Row-stored expiry is the source of truth; the token expiry
            # should match but is checked independently in verify_invite_token.
            if invite["expires_at"] <= datetime.now(timezone.utc):
                raise _invite_error("invite_expired", "invite has expired")
            if invite["redeemed_at"] is not None:
                # Idempotent for the same user; rejected for anyone else so an
                # already-consumed invite cannot be re-used by a different
                # account that happens to have the URL.
                if invite["redeemed_by"] != body.redeemer_user_id:
                    raise _invite_error(
                        "invite_already_redeemed",
                        "invite has already been redeemed",
                    )
            else:
                # First redemption: insert membership and mark the invite
                # consumed. ON CONFLICT handles the edge case where the
                # redeemer was added to the workspace by some other path
                # between mint and redeem.
                await conn.execute(
                    "INSERT INTO workspace_members (workspace_id, user_id, role) "
                    "VALUES ($1, $2, $3) "
                    "ON CONFLICT (workspace_id, user_id) DO NOTHING",
                    invite["workspace_id"],
                    body.redeemer_user_id,
                    invite["role"],
                )
                await conn.execute(
                    "UPDATE workspace_invites "
                    "SET redeemed_at = now(), redeemed_by = $2 "
                    "WHERE id = $1",
                    invite_id,
                    body.redeemer_user_id,
                )
            actual_role = await conn.fetchval(
                "SELECT role FROM workspace_members "
                "WHERE workspace_id = $1 AND user_id = $2",
                invite["workspace_id"],
                body.redeemer_user_id,
            )
            workspace = await conn.fetchrow(
                "SELECT name FROM workspaces WHERE id = $1 AND deleted_at IS NULL",
                invite["workspace_id"],
            )
            if workspace is None:
                # Workspace was soft-deleted between mint and redeem. Treat
                # as invalid rather than letting the redeemer join a tombstone.
                raise _invite_error("invite_invalid", "workspace no longer exists")
    return RedeemInviteResponse(
        workspace_id=invite["workspace_id"],
        workspace_name=workspace["name"],
        role=actual_role or invite["role"],
    )


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


class RevokeInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_user_id: str = Field(min_length=1, max_length=255)


@router.post(
    "/api/internal/invites/{invite_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invite(
    invite_id: str, body: RevokeInviteRequest, request: Request, pool: PoolDep
) -> None:
    """Mark an invite as revoked. Caller must be admin/owner of its workspace."""
    require_service_token(request)
    async with pool.acquire() as conn:
        async with conn.transaction():
            invite = await conn.fetchrow(
                "SELECT workspace_id, redeemed_at, revoked_at "
                "FROM workspace_invites WHERE id = $1 FOR UPDATE",
                invite_id,
            )
            if invite is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="invite not found"
                )
            await _require_workspace_admin(
                conn, workspace_id=invite["workspace_id"], user_id=body.actor_user_id
            )
            if invite["redeemed_at"] is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="invite has already been redeemed",
                )
            if invite["revoked_at"] is not None:
                # Idempotent: revoking an already-revoked invite is a no-op.
                return
            await conn.execute(
                "UPDATE workspace_invites "
                "SET revoked_at = now(), revoked_by = $2 "
                "WHERE id = $1",
                invite_id,
                body.actor_user_id,
            )


# ---------------------------------------------------------------------------
# List pending invites
# ---------------------------------------------------------------------------


class PendingInvite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invite_id: str
    invited_email: str
    role: str
    invited_by_user_id: str
    invited_by_email: str | None
    invited_by_display_name: str | None
    created_at: str  # ISO-8601 UTC
    expires_at: str  # ISO-8601 UTC


class ListInvitesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invites: list[PendingInvite]


@router.get(
    "/api/internal/workspaces/{workspace_id}/invites",
    response_model=ListInvitesResponse,
)
async def list_pending_invites(
    workspace_id: str,
    actor_user_id: str,
    request: Request,
    pool: PoolDep,
) -> ListInvitesResponse:
    """Return invites that are still actionable for the members admin page.

    "Pending" = not redeemed, not revoked, not yet expired. Redeemed invites
    are excluded because the row is also represented in ``workspace_members``;
    revoked and expired invites are excluded because they are not actionable
    (nothing to do but mint a fresh one).
    """
    require_service_token(request)
    async with pool.acquire() as conn:
        await _require_workspace_admin(
            conn, workspace_id=workspace_id, user_id=actor_user_id
        )
        rows = await conn.fetch(
            "SELECT i.id, i.invited_email, i.role, i.invited_by, "
            "       i.created_at, i.expires_at, "
            "       u.email AS inviter_email, u.display_name AS inviter_name "
            "FROM workspace_invites i "
            "LEFT JOIN users u ON u.id = i.invited_by "
            "WHERE i.workspace_id = $1 "
            "  AND i.redeemed_at IS NULL "
            "  AND i.revoked_at IS NULL "
            "  AND i.expires_at > now() "
            "ORDER BY i.created_at DESC "
            # Hard ceiling: the admin page renders every pending invite with a
            # Revoke button; 200 is well above realistic invite volumes.
            "LIMIT 200",
            workspace_id,
        )
    return ListInvitesResponse(
        invites=[
            PendingInvite(
                invite_id=str(r["id"]),
                invited_email=r["invited_email"],
                role=r["role"],
                invited_by_user_id=r["invited_by"],
                invited_by_email=r["inviter_email"],
                invited_by_display_name=r["inviter_name"],
                created_at=r["created_at"].isoformat(),
                expires_at=r["expires_at"].isoformat(),
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


# Viewer-relative status returned by the preview endpoint. The accept page
# branches on this; new values must be reflected in the web client.
_STATUS_PENDING = "pending"
_STATUS_EXPIRED = "expired"
_STATUS_REVOKED = "revoked"
_STATUS_REDEEMED_BY_ME = "redeemed_by_me"
_STATUS_REDEEMED_BY_OTHER = "redeemed_by_other"
_STATUS_EMAIL_MISMATCH = "email_mismatch"
_STATUS_WORKSPACE_GONE = "workspace_gone"


class PreviewInviteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "pending",
        "expired",
        "revoked",
        "redeemed_by_me",
        "redeemed_by_other",
        "email_mismatch",
        "workspace_gone",
    ]
    workspace_id: str
    workspace_name: str | None
    invited_email: str
    role: str
    invited_by_email: str | None
    invited_by_display_name: str | None
    expires_at: str  # ISO-8601 UTC


@router.get("/api/internal/invites/preview", response_model=PreviewInviteResponse)
async def preview_invite(
    token: str,
    actor_user_id: str,
    request: Request,
    pool: PoolDep,
) -> PreviewInviteResponse:
    """Describe an invite token without consuming it.

    The accept page calls this on every render so it can show the workspace
    name, role, and inviter — and so it can surface terminal states (expired,
    revoked, wrong account) before the user clicks Accept. The token is the
    authorization here: anyone holding the URL can already redeem it (subject
    to the email check), so the preview discloses nothing extra.

    ``actor_user_id`` is required because every meaningful status (especially
    ``redeemed_by_me`` vs ``redeemed_by_other`` and ``email_mismatch``) is
    relative to the signed-in viewer. A page rendered for a logged-out user
    has nothing to act on.
    """
    require_service_token(request)
    signing_key = _require_signing_key()
    try:
        # Skip the token-side expiry check: the DB row's expires_at is the
        # authoritative expiry for the preview so the page can surface a
        # structured "expired" status with workspace/inviter metadata rather
        # than a generic "link not valid" error. Signature and format are
        # still validated unconditionally.
        invite_id = verify_invite_token(token, signing_key, check_expiry=False)
    except InviteTokenInvalid as exc:
        raise _invite_error("invite_invalid", str(exc)) from exc

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT i.workspace_id, i.invited_email, i.role, i.invited_by, "
            "       i.expires_at, i.redeemed_at, i.redeemed_by, i.revoked_at, "
            "       w.name AS workspace_name, w.deleted_at AS workspace_deleted_at, "
            "       u.email AS inviter_email, u.display_name AS inviter_name "
            "FROM workspace_invites i "
            "LEFT JOIN workspaces w ON w.id = i.workspace_id "
            "LEFT JOIN users u ON u.id = i.invited_by "
            "WHERE i.id = $1",
            invite_id,
        )
        if row is None:
            raise _invite_error("invite_invalid", "invite not found")

        actor_email = await conn.fetchval(
            "SELECT email FROM users WHERE id = $1", actor_user_id
        )

    if row["workspace_deleted_at"] is not None:
        status_str = _STATUS_WORKSPACE_GONE
    elif row["revoked_at"] is not None:
        status_str = _STATUS_REVOKED
    elif row["redeemed_at"] is not None:
        status_str = (
            _STATUS_REDEEMED_BY_ME
            if row["redeemed_by"] == actor_user_id
            else _STATUS_REDEEMED_BY_OTHER
        )
    elif row["expires_at"] <= datetime.now(timezone.utc):
        status_str = _STATUS_EXPIRED
    elif actor_email is None or actor_email.lower() != row["invited_email"]:
        # Unknown actor (no row in users) or a mismatched email both block
        # redemption — surface as the same UX state so the page can ask the
        # viewer to sign in with the right account.
        status_str = _STATUS_EMAIL_MISMATCH
    else:
        status_str = _STATUS_PENDING

    return PreviewInviteResponse(
        status=status_str,
        workspace_id=row["workspace_id"],
        workspace_name=row["workspace_name"],
        invited_email=row["invited_email"],
        role=row["role"],
        invited_by_email=row["inviter_email"],
        invited_by_display_name=row["inviter_name"],
        expires_at=row["expires_at"].isoformat(),
    )
