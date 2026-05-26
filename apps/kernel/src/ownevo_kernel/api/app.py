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

from ..clustering.auto_trigger import DEFAULT_DEBOUNCE_SECONDS, ClusterAutoTrigger
from ..db import ENV_VAR
from ..llm.router import check_provider_api_keys
from .deps import is_demo_mode
from .models import HealthResponse
from .routes import (
    agents,
    audit,
    demo,
    design_agent,
    design_agent_ambiguity,
    design_agent_import,
    integrations,
    mcp,
    mcp_oauth,
    models,
    nl_gen,
    otel_ingest,
    proposals,
    skills,
    traces,
    triggers,
    uploads,
    workflows,
)

logger = logging.getLogger(__name__)

# Opt-in: auto-clustering loads sentence-transformers and calls the Anthropic
# labeler (cost + ANTHROPIC_API_KEY), so it stays off unless explicitly enabled
# rather than changing `make api` behaviour for everyone. The on-demand
# `POST /api/workflows/{id}/cluster-production-failures` endpoint works either way.
_AUTOTRIGGER_ENV = "OWNEVO_CLUSTER_AUTOTRIGGER"
_AUTOTRIGGER_DEBOUNCE_ENV = "OWNEVO_CLUSTER_AUTOTRIGGER_DEBOUNCE_SECONDS"

# Opt-in: trigger scheduler runs cron/threshold/Slack/email/calendar triggers.
# Disabled by default; enable with OWNEVO_TRIGGER_SCHEDULER=true.
_TRIGGER_SCHEDULER_ENV = "OWNEVO_TRIGGER_SCHEDULER"


def _trigger_scheduler_enabled() -> bool:
    return os.environ.get(_TRIGGER_SCHEDULER_ENV, "").strip().lower() in ("1", "true", "yes")


def _autotrigger_enabled() -> bool:
    return os.environ.get(_AUTOTRIGGER_ENV, "").strip().lower() in ("1", "true", "yes")


def _autotrigger_debounce_seconds() -> float:
    raw = os.environ.get(_AUTOTRIGGER_DEBOUNCE_ENV)
    if raw is None:
        return DEFAULT_DEBOUNCE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if value <= 0:
        logger.warning(
            "%s=%r is not a positive number; using default %.0fs",
            _AUTOTRIGGER_DEBOUNCE_ENV,
            raw,
            DEFAULT_DEBOUNCE_SECONDS,
        )
        return DEFAULT_DEBOUNCE_SECONDS
    return value


async def _maybe_start_auto_trigger(pool: asyncpg.Pool) -> ClusterAutoTrigger | None:
    """Start the debounced cluster auto-trigger when enabled and not in demo mode.

    Returns the running trigger (so the lifespan can stop it on shutdown),
    or None when disabled — in which case the ingest route's signal call
    is a no-op.
    """
    if not _autotrigger_enabled():
        return None
    if is_demo_mode():
        logger.info("cluster auto-trigger: not started (DEMO_MODE)")
        return None
    debounce = _autotrigger_debounce_seconds()
    trigger = ClusterAutoTrigger(pool, debounce_seconds=debounce)
    await trigger.start()
    logger.info("cluster auto-trigger: enabled (debounce %.0fs)", debounce)
    return trigger


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
        for warning in check_provider_api_keys():
            logger.warning("llm-router: %s", warning)

        own_pool = pool is None
        if own_pool:
            db_url = os.environ.get(ENV_VAR)
            if not db_url:
                raise RuntimeError(
                    f"{ENV_VAR} is not set. Either export it or call "
                    "create_app(pool=...) with a pre-built asyncpg pool.",
                )
            # min_size=1 keeps a warm connection so the first request doesn't
            # pay the handshake cost. max_size=10 fits a single Next.js dev server.
            app.state.pool = await asyncpg.create_pool(
                db_url, min_size=1, max_size=10,
            )
        else:
            # Caller-managed pool — don't open or close it here.
            app.state.pool = pool

        app.state.cluster_auto_trigger = await _maybe_start_auto_trigger(app.state.pool)

        # Opt-in trigger scheduler (cron / threshold / Slack / email / calendar).
        app.state.trigger_scheduler = None
        if _trigger_scheduler_enabled() and not is_demo_mode():
            from ..triggers.scheduler import TriggerScheduler
            scheduler = TriggerScheduler(app.state.pool)
            await scheduler.start()
            app.state.trigger_scheduler = scheduler
            logger.info("trigger scheduler: enabled")

        try:
            yield
        finally:
            if app.state.cluster_auto_trigger is not None:
                await app.state.cluster_auto_trigger.stop()
            if app.state.trigger_scheduler is not None:
                await app.state.trigger_scheduler.stop()
            if own_pool:
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

    if cors_origins is not None:
        origins = cors_origins
    elif env_origins := os.environ.get("OWNEVO_CORS_ORIGINS", ""):
        origins = [o.strip() for o in env_origins.split(",") if o.strip()]
    else:
        origins = ["http://localhost:3000"]
    if origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["*"],
        )

    api.include_router(proposals.router)
    api.include_router(nl_gen.router)
    api.include_router(design_agent.router)
    api.include_router(design_agent_ambiguity.router)
    api.include_router(design_agent_import.router)
    api.include_router(workflows.router)
    api.include_router(models.router)
    api.include_router(audit.router)
    api.include_router(traces.workflow_traces_router)
    api.include_router(traces.trace_router)
    api.include_router(skills.skill_router)
    api.include_router(skills.workflow_skills_router)
    api.include_router(demo.router)
    api.include_router(otel_ingest.router)
    api.include_router(integrations.router)
    api.include_router(agents.router)
    api.include_router(mcp.router)
    api.include_router(mcp_oauth.router)
    api.include_router(uploads.router)
    api.include_router(triggers.router)

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
