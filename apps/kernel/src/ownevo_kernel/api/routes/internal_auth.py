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

router = APIRouter(prefix="/api/internal/auth", tags=["internal-auth"])


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
            detail=f"{INTERNAL_AUTH_KEY_ENV} not set; internal auth unavailable",
        )
    if not verify_internal_service_token(bearer_token(request), key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal service token",
        )


async def _upsert_user(conn: asyncpg.Connection, body: AuthSyncRequest) -> str:
    """Resolve the internal user id for a provider identity, creating as needed.

    Precedence: an existing ``(provider, provider_sub)`` identity wins; failing
    that, an existing user with the same email is linked (so the seeded dev user
    and a later Google login under ``dev@ownevo.local`` collapse to one person);
    otherwise a fresh user is created. Returns the internal user id.
    """
    row = await conn.fetchrow(
        "SELECT user_id FROM user_identities WHERE provider = $1 AND provider_sub = $2",
        body.provider,
        body.provider_sub,
    )
    if row is not None:
        user_id = row["user_id"]
        # Keep the display name fresh; leave email alone to avoid colliding with
        # the UNIQUE(email) constraint if the provider reports a changed address.
        if body.display_name:
            await conn.execute(
                "UPDATE users SET display_name = $2 WHERE id = $1",
                user_id,
                body.display_name,
            )
        return user_id

    existing = await conn.fetchrow("SELECT id FROM users WHERE email = $1", body.email)
    if existing is not None:
        user_id = existing["id"]
    else:
        user_id = f"usr_{secrets.token_urlsafe(16)}"
        await conn.execute(
            "INSERT INTO users (id, email, display_name) VALUES ($1, $2, $3)",
            user_id,
            body.email,
            body.display_name,
        )
    await conn.execute(
        "INSERT INTO user_identities (user_id, provider, provider_sub) "
        "VALUES ($1, $2, $3) ON CONFLICT (provider, provider_sub) DO NOTHING",
        user_id,
        body.provider,
        body.provider_sub,
    )
    return user_id


@router.post("/sync", response_model=AuthSyncResponse)
async def sync_principal(
    body: AuthSyncRequest, request: Request, pool: PoolDep
) -> AuthSyncResponse:
    """Upsert the authenticated principal and return its live memberships."""
    _require_service_token(request)
    async with pool.acquire() as conn:
        async with conn.transaction():
            user_id = await _upsert_user(conn, body)
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
        email=body.email,
        workspaces=[
            WorkspaceMembership(id=r["id"], name=r["name"], role=r["role"])
            for r in memberships
        ],
    )
