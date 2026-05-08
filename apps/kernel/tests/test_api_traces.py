"""Integration tests for the W7 slice 8 (7.1.9) `/api/traces` surface.

Mirrors the pattern in `test_api_workflows.py`: in-process httpx +
ASGITransport, skip when `OWNEVO_DATABASE_URL` is unset.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse

import asyncpg
import httpx
import pytest
from httpx import ASGITransport
from ownevo_kernel.api.app import create_app
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


@pytest.fixture
async def api_client(db: asyncpg.Connection):
    dbname = await db.fetchval("SELECT current_database()")
    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        app = create_app(pool=pool, cors_origins=[])
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=transport, base_url="http://api.test",
        ) as client:
            yield client
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_workflow(conn: asyncpg.Connection, *, workflow_id: str) -> None:
    await conn.execute(
        "INSERT INTO workflows (id, description, spec, mode) "
        "VALUES ($1, 'desc', '{}'::jsonb, 'gated'::workflow_mode) "
        "ON CONFLICT DO NOTHING",
        workflow_id,
    )


def _make_event(
    *,
    kind: str,
    trace_id: uuid.UUID,
    timestamp: datetime,
    extra: dict | None = None,
) -> dict:
    base = {
        "type": kind,
        "event_id": str(uuid.uuid4()),
        "trace_id": str(trace_id),
        "iteration_id": None,
        "timestamp": timestamp.isoformat(),
        "parent_span_id": None,
    }
    if extra:
        base.update(extra)
    return base


async def _seed_trace(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    events: list[dict],
    started_at: datetime | None = None,
) -> uuid.UUID:
    trace_id = uuid.uuid4()
    started_at = started_at or datetime.now(tz=UTC)
    # Re-stamp event trace_id so the seeded events all reference the
    # row we're about to insert.
    fixed = []
    for e in events:
        e2 = dict(e)
        e2["trace_id"] = str(trace_id)
        fixed.append(e2)
    await conn.execute(
        """
        INSERT INTO traces (id, workflow_id, events, started_at, ended_at)
        VALUES ($1, $2, $3::jsonb, $4, $5)
        """,
        trace_id,
        workflow_id,
        json.dumps(fixed),
        started_at,
        started_at + timedelta(seconds=2),
    )
    return trace_id


# ---------------------------------------------------------------------------
# GET /api/workflows/{id}/traces
# ---------------------------------------------------------------------------


async def test_workflow_traces_404_on_unknown(api_client: httpx.AsyncClient):
    res = await api_client.get("/api/workflows/nope/traces")
    assert res.status_code == 404


async def test_workflow_traces_empty(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-empty-traces")
    res = await api_client.get("/api/workflows/wf-empty-traces/traces")
    assert res.status_code == 200
    body = res.json()
    assert body == {"workflow_id": "wf-empty-traces", "items": []}


async def test_workflow_traces_lists_with_kind_counts(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """Seed two traces with mixed event kinds and assert the list view
    returns event_count + kind_counts derived from the JSONB events.
    """
    await _seed_workflow(db, workflow_id="wf-list")
    now = datetime.now(tz=UTC)
    t1 = uuid.uuid4()
    t1 = await _seed_trace(
        db,
        workflow_id="wf-list",
        events=[
            _make_event(kind="skill_loaded", trace_id=t1, timestamp=now,
                        extra={"skill_id": "s.x", "version_seq": 1,
                               "retention_acknowledged": True}),
            _make_event(kind="content_delta", trace_id=t1,
                        timestamp=now + timedelta(seconds=1),
                        extra={"text": "hi", "model": "claude",
                               "cumulative_text": None}),
            _make_event(kind="content_delta", trace_id=t1,
                        timestamp=now + timedelta(seconds=2),
                        extra={"text": " there", "model": "claude",
                               "cumulative_text": "hi there"}),
        ],
        started_at=now - timedelta(minutes=5),
    )
    t2 = uuid.uuid4()
    t2 = await _seed_trace(
        db,
        workflow_id="wf-list",
        events=[
            _make_event(kind="skill_loaded", trace_id=t2, timestamp=now,
                        extra={"skill_id": "s.y", "version_seq": 1,
                               "retention_acknowledged": False}),
        ],
        started_at=now,  # newer
    )

    res = await api_client.get("/api/workflows/wf-list/traces")
    assert res.status_code == 200
    body = res.json()
    assert body["workflow_id"] == "wf-list"
    assert len(body["items"]) == 2

    # newest first
    assert body["items"][0]["id"] == str(t2)
    assert body["items"][0]["event_count"] == 1
    assert body["items"][0]["kind_counts"] == {"skill_loaded": 1}

    assert body["items"][1]["id"] == str(t1)
    assert body["items"][1]["event_count"] == 3
    assert body["items"][1]["kind_counts"] == {
        "skill_loaded": 1,
        "content_delta": 2,
    }


# ---------------------------------------------------------------------------
# GET /api/traces/{trace_id}
# ---------------------------------------------------------------------------


async def test_get_trace_404_on_unknown(api_client: httpx.AsyncClient):
    res = await api_client.get(f"/api/traces/{uuid.uuid4()}")
    assert res.status_code == 404


async def test_get_trace_returns_full_event_stream(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-trace")
    now = datetime.now(tz=UTC)
    placeholder = uuid.uuid4()
    trace_id = await _seed_trace(
        db,
        workflow_id="wf-trace",
        events=[
            _make_event(kind="skill_loaded", trace_id=placeholder, timestamp=now,
                        extra={"skill_id": "s.demand", "version_seq": 7,
                               "retention_acknowledged": True}),
            _make_event(kind="reasoning_delta", trace_id=placeholder,
                        timestamp=now + timedelta(milliseconds=200),
                        extra={"text": "think...", "model": "claude"}),
            _make_event(kind="tool_call_start", trace_id=placeholder,
                        timestamp=now + timedelta(milliseconds=400),
                        extra={"call_id": "toolu_a", "name": "lookup",
                               "args": {"q": "x"}}),
            _make_event(kind="tool_call_result", trace_id=placeholder,
                        timestamp=now + timedelta(milliseconds=900),
                        extra={"call_id": "toolu_a", "name": "lookup",
                               "status": "ok", "output": {"hits": 3},
                               "duration_ms": 480, "error": None,
                               "error_class": None}),
            _make_event(kind="content_delta", trace_id=placeholder,
                        timestamp=now + timedelta(milliseconds=1100),
                        extra={"text": "Done.", "model": "claude",
                               "cumulative_text": "Done."}),
            _make_event(kind="citation", trace_id=placeholder,
                        timestamp=now + timedelta(milliseconds=1200),
                        extra={"ref": 1, "source": "doc-1",
                               "quote": "supplier A"}),
        ],
    )

    res = await api_client.get(f"/api/traces/{trace_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == str(trace_id)
    assert body["workflow_id"] == "wf-trace"
    # ≥6 distinct event kinds covered (PLAN row 7.1.9 validation)
    kinds = {e["type"] for e in body["events"]}
    assert kinds >= {
        "skill_loaded",
        "reasoning_delta",
        "tool_call_start",
        "tool_call_result",
        "content_delta",
        "citation",
    }
    # Tool-call status round-trips
    tool_result = next(
        e for e in body["events"] if e["type"] == "tool_call_result"
    )
    assert tool_result["status"] == "ok"
    assert tool_result["duration_ms"] == 480
    assert tool_result["output"] == {"hits": 3}
