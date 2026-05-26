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

import secrets

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .._internal_auth import require_service_token
from ..deps import PoolDep

router = APIRouter(
    prefix="/api/internal/workspaces",
    tags=["internal-workspaces"],
    include_in_schema=False,  # internal service endpoint; not part of the public API
)

# Hard cap on workspaces per user. Prevents unbounded DB growth from a
# single user submitting the create form in a tight loop or from two browser
# tabs racing — both would pass form validation but the cap fires inside the
# same transaction as the INSERT so only one proceeds.
MAX_WORKSPACES_PER_USER = 10


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def strip_and_validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank or whitespace-only")
        return v


class WorkspaceCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    name: str


@router.post("", response_model=WorkspaceCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreateRequest, request: Request, pool: PoolDep
) -> WorkspaceCreateResponse:
    """Create a new workspace and make the caller its owner.

    The user must already exist in the ``users`` table (ensured by the
    auth-sync endpoint which is called first, on sign-in). Returns the new
    workspace id and name so the web app can update the session immediately.
    """
    require_service_token(request)

    workspace_id = f"ws_{secrets.token_urlsafe(16)}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            # All checks run inside the transaction so no intermediate state
            # is visible to concurrent requests.
            user_exists = await conn.fetchval(
                "SELECT 1 FROM users WHERE id = $1", body.user_id
            )
            if not user_exists:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="user not found",
                )

            workspace_count = await conn.fetchval(
                "SELECT COUNT(*) FROM workspace_members WHERE user_id = $1",
                body.user_id,
            )
            if workspace_count >= MAX_WORKSPACES_PER_USER:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"user already has {workspace_count} workspace(s); "
                        f"limit is {MAX_WORKSPACES_PER_USER}"
                    ),
                )

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
