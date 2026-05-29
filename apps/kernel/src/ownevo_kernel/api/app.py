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
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from ..clustering.auto_trigger import DEFAULT_DEBOUNCE_SECONDS, ClusterAutoTrigger
from ..db import ENV_VAR, open_pool
from ..jobs import reap_orphaned_iterations
from ..llm.router import check_provider_api_keys
from ..sandbox.local_docker import _read_max_concurrent
from ..secrets.encrypted_field import MASTER_KEY_ENV
from ._error_handler import install_exception_handler
from ._internal_auth import (
    DEPLOY_ENV_VAR,
    DEV_AUTH_ENV,
    INTERNAL_AUTH_KEY_ENV,
    PRODUCTION_ENV_VALUE,
    dev_auth_enabled,
    is_production,
)
from ._logging import configure_logging
from ._metrics import CONTENT_TYPE as METRICS_CONTENT_TYPE
from ._metrics import render_metrics
from ._request_id import RequestIdMiddleware
from ._sentry import flush_sentry, init_sentry
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
    internal_auth,
    internal_invites,
    internal_workspaces,
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

# Stamped once at import so /metrics can report process uptime without
# depending on the lifespan having run (tests bypass it). monotonic() is
# immune to wall-clock adjustments.
_PROCESS_START = time.monotonic()

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
        # Initialize Sentry before any other boot work, so a guard failure
        # below also reports as a Sentry event (when SENTRY_DSN is set). A
        # bad OWNEVO_SENTRY_TRACES_SAMPLE_RATE fails the boot here rather
        # than silently disabling traces or sampling at the wrong rate.
        init_sentry()

        # Boot-time guard: dev-auth and the shared signing key are mutually
        # exclusive. With both set the kernel would silently fall back to the
        # seeded dev principal for any unauthenticated request, effectively
        # bypassing workspace isolation in a production deployment.
        # Fail here — at startup — rather than silently granting access at
        # runtime where the misconfiguration is invisible in logs.
        if dev_auth_enabled() and os.environ.get(INTERNAL_AUTH_KEY_ENV):
            raise RuntimeError(
                f"{DEV_AUTH_ENV}=true is set alongside {INTERNAL_AUTH_KEY_ENV}. "
                "These flags are mutually exclusive: dev-auth makes the kernel "
                "resolve every unauthenticated request to the seeded dev user, "
                "bypassing workspace isolation when a real signing key is in use. "
                f"Unset {DEV_AUTH_ENV} in any deployment that authenticates real users."
            )

        # Production boot guard: when the deployment explicitly identifies as
        # production, refuse the dev-auth fallback outright (independent of
        # whether a signing key is present) and assert the required server
        # secrets are configured. The signing-key check above catches the most
        # dangerous misconfiguration; this catches the rest -- a prod boot
        # without a key would otherwise reject every request at runtime, and a
        # prod boot without the credentials master key would fail at first
        # integration write rather than at startup.
        if is_production():
            if dev_auth_enabled():
                raise RuntimeError(
                    f"{DEPLOY_ENV_VAR}={PRODUCTION_ENV_VALUE} is set with "
                    f"{DEV_AUTH_ENV}=true. The dev-auth fallback resolves every "
                    "unauthenticated request to the seeded dev user and the "
                    "default workspace, which would bypass real authentication "
                    f"in production. Unset {DEV_AUTH_ENV}."
                )
            missing = [
                name
                for name in (INTERNAL_AUTH_KEY_ENV, MASTER_KEY_ENV)
                if not os.environ.get(name)
            ]
            if missing:
                raise RuntimeError(
                    f"{DEPLOY_ENV_VAR}={PRODUCTION_ENV_VALUE} but required "
                    f"production secrets are not set: {', '.join(missing)}. "
                    "These must be present in the deployment environment so a "
                    "misconfigured prod boot fails loudly instead of silently "
                    "rejecting authenticated requests or crashing at the first "
                    "credential write."
                )

        # Validate the sandbox admission cap at startup so a misconfigured
        # OWNEVO_SANDBOX_MAX_CONCURRENT fails fast here rather than on the
        # first sandbox call (which may be mid-iteration after LLM budget is spent).
        try:
            _read_max_concurrent()
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

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
            # Pool sizing + per-connection statement_timeout default from env
            # (OWNEVO_DB_POOL_MIN_SIZE / _MAX_SIZE / _STATEMENT_TIMEOUT_MS);
            # see db.py for the validation and defaults. The statement_timeout
            # caps any single query so one runaway can't pin a connection.
            app.state.pool = await open_pool(db_url)
        else:
            # Caller-managed pool — don't open or close it here.
            app.state.pool = pool

        # Close any iteration row stuck in 'running' from a previous boot.
        # Each workflow's run-iteration endpoint refuses to start a new
        # iteration while another is in flight; an orphaned row would block
        # that workflow indefinitely until the row was manually closed.
        try:
            await reap_orphaned_iterations(app.state.pool)
        except Exception:  # noqa: BLE001 — reaper must not block startup
            logger.exception(
                "orphan reaper: failed at startup; continuing without sweep"
            )

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
            # Drain any queued Sentry events before the process exits.
            # SIGTERM bypasses atexit hooks in containers, so we flush
            # explicitly here. No-op when SENTRY_DSN is unset.
            flush_sentry()

    # Opt-in JSON log formatter (OWNEVO_LOG_FORMAT=json). Called before
    # the app is built so logs emitted from middleware/handler setup land
    # in the configured format too. No-op when the env var is unset.
    configure_logging()

    api = FastAPI(
        title="ownEvo approval API",
        version="0.1.0",
        description=(
            "Approval queue surface for the W2.5 UI scaffold. "
            "REST seam between Python kernel and TS web app."
        ),
        lifespan=lifespan,
    )

    # Global exception handler -- registers a structured 500 with an
    # error_id correlation key so an unhandled error in any route surfaces
    # as a grepable log line instead of a bare Starlette traceback.
    install_exception_handler(api)

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

    # Request-id middleware installed AFTER CORS so the id is attached
    # to OPTIONS pre-flights too. add_middleware wraps in LIFO order, so
    # the request-id layer sits on the outside. For normal responses the
    # header is written here; for unhandled exceptions it is written by
    # the exception handler directly (ServerErrorMiddleware catches before
    # the request-id layer can write it on the way out).
    api.add_middleware(RequestIdMiddleware)

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
    api.include_router(internal_auth.router)
    api.include_router(internal_workspaces.router)
    api.include_router(internal_invites.router)
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

    async def _db_ok() -> bool:
        """True when the pool answers a SELECT 1. Never raises — a probe or
        a metrics scrape must report the failure as a value, not a 500."""
        pool = getattr(api.state, "pool", None)
        if pool is None:
            return False
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:  # noqa: BLE001 — surfaced as db_up=0 / not_ready
            return False

    @api.get("/api/livez", tags=["health"])
    async def livez() -> dict[str, str]:
        """Liveness: the process is up and the event loop is responsive.

        Deliberately dependency-free — it does NOT touch the DB. An
        orchestrator uses liveness to decide whether to *restart* the
        container, so a transient DB outage must not flip it (that would
        cause a restart loop). DB reachability is the readiness probe's job.
        """
        return {"status": "ok"}

    @api.get("/api/readyz", tags=["health"])
    async def readyz(response: Response) -> dict[str, str]:
        """Readiness: can this instance serve traffic right now.

        Returns 503 when the pool can't answer, so a load balancer pulls
        the instance out of rotation rather than routing requests that
        will fail. Unlike liveness, readiness is allowed to flip during a
        DB blip without implying the process should be restarted.
        """
        if await _db_ok():
            return {"status": "ready", "db": "ok"}
        response.status_code = 503
        return {"status": "not_ready", "db": "unavailable"}

    @api.get("/metrics", tags=["health"])
    async def metrics() -> Response:
        """Operational gauges in Prometheus text exposition format.

        Pool saturation, sandbox admission cap, DB reachability, uptime —
        enough for a scraper to alert on the kernel's own health. Not the
        product's AgentEvent observability, which is the trace-format seam.
        """
        pool = getattr(api.state, "pool", None)
        text = render_metrics(
            uptime_seconds=time.monotonic() - _PROCESS_START,
            db_up=await _db_ok(),
            pool_size=pool.get_size() if pool is not None else None,
            pool_idle=pool.get_idle_size() if pool is not None else None,
            sandbox_max_concurrent=_read_max_concurrent(),
        )
        return Response(content=text, media_type=METRICS_CONTENT_TYPE)

    return api


# Module-level instance for `uvicorn ownevo_kernel.api.app:app`.
app = create_app()
