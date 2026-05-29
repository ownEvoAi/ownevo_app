"""Readiness/liveness split + Prometheus /metrics endpoint.

Two layers:

  * Pure unit tests of `render_metrics` — no app, no DB. The renderer takes
    already-collected values, so the exposition format is fully testable in
    isolation.
  * Endpoint tests. The no-DB cases mount the app *without* entering the
    lifespan, so `app.state.pool` is unset — exercising the "pool absent"
    branch (liveness still 200, readiness 503, metrics db_up=0). The DB-backed
    cases use the `api_client` fixture (lifespan run, pool live) to confirm
    the ready/healthy path.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import httpx
import pytest
from httpx import ASGITransport
from ownevo_kernel.api._metrics import CONTENT_TYPE, render_metrics
from ownevo_kernel.api.app import create_app
from ownevo_kernel.db import ENV_VAR

# The `api_client` fixture builds a real asyncpg pool, so the DB-backed cases
# below can only run when a database is configured. The CI lint/unit job runs
# without one; mark just those tests so they skip there instead of erroring.
# The pure-renderer and no-DB endpoint tests above carry no such guard and
# always run.
requires_db = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping DB-backed integration tests",
)

# ---------------------------------------------------------------------------
# Pure renderer
# ---------------------------------------------------------------------------


def test_render_metrics_includes_core_gauges() -> None:
    text = render_metrics(
        uptime_seconds=12.0,
        db_up=True,
        pool_size=5,
        pool_idle=3,
        sandbox_max_concurrent=4,
    )
    assert "ownevo_up 1" in text
    assert "ownevo_uptime_seconds 12" in text
    assert "ownevo_db_up 1" in text
    assert "ownevo_db_pool_size 5" in text
    assert "ownevo_db_pool_idle 3" in text
    # in_use is derived: size - idle.
    assert "ownevo_db_pool_in_use 2" in text
    assert "ownevo_sandbox_max_concurrent 4" in text
    # Every gauge carries HELP + TYPE lines.
    assert "# HELP ownevo_up" in text
    assert "# TYPE ownevo_up gauge" in text
    assert text.endswith("\n")


def test_render_metrics_db_down_is_zero() -> None:
    text = render_metrics(
        uptime_seconds=1.0,
        db_up=False,
        pool_size=1,
        pool_idle=0,
        sandbox_max_concurrent=4,
    )
    assert "ownevo_db_up 0" in text
    assert "ownevo_db_pool_in_use 1" in text


def test_render_metrics_emits_labeled_job_gauges() -> None:
    """Job counts render as a labeled gauge family, one line per status, with
    a single HELP/TYPE pair. The failed series is always present (even at 0) so
    an alert rule can key on it."""
    text = render_metrics(
        uptime_seconds=1.0,
        db_up=True,
        pool_size=1,
        pool_idle=1,
        sandbox_max_concurrent=4,
        jobs_by_status={"queued": 3, "running": 1, "failed": 0},
    )
    assert text.count("# TYPE ownevo_jobs gauge") == 1
    assert 'ownevo_jobs{status="queued"} 3' in text
    assert 'ownevo_jobs{status="running"} 1' in text
    assert 'ownevo_jobs{status="failed"} 0' in text


def test_render_metrics_omits_job_gauges_when_absent() -> None:
    """No counts collected (no pool / aggregation failed) → the jobs block is
    omitted, not zeroed — same 'absent vs zero' treatment as the pool gauges."""
    text = render_metrics(
        uptime_seconds=1.0,
        db_up=True,
        pool_size=1,
        pool_idle=1,
        sandbox_max_concurrent=4,
        jobs_by_status=None,
    )
    assert "ownevo_jobs" not in text


def test_render_metrics_omits_pool_gauges_when_absent() -> None:
    """No pool attached → pool gauges are omitted, not reported as zero, so a
    scraper can distinguish 'pool absent' from 'pool empty'."""
    text = render_metrics(
        uptime_seconds=1.0,
        db_up=False,
        pool_size=None,
        pool_idle=None,
        sandbox_max_concurrent=4,
    )
    assert "ownevo_db_pool_size" not in text
    assert "ownevo_db_pool_idle" not in text
    assert "ownevo_db_pool_in_use" not in text
    # Non-pool gauges still present.
    assert "ownevo_up 1" in text
    assert "ownevo_db_up 0" in text


def test_render_metrics_pool_in_use_clamps_at_zero() -> None:
    """asyncpg can momentarily report idle > size during churn; clamp to 0."""
    text = render_metrics(
        uptime_seconds=1.0,
        db_up=True,
        pool_size=3,
        pool_idle=5,
        sandbox_max_concurrent=4,
    )
    assert "ownevo_db_pool_in_use 0" in text


def test_render_metrics_asymmetric_none_pool_size_absent() -> None:
    """pool_size=None with pool_idle present: only idle gauge is emitted."""
    text = render_metrics(
        uptime_seconds=1.0,
        db_up=True,
        pool_size=None,
        pool_idle=5,
        sandbox_max_concurrent=4,
    )
    assert "ownevo_db_pool_idle 5" in text
    assert "ownevo_db_pool_size" not in text
    assert "ownevo_db_pool_in_use" not in text


def test_render_metrics_fractional_uptime() -> None:
    """Sub-second uptime renders with 3-decimal precision, not as an integer."""
    text = render_metrics(
        uptime_seconds=12.345,
        db_up=True,
        pool_size=None,
        pool_idle=None,
        sandbox_max_concurrent=4,
    )
    assert "ownevo_uptime_seconds 12.345" in text


# ---------------------------------------------------------------------------
# Endpoints — no DB (lifespan not entered, so no pool)
# ---------------------------------------------------------------------------


@pytest.fixture
async def no_db_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    # No lifespan_context → app.state.pool is never set.
    app = create_app(cors_origins=[])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c


async def test_livez_is_dependency_free(no_db_client: httpx.AsyncClient) -> None:
    """Liveness stays 200 even with no DB pool — it must not flip on a DB blip."""
    resp = await no_db_client.get("/api/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_503_when_pool_absent(no_db_client: httpx.AsyncClient) -> None:
    """Readiness fails closed when the DB is unreachable so an LB drains us."""
    resp = await no_db_client.get("/api/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready", "db": "unavailable"}


async def test_metrics_text_format_without_pool(no_db_client: httpx.AsyncClient) -> None:
    resp = await no_db_client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == CONTENT_TYPE
    body = resp.text
    assert "ownevo_up 1" in body
    assert "ownevo_db_up 0" in body  # no pool → not up
    assert "ownevo_sandbox_max_concurrent" in body
    assert "ownevo_db_pool_size" not in body  # omitted when pool absent


# ---------------------------------------------------------------------------
# Endpoints — DB-backed (lifespan run via api_client, pool live)
# ---------------------------------------------------------------------------


@requires_db
async def test_livez_is_200_with_live_pool(api_client: httpx.AsyncClient) -> None:
    """Liveness stays dependency-free even when the pool is live."""
    resp = await api_client.get("/api/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@requires_db
async def test_readyz_ready_with_live_pool(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.get("/api/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "db": "ok"}


@requires_db
async def test_metrics_reports_live_pool(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == CONTENT_TYPE
    body = resp.text
    assert "ownevo_db_up 1" in body
    # Pool is live, so the pool gauges are present.
    assert "ownevo_db_pool_size" in body
    assert "ownevo_db_pool_in_use" in body
