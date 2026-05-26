"""FastAPI dependency-injection for the asyncpg pool.

The pool is owned by the FastAPI lifespan (`app.state.pool`); each
request acquires a connection via the `get_conn` dependency, which
yields it for the request's lifetime and releases it back to the pool
on completion.

Tests inject a custom pool via `app.dependency_overrides[get_pool]`
or by attaching their own `app.state.pool`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from ..tenant_session import WorkspaceBindError, WorkspaceMembershipError, set_workspace
from ._internal_auth import (
    INTERNAL_AUTH_KEY_ENV,
    AssertionInvalid,
    Principal,
    bearer_token,
    dev_auth_enabled,
    dev_principal,
    verify_workspace_assertion,
)

_log = logging.getLogger(__name__)


async def get_pool(request: Request) -> asyncpg.Pool:
    """Return the asyncpg pool stored on `app.state.pool`.

    Raises RuntimeError if the lifespan didn't initialize one — that's a
    deployment misconfiguration, not a request error.
    """
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "asyncpg pool not initialized. Check the FastAPI lifespan or, in "
            "tests, attach a pool to app.state before issuing requests.",
        )
    return pool


PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]


def get_principal(request: Request) -> Principal:
    """Resolve the authenticated principal (user + active workspace).

    A signed identity assertion in the ``Authorization: Bearer`` header is the
    authoritative source: it is minted by the web app, which authenticates the
    user and only signs a workspace the user belongs to. The kernel verifies
    the signature + expiry and trusts the claimed ``(user_id, workspace_id)``.

    With no assertion present, behaviour depends on dev-auth: when
    ``OWNEVO_DEV_AUTH=true`` the request resolves to the seeded dev principal
    (local/test convenience); otherwise the request is rejected. This fails
    closed — a production deployment that never sets the flag rejects
    unauthenticated requests rather than serving the default workspace.

    Only signature + expiry are checked here (no database access). Membership
    is enforced in ``get_conn``, the single path to workspace-scoped data, so a
    valid-but-unauthorized assertion cannot read another tenant's rows.
    """
    token = bearer_token(request)
    if token is None:
        if dev_auth_enabled():
            return dev_principal()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing identity assertion",
        )
    key = os.environ.get(INTERNAL_AUTH_KEY_ENV)
    if not key:
        # A bearer token was presented but the kernel has no key to verify it.
        # This is a deployment misconfiguration; reject rather than trust it.
        _log.error("%s not set; cannot verify identity assertion", INTERNAL_AUTH_KEY_ENV)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="identity assertion cannot be verified",
        )
    try:
        return verify_workspace_assertion(token, key)
    except AssertionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid identity assertion",
        ) from exc


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def get_workspace_id(principal: PrincipalDep) -> str:
    """The workspace the current request operates in, from the principal.

    Routes that spawn background work depend on this directly so the same
    workspace id flows into ``acquire_workspace_conn`` off-request.
    """
    return principal.workspace_id


WorkspaceIdDep = Annotated[str, Depends(get_workspace_id)]


async def get_conn(
    pool: PoolDep, principal: PrincipalDep
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Acquire one connection bound to the principal's workspace.

    Before binding, confirm the principal is a member of a live workspace —
    ``workspace_members`` is a global (non-RLS) table, readable on the
    still-unbound connection. A non-member, or a soft-deleted workspace, is
    rejected with 403. Once bound via ``set_workspace``, row-level security
    scopes every subsequent query to that workspace. This is the only path to
    workspace-scoped data, so the membership gate cannot be bypassed.
    """
    async with pool.acquire() as conn:
        member = await conn.fetchval(
            "SELECT 1 FROM workspace_members wm "
            "JOIN workspaces w ON w.id = wm.workspace_id "
            "WHERE wm.user_id = $1 AND wm.workspace_id = $2 "
            "AND w.deleted_at IS NULL",
            principal.user_id,
            principal.workspace_id,
        )
        if member is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not a member of this workspace",
            )
        try:
            await set_workspace(conn, principal.workspace_id)
        except (WorkspaceBindError, WorkspaceMembershipError) as exc:
            # Workspace was soft-deleted between the membership SELECT and here
            # (narrow race). Return 403 consistent with the member-check above.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="workspace unavailable",
            ) from exc
        yield conn


ConnDep = Annotated[asyncpg.Connection, Depends(get_conn)]


def is_demo_mode() -> bool:
    """True when this kernel is serving the public demo (``DEMO_MODE=true``)."""
    return os.environ.get("DEMO_MODE", "").lower() == "true"


def require_not_demo_mode() -> None:
    """Raise 503 when DEMO_MODE=true — blocks write ops on the live demo."""
    if is_demo_mode():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "This action is disabled in the live demo. "
                "Clone the repo and run locally: "
                "https://github.com/ownEvoAi/ownevo_app"
            ),
        )


DemoModeCheck = Annotated[None, Depends(require_not_demo_mode)]
