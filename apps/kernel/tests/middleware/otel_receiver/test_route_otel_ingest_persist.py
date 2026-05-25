"""End-to-end persistence assertions for `POST /api/otel/v1/traces`.

The receiver only earns its keep once the decoded `AgentEvent`s land
in the `traces` table — that's the contract the failure clustering
pipeline, the gate runner, and the trace inspection UI read against.
These tests POST a payload through the live FastAPI app, then read
back through both the raw DB connection and the public
`GET /api/traces/{id}` endpoint to prove the round trip works.

Pinned guarantees:

  * One traces row per unique `AgentEvent.trace_id` in the batch.
  * `ingest_source = 'otlp'` (vs NULL for kernel-emitted traces).
  * `events` JSONB array preserves arrival order; each element is
    AgentEvent-shaped (parseable back through `AgentEventAdapter`).
  * A second POST for the same trace_id appends — does not duplicate
    or overwrite.
  * The trace shows up in `GET /api/traces` and the per-trace
    `GET /api/traces/{id}` detail endpoint.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import asyncpg
import httpx
import pytest
from ownevo_format import AgentEventAdapter
from ownevo_kernel.db import ENV_VAR

from ._fixture_cases import CASES
from ._fixture_helpers import make_span, str_attr, wrap_batch

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


def _case(name: str) -> dict[str, Any]:
    return next(c.payload for c in CASES if c.name == name)


def _decode_jsonb(value: str | list | dict) -> Any:
    if isinstance(value, (list, dict)):
        return value
    return json.loads(value)


async def test_chat_batch_lands_in_traces_table(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    """A single chat span produces a traces row with ingest_source='otlp'."""
    resp = await api_client.post(
        "/api/otel/v1/traces", json=_case("01_chat_basic_text"),
    )
    assert resp.status_code == 200
    created = resp.json()["created_trace_ids"]
    assert len(created) == 1
    trace_id = uuid.UUID(created[0])

    row = await db.fetchrow(
        "SELECT id, ingest_source, events, started_at, ended_at, "
        "workflow_id, iteration_id "
        "FROM traces WHERE id = $1",
        trace_id,
    )
    assert row is not None
    assert row["ingest_source"] == "otlp"
    # External-source traces have no workflow / iteration binding yet —
    # those land when a customer associates an OTLP collector with a
    # registered workflow (future slice).
    assert row["workflow_id"] is None
    assert row["iteration_id"] is None

    events = _decode_jsonb(row["events"])
    assert len(events) == 1
    assert events[0]["type"] == "content_delta"
    # Each persisted event must parse back through the typed adapter —
    # if the JSONB round-trip drops a discriminator or a load-bearing
    # field, the gate runner can't read the trace.
    AgentEventAdapter.validate_python(events[0])

    assert row["started_at"] is not None
    assert row["ended_at"] is not None


async def test_end_to_end_run_persists_three_events_in_order(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    """The realistic 3-event run (chat → tool start → tool result) round-trips."""
    resp = await api_client.post(
        "/api/otel/v1/traces", json=_case("12_end_to_end_agent_run"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_events"] == 3
    assert len(body["created_trace_ids"]) == 1
    trace_id = uuid.UUID(body["created_trace_ids"][0])

    events = _decode_jsonb(
        await db.fetchval("SELECT events FROM traces WHERE id = $1", trace_id),
    )
    kinds = [e["type"] for e in events]
    assert kinds == ["content_delta", "tool_call_start", "tool_call_result"]


async def test_repeat_post_for_same_trace_appends_not_duplicates(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    """Second batch with the same trace_id extends the existing row.

    Real OTel collectors flush spans in waves as they complete; the
    inspection UI walks one row per trace, so duplicates would split
    the view and overwrites would lose history. The upsert-on-conflict
    path must concatenate the events array — pinned here.
    """
    # First wave: one chat span. Capture the OTel trace_id from the
    # payload so the second wave can target the same trace.
    payload1 = _case("01_chat_basic_text")
    otel_trace_hex = payload1["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"]

    resp1 = await api_client.post("/api/otel/v1/traces", json=payload1)
    assert resp1.status_code == 200
    trace_id = uuid.UUID(resp1.json()["created_trace_ids"][0])

    # Second wave: a fresh tool-call pair on the same trace_id.
    follow_up_span = make_span(
        span_id="bbcc" * 4,
        trace_id=otel_trace_hex,
        name="gen_ai.execute_tool",
        attributes=[
            str_attr("gen_ai.operation.name", "execute_tool"),
            str_attr("gen_ai.tool.call.id", "toolu_followup"),
            str_attr("gen_ai.tool.name", "lookup"),
            str_attr("gen_ai.tool.call.arguments", "{}"),
            str_attr("gen_ai.tool.call.result", "{}"),
        ],
        status_code=1,
    )
    resp2 = await api_client.post(
        "/api/otel/v1/traces", json=wrap_batch([follow_up_span]),
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["created_trace_ids"] == []
    assert body2["appended_trace_ids"] == [str(trace_id)]

    # Single row, full arrival-order event stream.
    rows = await db.fetch("SELECT id FROM traces WHERE id = $1", trace_id)
    assert len(rows) == 1
    events = _decode_jsonb(
        await db.fetchval("SELECT events FROM traces WHERE id = $1", trace_id),
    )
    kinds = [e["type"] for e in events]
    assert kinds == ["content_delta", "tool_call_start", "tool_call_result"]


async def test_multiple_traces_in_one_batch_create_separate_rows(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    """One HTTP request carrying spans from two different OTel traces."""
    span_a = make_span(
        span_id="11aa" * 4,
        trace_id="aaaa" * 8,
        attributes=[
            str_attr("gen_ai.operation.name", "chat"),
            str_attr("gen_ai.response.model", "claude-opus-4-7"),
            {
                "key": "gen_ai.output.messages",
                "value": {
                    "arrayValue": {
                        "values": [
                            {
                                "kvlistValue": {
                                    "values": [
                                        str_attr("role", "assistant"),
                                        {
                                            "key": "parts",
                                            "value": {
                                                "arrayValue": {
                                                    "values": [
                                                        {
                                                            "kvlistValue": {
                                                                "values": [
                                                                    str_attr("type", "text"),
                                                                    str_attr("content", "trace A"),
                                                                ],
                                                            },
                                                        },
                                                    ],
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        ],
                    },
                },
            },
        ],
    )
    span_b = make_span(
        span_id="22bb" * 4,
        trace_id="bbbb" * 8,
        attributes=[
            str_attr("gen_ai.operation.name", "chat"),
            str_attr("gen_ai.response.model", "claude-opus-4-7"),
            {
                "key": "gen_ai.output.messages",
                "value": {
                    "arrayValue": {
                        "values": [
                            {
                                "kvlistValue": {
                                    "values": [
                                        str_attr("role", "assistant"),
                                        {
                                            "key": "parts",
                                            "value": {
                                                "arrayValue": {
                                                    "values": [
                                                        {
                                                            "kvlistValue": {
                                                                "values": [
                                                                    str_attr("type", "text"),
                                                                    str_attr("content", "trace B"),
                                                                ],
                                                            },
                                                        },
                                                    ],
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        ],
                    },
                },
            },
        ],
    )

    resp = await api_client.post(
        "/api/otel/v1/traces", json=wrap_batch([span_a, span_b]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["created_trace_ids"]) == 2
    assert body["appended_trace_ids"] == []

    rows = await db.fetch(
        "SELECT id, events FROM traces WHERE id = ANY($1::uuid[]) "
        "ORDER BY events->0->>'text'",
        [uuid.UUID(t) for t in body["created_trace_ids"]],
    )
    assert len(rows) == 2
    texts = sorted(_decode_jsonb(r["events"])[0]["text"] for r in rows)
    assert texts == ["trace A", "trace B"]


async def test_ingested_trace_visible_via_get_trace_endpoint(
    api_client: httpx.AsyncClient,
) -> None:
    """An OTLP-ingested trace must be readable through the public read API."""
    resp = await api_client.post(
        "/api/otel/v1/traces", json=_case("12_end_to_end_agent_run"),
    )
    assert resp.status_code == 200
    trace_id = resp.json()["created_trace_ids"][0]

    detail = await api_client.get(f"/api/traces/{trace_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == trace_id
    # External traces have no workflow / iteration binding.
    assert body["workflow_id"] is None
    assert body["iteration_id"] is None
    # Events arrive shaped for the inspection UI.
    kinds = [e["type"] for e in body["events"]]
    assert kinds == ["content_delta", "tool_call_start", "tool_call_result"]


async def test_ingested_trace_appears_in_list_endpoint(
    api_client: httpx.AsyncClient,
) -> None:
    """The workspace `/api/traces` list view picks up ingested traces."""
    resp = await api_client.post(
        "/api/otel/v1/traces", json=_case("01_chat_basic_text"),
    )
    assert resp.status_code == 200
    trace_id = resp.json()["created_trace_ids"][0]

    listing = await api_client.get("/api/traces")
    assert listing.status_code == 200
    ids = {item["id"] for item in listing.json()["items"]}
    assert trace_id in ids


async def test_empty_batch_persists_nothing(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    """An empty resourceSpans payload decodes cleanly and writes no rows."""
    before = await db.fetchval("SELECT COUNT(*) FROM traces")
    resp = await api_client.post("/api/otel/v1/traces", json={"resourceSpans": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_events"] == 0
    assert body["created_trace_ids"] == []
    assert body["appended_trace_ids"] == []
    after = await db.fetchval("SELECT COUNT(*) FROM traces")
    assert after == before


async def test_unknown_op_does_not_create_row(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    """A batch where every span is unknown emits warnings but writes nothing."""
    before = await db.fetchval("SELECT COUNT(*) FROM traces")
    resp = await api_client.post(
        "/api/otel/v1/traces", json=_case("14_unknown_operation_skipped"),
    )
    assert resp.status_code == 200
    assert resp.json()["created_trace_ids"] == []
    after = await db.fetchval("SELECT COUNT(*) FROM traces")
    assert after == before


async def test_saturated_trace_is_dropped_with_response_signal(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    """When a trace's event count would exceed the per-trace cap, the
    batch is dropped at the persist layer and surfaced as
    `saturated_trace_ids` — the table stays at the cap, the response
    is truthful, and the caller can pause emission against the trace.

    To exercise this without pushing 10 000 events through the wire,
    we patch the cap down to 2 for the duration of the test and emit
    a 3-event run for one trace_id.
    """
    from ownevo_kernel.middleware.otel_receiver import persist as persist_mod

    original_cap = persist_mod._MAX_EVENTS_PER_TRACE
    persist_mod._MAX_EVENTS_PER_TRACE = 2
    try:
        # 3 events for one trace_id — exceeds the cap of 2.
        resp = await api_client.post(
            "/api/otel/v1/traces", json=_case("12_end_to_end_agent_run"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["created_trace_ids"] == []
        assert body["appended_trace_ids"] == []
        assert len(body["saturated_trace_ids"]) == 1
        saturated_id = uuid.UUID(body["saturated_trace_ids"][0])
        # The saturated trace must not have been written.
        row = await db.fetchrow(
            "SELECT 1 FROM traces WHERE id = $1", saturated_id,
        )
        assert row is None
    finally:
        persist_mod._MAX_EVENTS_PER_TRACE = original_cap


async def test_saturation_at_append_time_preserves_existing_row(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    """A second batch that would push an existing row past the cap is
    dropped — the original row's events array is preserved untouched.
    """
    from ownevo_kernel.middleware.otel_receiver import persist as persist_mod

    # First batch lands normally (3 events, well under any cap).
    resp1 = await api_client.post(
        "/api/otel/v1/traces", json=_case("12_end_to_end_agent_run"),
    )
    assert resp1.status_code == 200
    trace_id = uuid.UUID(resp1.json()["created_trace_ids"][0])
    events_before = _decode_jsonb(
        await db.fetchval("SELECT events FROM traces WHERE id = $1", trace_id),
    )
    assert len(events_before) == 3

    # Drop the cap to exactly the current row size; any append saturates.
    original_cap = persist_mod._MAX_EVENTS_PER_TRACE
    persist_mod._MAX_EVENTS_PER_TRACE = 3
    try:
        # Build a follow-up batch with the same OTel trace_id.
        otel_trace_hex = _case(
            "12_end_to_end_agent_run",
        )["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"]
        follow_up = make_span(
            span_id="cccc" * 4,
            trace_id=otel_trace_hex,
            name="gen_ai.execute_tool",
            attributes=[
                str_attr("gen_ai.operation.name", "execute_tool"),
                str_attr("gen_ai.tool.call.id", "saturating_call"),
                str_attr("gen_ai.tool.name", "x"),
                str_attr("gen_ai.tool.call.arguments", "{}"),
                str_attr("gen_ai.tool.call.result", "{}"),
            ],
            status_code=1,
        )
        resp2 = await api_client.post(
            "/api/otel/v1/traces", json=wrap_batch([follow_up]),
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["saturated_trace_ids"] == [str(trace_id)]
        assert body2["appended_trace_ids"] == []
    finally:
        persist_mod._MAX_EVENTS_PER_TRACE = original_cap

    # Existing row must be exactly as it was — no partial append.
    events_after = _decode_jsonb(
        await db.fetchval("SELECT events FROM traces WHERE id = $1", trace_id),
    )
    assert len(events_after) == 3
    assert events_after == events_before


async def test_persist_is_atomic_across_traces_on_failure(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    """A DB error mid-batch must roll back any earlier upsert in the
    same batch — no partial persistence leaks through.

    We force a failure by monkey-patching `_upsert_one_trace` to raise
    on the second trace, then assert that the first trace's row was
    not committed.
    """
    from ownevo_kernel.middleware.otel_receiver import persist as persist_mod

    span_a = make_span(
        span_id="aabb" * 4,
        trace_id="aa00" * 8,
        attributes=[
            str_attr("gen_ai.operation.name", "chat"),
            str_attr("gen_ai.response.model", "claude-opus-4-7"),
            {
                "key": "gen_ai.output.messages",
                "value": {
                    "arrayValue": {
                        "values": [
                            {
                                "kvlistValue": {
                                    "values": [
                                        str_attr("role", "assistant"),
                                        {
                                            "key": "parts",
                                            "value": {
                                                "arrayValue": {
                                                    "values": [
                                                        {
                                                            "kvlistValue": {
                                                                "values": [
                                                                    str_attr("type", "text"),
                                                                    str_attr("content", "first"),
                                                                ],
                                                            },
                                                        },
                                                    ],
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        ],
                    },
                },
            },
        ],
    )
    span_b = make_span(
        span_id="ccdd" * 4,
        trace_id="bb00" * 8,
        attributes=[
            str_attr("gen_ai.operation.name", "chat"),
            str_attr("gen_ai.response.model", "claude-opus-4-7"),
            {
                "key": "gen_ai.output.messages",
                "value": {
                    "arrayValue": {
                        "values": [
                            {
                                "kvlistValue": {
                                    "values": [
                                        str_attr("role", "assistant"),
                                        {
                                            "key": "parts",
                                            "value": {
                                                "arrayValue": {
                                                    "values": [
                                                        {
                                                            "kvlistValue": {
                                                                "values": [
                                                                    str_attr("type", "text"),
                                                                    str_attr("content", "second"),
                                                                ],
                                                            },
                                                        },
                                                    ],
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        ],
                    },
                },
            },
        ],
    )

    original_upsert = persist_mod._upsert_one_trace
    call_count = {"n": 0}

    async def flaky_upsert(conn: asyncpg.Connection, trace_id, events):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated DB failure on second trace")
        return await original_upsert(conn, trace_id, events)

    persist_mod._upsert_one_trace = flaky_upsert
    try:
        # ASGITransport propagates unhandled route exceptions to the
        # caller (Starlette's default ServerErrorMiddleware turns these
        # into 500 in production; the test transport re-raises). What
        # matters for atomicity is what landed on disk — asserted below.
        with pytest.raises(RuntimeError, match="simulated DB failure"):
            await api_client.post(
                "/api/otel/v1/traces", json=wrap_batch([span_a, span_b]),
            )
    finally:
        persist_mod._upsert_one_trace = original_upsert

    # The transaction rolled back; neither trace_id is on disk.
    count = await db.fetchval(
        "SELECT COUNT(*) FROM traces "
        "WHERE id IN ($1::uuid, $2::uuid)",
        uuid.UUID("aa00" * 8),
        uuid.UUID("bb00" * 8),
    )
    assert count == 0
