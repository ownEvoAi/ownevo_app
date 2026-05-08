"""FastAPI app factory for the W2.5 approval REST surface.

Two entry points:

  * `app` — module-level instance for `uvicorn ownevo_kernel.api.app:app`
  * `create_app(...)` — factory for tests; lets a custom asyncpg pool be
    attached without going through env-var lifespan.

Lifespan: the default `app` reads `OWNEVO_DATABASE_URL` and creates a
connection pool on startup; tests bypass the lifespan by using
`create_app(pool=...)` to pre-attach a pool.

CORS: the dev origin (`http://localhost:3000`) is allowed by default so
the Next.js dev server can hit the API without a proxy. Production
callers should override `cors_origins`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..db import ENV_VAR
from .models import HealthResponse
from .routes import nl_gen, proposals

logger = logging.getLogger(__name__)


def create_app(
    *,
    pool: asyncpg.Pool | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build a FastAPI app.

    Args:
        pool: pre-built asyncpg pool. When provided, the lifespan no
            longer reads the env var — used by tests and by hosts that
            manage the pool externally.
        cors_origins: allowed origins. Defaults to `http://localhost:3000`
            (the Next.js dev server). Pass an empty list to disable CORS.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        if pool is not None:
            # Caller-managed pool — don't open or close it here.
            app.state.pool = pool
            yield
            return

        db_url = os.environ.get(ENV_VAR)
        if not db_url:
            raise RuntimeError(
                f"{ENV_VAR} is not set. Either export it or call "
                "create_app(pool=...) with a pre-built asyncpg pool.",
            )
        # min_size=1 keeps a warm connection so the first request doesn't pay
        # the handshake cost. max_size=10 fits a single Next.js dev server.
        app.state.pool = await asyncpg.create_pool(
            db_url, min_size=1, max_size=10,
        )
        try:
            yield
        finally:
            await app.state.pool.close()

    api = FastAPI(
        title="ownEvo approval API",
        version="0.1.0",
        description=(
            "Approval queue surface for the W2.5 UI scaffold. "
            "REST seam between Python kernel and TS web app."
        ),
        lifespan=lifespan,
    )

    origins = cors_origins if cors_origins is not None else ["http://localhost:3000"]
    if origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    api.include_router(proposals.router)
    api.include_router(nl_gen.router)

    @api.get("/api/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        """Liveness + DB-roundtrip check.

        `db='ok'` confirms the pool answered a `SELECT 1`. A non-ok value
        carries the asyncpg error class so monitoring can distinguish
        pool exhaustion from network failure.
        """
        try:
            async with api.state.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_status = "ok"
        except Exception as exc:  # noqa: BLE001 — health check shouldn't propagate
            db_status = type(exc).__name__
        return HealthResponse(status="ok", db=db_status)

    return api


# Module-level instance for `uvicorn ownevo_kernel.api.app:app`.
app = create_app()
