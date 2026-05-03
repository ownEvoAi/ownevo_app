"""Trace capture pipeline — DB-backed integration tests.

Verifies that an AgentEvent stream collected during an agent run lands
in the `traces` table as a usable JSONB array, with run-level metadata
attached, and that the session finalizes even on exceptions (failed
iterations still produce traces for the clustering pipeline).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from ownevo_format import AgentEventAdapter, ContentDelta, ToolCallStart
from ownevo_kernel.db import ENV_VAR, migrate
from ownevo_kernel.traces import TraceCollector, trace_session

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


def _admin_url() -> str:
    base = os.environ[ENV_VAR]
    return base.rsplit("/", 1)[0] + "/postgres"


@pytest.fixture
async def db():
    dbname = f"ownevo_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_admin_url())
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()
    base = os.environ[ENV_VAR]
    test_url = base.rsplit("/", 1)[0] + f"/{dbname}"
    conn = await asyncpg.connect(test_url)
    try:
        await migrate(conn)
        yield conn
    finally:
        await conn.close()
        admin = await asyncpg.connect(_admin_url())
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=$1 AND pid<>pg_backend_pid()",
                dbname,
            )
            await admin.execute(f'DROP DATABASE "{dbname}"')
        finally:
            await admin.close()


def _decode_jsonb(conn_data: str | dict | list) -> dict | list:
    """Decode a JSONB column to a Python value. asyncpg returns JSONB as
    a JSON-encoded string by default; some setups register a codec that
    pre-decodes to dict/list, so handle both."""
    if isinstance(conn_data, (dict, list)):
        return conn_data
    return json.loads(conn_data)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_trace_session_persists_events_in_order(db: asyncpg.Connection):
    async with trace_session(db) as session:
        e1 = session.make_event(
            type="content_delta",
            text="hello",
            model="claude-opus-4-7",
        )
        e2 = session.make_event(
            type="tool_call_start",
            call_id="toolu_1",
            name="lookup_supplier",
            args={"id": "X"},
        )
        session.record(e1)
        session.record(e2)
        recorded_trace_id = session.trace_id

    row = await db.fetchrow(
        "SELECT id, events, ended_at FROM traces WHERE id = $1",
        recorded_trace_id,
    )
    assert row is not None
    events = _decode_jsonb(row["events"])
    assert [e["type"] for e in events] == ["content_delta", "tool_call_start"]
    assert events[0]["text"] == "hello"
    assert events[1]["call_id"] == "toolu_1"
    assert row["ended_at"] is not None


async def test_round_trip_through_AgentEventAdapter(db: asyncpg.Connection):
    """Stored events must parse back to typed AgentEvents — that's the
    contract the gate runner / clustering pipeline reads against."""
    async with trace_session(db) as session:
        session.record(session.make_event(
            type="content_delta", text="x", model="claude-opus-4-7",
        ))
        trace_id = session.trace_id

    raw = await db.fetchval("SELECT events FROM traces WHERE id = $1", trace_id)
    events = _decode_jsonb(raw)
    typed = [AgentEventAdapter.validate_python(e) for e in events]
    assert isinstance(typed[0], ContentDelta)
    assert typed[0].text == "x"


# ---------------------------------------------------------------------------
# Run-level metadata
# ---------------------------------------------------------------------------


async def test_metric_outputs_and_token_usage_persisted(db: asyncpg.Connection):
    async with trace_session(db) as session:
        session.set_metric_outputs({"acc": 0.84, "n": 100})
        session.set_token_usage({"input": 1234, "output": 567})
        trace_id = session.trace_id

    row = await db.fetchrow(
        "SELECT metric_outputs, token_usage FROM traces WHERE id = $1",
        trace_id,
    )
    assert _decode_jsonb(row["metric_outputs"]) == {"acc": 0.84, "n": 100}
    assert _decode_jsonb(row["token_usage"]) == {"input": 1234, "output": 567}


# ---------------------------------------------------------------------------
# Trace ID hygiene
# ---------------------------------------------------------------------------


async def test_record_rejects_event_with_wrong_trace_id(db: asyncpg.Connection):
    """Routing an event to the wrong session must raise, not silently
    corrupt the trace_id linkage."""
    async with trace_session(db) as session:
        bogus = ToolCallStart(
            event_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),  # NOT the session's trace_id
            timestamp=datetime.now(UTC),
            type="tool_call_start",
            call_id="x",
            name="x",
            args={},
        )
        with pytest.raises(ValueError, match="trace_id"):
            session.record(bogus)


def test_make_event_propagates_iteration_id():
    """When the session knows the iteration, events carry it forward
    automatically. Saves callers from threading it on every event.
    Pure unit test — no DB needed for the propagation behavior itself."""
    iteration_id = uuid.uuid4()
    collector = TraceCollector(iteration_id=iteration_id)
    e = collector.make_event(
        type="content_delta", text="t", model="claude-opus-4-7",
    )
    assert e.iteration_id == iteration_id
    assert e.trace_id == collector.trace_id


def test_make_event_rejects_unknown_type():
    """`make_event` must validate against the AgentEvent discriminated
    union so a typo in `type` doesn't silently produce a malformed event."""
    from pydantic import ValidationError

    collector = TraceCollector()
    with pytest.raises(ValidationError):
        collector.make_event(type="not_a_real_event_type", text="x")


