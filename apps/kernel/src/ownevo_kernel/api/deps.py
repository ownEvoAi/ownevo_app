"""FastAPI dependency-injection for the asyncpg pool.

The pool is owned by the FastAPI lifespan (`app.state.pool`); each
request acquires a connection via the `get_conn` dependency, which
yields it for the request's lifetime and releases it back to the pool
on completion.

Tests inject a custom pool via `app.dependency_overrides[get_pool]`
or by attaching their own `app.state.pool`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from ..tenant_session import DEFAULT_WORKSPACE_ID, set_workspace


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


def get_workspace_id() -> str:
    """Resolve the workspace the current request operates in.

    Single-tenant today: every request resolves to the ``default`` workspace.
    When the auth layer lands this derives the workspace from the authenticated
    principal. Routes that spawn background work depend on this directly so the
    same workspace id flows into ``acquire_workspace_conn`` off-request.
    """
    return DEFAULT_WORKSPACE_ID


WorkspaceIdDep = Annotated[str, Depends(get_workspace_id)]


async def get_conn(
    pool: PoolDep, workspace_id: WorkspaceIdDep
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Acquire one connection from the pool for the request's duration.

    Each connection is bound to the request's workspace before it is handed to
    the route, so workspace-scoped row-level security scopes every query.
    """
    async with pool.acquire() as conn:
        await set_workspace(conn, workspace_id)
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
