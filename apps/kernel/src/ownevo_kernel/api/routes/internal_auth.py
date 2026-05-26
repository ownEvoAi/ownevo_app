"""Internal web→kernel auth-sync endpoint.

The web app (Auth.js) calls this immediately after it authenticates a user,
before any workspace is bound. It upserts the user and the provider identity
into the global (non-RLS) auth tables and returns the live workspaces the user
belongs to, so the web app can mint a workspace assertion for subsequent
calls.

Authenticated by the shared ``OWNEVO_INTERNAL_AUTH_KEY`` presented as a bearer
token — a service-to-service secret, distinct from the per-request workspace
assertion. A workspace assertion cannot be used here: no workspace is bound
yet, and a brand-new user has no membership to assert. The endpoint is refused
when the key is unset (deployment misconfiguration → 503) or the presented
token does not match (401).

The ``users`` / ``user_identities`` / ``workspace_members`` tables sit outside
row-level security, so a plain pooled connection with no workspace GUC reads
and writes them directly.
"""

from __future__ import annotations

import os
import secrets

import asyncpg
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from .._internal_auth import (
    INTERNAL_AUTH_KEY_ENV,
    bearer_token,
    verify_internal_service_token,
)
from ..deps import PoolDep

router = APIRouter(
    prefix="/api/internal/auth",
    tags=["internal-auth"],
    include_in_schema=False,  # internal service endpoint; not part of the public API
)


class AuthSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    provider_sub: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=1, max_length=320)
    display_name: str | None = Field(default=None, max_length=255)


class WorkspaceMembership(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: str


class AuthSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    workspaces: list[WorkspaceMembership]


def _require_service_token(request: Request) -> None:
    key = os.environ.get(INTERNAL_AUTH_KEY_ENV)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal auth endpoint is not configured",
        )
    if not verify_internal_service_token(bearer_token(request), key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal service token",
        )


async def _upsert_user(conn: asyncpg.Connection, body: AuthSyncRequest) -> tuple[str, str]:
    """Resolve the internal user id for a provider identity, creating as needed.

    Precedence: an existing ``(provider, provider_sub)`` identity wins; failing
    that, an existing user with the same email is linked (so the seeded dev user
    and a later Google login under ``dev@ownevo.local`` collapse to one person);
    otherwise a fresh user is created.

    The email is normalised to lowercase before any DB read or write so that
    providers which differ only in case (e.g. ``User@Gmail.com`` vs
    ``user@gmail.com``) resolve to the same user row rather than creating
    duplicates.

    Returns ``(user_id, db_email)`` where ``db_email`` is the canonical address
    stored in the ``users`` table (not the caller-supplied value, which may
    differ if the provider reported a changed address on a re-login).
    """
    email = body.email.lower()

    row = await conn.fetchrow(
        "SELECT ui.user_id, u.email "
        "FROM user_identities ui "
        "JOIN users u ON u.id = ui.user_id "
        "WHERE ui.provider = $1 AND ui.provider_sub = $2",
        body.provider,
        body.provider_sub,
    )
    if row is not None:
        user_id = row["user_id"]
        db_email = row["email"]
        # Keep the display name fresh; leave email alone to avoid colliding with
        # the UNIQUE(email) constraint if the provider reports a changed address.
        if body.display_name:
            await conn.execute(
                "UPDATE users SET display_name = $2 WHERE id = $1",
                user_id,
                body.display_name,
            )
        return user_id, db_email

    existing = await conn.fetchrow("SELECT id, email FROM users WHERE email = $1", email)
    if existing is not None:
        user_id = existing["id"]
        db_email = existing["email"]
        # Keep the display name fresh for this path too (same as the existing-identity branch).
        if body.display_name:
            await conn.execute(
                "UPDATE users SET display_name = $2 WHERE id = $1",
                user_id,
                body.display_name,
            )
    else:
        # ON CONFLICT handles the narrow race where two concurrent first sign-ins
        # for the same brand-new email both see no existing row and both attempt
        # to INSERT. The loser's transaction hits the UNIQUE(email) constraint;
        # DO UPDATE returns the winner's id so both callers resolve to the same
        # user without a 500 error.
        row = await conn.fetchrow(
            "INSERT INTO users (id, email, display_name) VALUES ($1, $2, $3) "
            "ON CONFLICT (email) DO UPDATE "
            "SET display_name = COALESCE(EXCLUDED.display_name, users.display_name) "
            "RETURNING id, email",
            f"usr_{secrets.token_urlsafe(16)}",
            email,
            body.display_name,
        )
        user_id = row["id"]
        db_email = row["email"]
    await conn.execute(
        "INSERT INTO user_identities (user_id, provider, provider_sub) "
        "VALUES ($1, $2, $3) ON CONFLICT (provider, provider_sub) DO NOTHING",
        user_id,
        body.provider,
        body.provider_sub,
    )
    return user_id, db_email


@router.post("/sync", response_model=AuthSyncResponse)
async def sync_principal(
    body: AuthSyncRequest, request: Request, pool: PoolDep
) -> AuthSyncResponse:
    """Upsert the authenticated principal and return its live memberships."""
    _require_service_token(request)
    async with pool.acquire() as conn:
        async with conn.transaction():
            user_id, db_email = await _upsert_user(conn, body)
        memberships = await conn.fetch(
            "SELECT w.id, w.name, wm.role "
            "FROM workspace_members wm "
            "JOIN workspaces w ON w.id = wm.workspace_id "
            "WHERE wm.user_id = $1 AND w.deleted_at IS NULL "
            "ORDER BY w.name",
            user_id,
        )
    return AuthSyncResponse(
        user_id=user_id,
        email=db_email,  # DB-canonical address, not the caller-supplied value
        workspaces=[
            WorkspaceMembership(id=r["id"], name=r["name"], role=r["role"])
            for r in memberships
        ],
    )
