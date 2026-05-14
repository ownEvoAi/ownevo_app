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


async def get_conn(pool: PoolDep) -> AsyncGenerator[asyncpg.Connection, None]:
    """Acquire one connection from the pool for the request's duration."""
    async with pool.acquire() as conn:
        yield conn


ConnDep = Annotated[asyncpg.Connection, Depends(get_conn)]


def require_not_demo_mode() -> None:
    """Raise 503 when DEMO_MODE=true — blocks write ops on the live demo."""
    if os.environ.get("DEMO_MODE", "").lower() == "true":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "demo_mode",
                "message": (
                    "Iteration runs are disabled in the live demo. "
                    "Clone the repo and run locally: "
                    "https://github.com/ownEvoAi/ownevo_app"
                ),
            },
        )


DemoModeCheck = Annotated[None, Depends(require_not_demo_mode)]