def test_make_event_rejects_missing_required_field():
    """ContentDelta requires `text` and `model`; omitting either must
    surface as a validation error before any record/finalize."""
    from pydantic import ValidationError

    collector = TraceCollector()
    with pytest.raises(ValidationError):
        collector.make_event(type="content_delta", text="x")  # missing model


# ---------------------------------------------------------------------------
# Failure path — finalize must still fire
# ---------------------------------------------------------------------------


async def test_session_finalizes_on_exception(db: asyncpg.Connection):
    """Failing iterations must still produce a trace row — the failure-
    clustering pipeline reads from it."""
    trace_id = uuid.uuid4()
    with pytest.raises(RuntimeError, match="boom"):
        async with trace_session(db, trace_id=trace_id) as session:
            session.record(session.make_event(
                type="content_delta", text="partial", model="x",
            ))
            raise RuntimeError("boom")

    row = await db.fetchrow("SELECT events FROM traces WHERE id = $1", trace_id)
    assert row is not None
    events = _decode_jsonb(row["events"])
    assert events[0]["text"] == "partial"


async def test_finalize_is_idempotent(db: asyncpg.Connection):
    """Calling finalize twice (e.g., manually + on context exit) should
    only INSERT once."""
    collector = TraceCollector()
    collector.record(collector.make_event(
        type="content_delta", text="x", model="m",
    ))
    await collector.finalize(db)
    await collector.finalize(db)  # would violate PK if it INSERTed again

    rows = await db.fetch("SELECT id FROM traces WHERE id = $1", collector.trace_id)
    assert len(rows) == 1


async def test_empty_session_persists_empty_events_array(db: asyncpg.Connection):
    """A session that records zero events still INSERTs a row, with
    `events == []`. The clustering pipeline reads `events` and must
    handle the empty case (e.g., a sandbox-error iteration that died
    before emitting anything)."""
    async with trace_session(db) as session:
        trace_id = session.trace_id

    row = await db.fetchrow(
        "SELECT events FROM traces WHERE id = $1",
        trace_id,
    )
    assert row is not None
    assert _decode_jsonb(row["events"]) == []


# ---------------------------------------------------------------------------
# Linked entities
# ---------------------------------------------------------------------------


async def test_trace_links_to_workflow_and_skill_version(db: asyncpg.Connection):
    """workflow_id and skill_version_id are FKs; the values we INSERT
    must satisfy them, so set them up first."""
    await db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, $2, '{}'::jsonb)",
        "demo-workflow",
        "test workflow",
    )
    await db.execute(
        "INSERT INTO skills (id, kind) VALUES ('demo-skill', 'instruction'::skill_kind)",
    )
    skill_version_id = await db.fetchval(
        """
        INSERT INTO skill_versions (skill_id, version_seq, content, created_by)
        VALUES ('demo-skill', 1, 'body', 'human:test')
        RETURNING id
        """,
    )

    async with trace_session(
        db,
        workflow_id="demo-workflow",
        skill_version_id=skill_version_id,
    ) as session:
        session.record(session.make_event(
            type="content_delta", text="x", model="m",
        ))
        trace_id = session.trace_id

    row = await db.fetchrow(
        "SELECT workflow_id, skill_version_id FROM traces WHERE id = $1",
        trace_id,
    )
    assert row["workflow_id"] == "demo-workflow"
    assert row["skill_version_id"] == skill_version_id
