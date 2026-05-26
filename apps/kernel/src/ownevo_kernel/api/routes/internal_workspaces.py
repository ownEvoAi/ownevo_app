"""Internal workspace provisioning endpoint.

The web app calls this when a newly authenticated user creates their first
workspace (or any subsequent workspace). Authentication is by the shared
``OWNEVO_INTERNAL_AUTH_KEY`` service token — the same credential used by the
auth-sync endpoint.

``workspaces`` and ``workspace_members`` are global tables that sit outside
row-level security, so a plain pooled connection with no workspace GUC can
read and write them directly.
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
    prefix="/api/internal/workspaces",
    tags=["internal-workspaces"],
    include_in_schema=False,  # internal service endpoint; not part of the public API
)


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=80)


class WorkspaceCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    name: str


def _require_service_token(request: Request) -> None:
    key = os.environ.get(INTERNAL_AUTH_KEY_ENV)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal workspace endpoint is not configured",
        )
    if not verify_internal_service_token(bearer_token(request), key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal service token",
        )


@router.post("", response_model=WorkspaceCreateResponse)
async def create_workspace(
    body: WorkspaceCreateRequest, request: Request, pool: PoolDep
) -> WorkspaceCreateResponse:
    """Create a new workspace and make the caller its owner.

    The user must already exist in the ``users`` table (ensured by the
    auth-sync endpoint which is called first, on sign-in). Returns the new
    workspace id and name so the web app can update the session immediately.
    """
    _require_service_token(request)

    workspace_id = f"ws_{secrets.token_urlsafe(16)}"

    async with pool.acquire() as conn:
        # Verify the user exists before creating the workspace.
        user_exists = await conn.fetchval(
            "SELECT 1 FROM users WHERE id = $1", body.user_id
        )
        if not user_exists:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="user not found",
            )

        async with conn.transaction():
            await conn.execute(
                "INSERT INTO workspaces (id, name) VALUES ($1, $2)",
                workspace_id,
                body.name,
            )
            await conn.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role) "
                "VALUES ($1, $2, 'owner')",
                workspace_id,
                body.user_id,
            )

    return WorkspaceCreateResponse(workspace_id=workspace_id, name=body.name)
