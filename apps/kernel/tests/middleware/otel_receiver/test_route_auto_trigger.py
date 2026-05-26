"""The ingest route nudges the auto-clustering trigger on tool failures.

These assert the *wiring* only: that a bound batch carrying a tool
failure marks the workflow pending on `app.state.cluster_auto_trigger`,
and that a clean batch (or an unbound one) does not. The debounce is set
absurdly high so the background loop never actually runs the (heavy)
clustering pipeline during the test — only the in-process pending set is
inspected.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import asyncpg
import httpx
import pytest
from httpx import ASGITransport
from ownevo_kernel.api.app import create_app
from ownevo_kernel.db import ENV_VAR

from ._fixture_cases import CASES

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping auto-trigger wiring tests",
)


def _payload(name: str) -> dict:
    return next(c.payload for c in CASES if c.name == name)


async def _seed_workflow(db: asyncpg.Connection, wf_id: str) -> None:
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, $2, '{}'::jsonb) ON CONFLICT DO NOTHING",
        wf_id,
        f"auto-trigger test ({wf_id})",
    )


async def _app_and_client(db: asyncpg.Connection):  # noqa: ANN202
    """Build an app on the per-test DB with the auto-trigger enabled.

    Returns (app, pool) so the test can close the pool in the finally
    block and inspect `app.state.cluster_auto_trigger` after a request.
    """
    dbname = await db.fetchval("SELECT current_database()")
    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    app = create_app(pool=pool, cors_origins=[])
    return app, pool


async def test_failing_batch_marks_workflow_pending(
    db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWNEVO_CLUSTER_AUTOTRIGGER", "true")
    # Huge debounce: the loop never fires the real pipeline mid-test.
    monkeypatch.setenv("OWNEVO_CLUSTER_AUTOTRIGGER_DEBOUNCE_SECONDS", "3600")
    await _seed_workflow(db, "wf-fail")

    app, pool = await _app_and_client(db)
    try:
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=transport, base_url="http://api.test",
        ) as client:
            resp = await client.post(
                "/api/otel/v1/traces?workflow_id=wf-fail",
                json=_payload("06_tool_call_logical_error"),
            )
            assert resp.status_code == 200, resp.text

            trigger = app.state.cluster_auto_trigger
            assert trigger is not None
            # _pending is keyed by (workspace_id, workflow_id) so the deferred
            # clustering run binds the same workspace the ingest landed in.
            assert ("default", "wf-fail") in trigger._pending
    finally:
        await pool.close()


async def test_clean_batch_does_not_mark_pending(
    db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWNEVO_CLUSTER_AUTOTRIGGER", "true")
    monkeypatch.setenv("OWNEVO_CLUSTER_AUTOTRIGGER_DEBOUNCE_SECONDS", "3600")
    await _seed_workflow(db, "wf-ok")

    app, pool = await _app_and_client(db)
    try:
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=transport, base_url="http://api.test",
        ) as client:
            resp = await client.post(
                "/api/otel/v1/traces?workflow_id=wf-ok",
                json=_payload("05_tool_call_ok"),
            )
            assert resp.status_code == 200, resp.text

            trigger = app.state.cluster_auto_trigger
            assert trigger is not None
            assert trigger._pending == {}
    finally:
        await pool.close()


async def test_disabled_by_default_no_trigger(
    db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Env var unset → no trigger started; the route signal call is a no-op.
    monkeypatch.delenv("OWNEVO_CLUSTER_AUTOTRIGGER", raising=False)
    await _seed_workflow(db, "wf-off")

    app, pool = await _app_and_client(db)
    try:
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=transport, base_url="http://api.test",
        ) as client:
            resp = await client.post(
                "/api/otel/v1/traces?workflow_id=wf-off",
                json=_payload("06_tool_call_logical_error"),
            )
            assert resp.status_code == 200, resp.text
            assert app.state.cluster_auto_trigger is None
    finally:
        await pool.close()
