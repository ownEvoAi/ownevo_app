"""HTTP-level tests for `POST /api/otel/v1/traces`.

The route is DB-free (no persistence yet — landed in a follow-on
slice), so we mount just the otel_ingest router on a fresh FastAPI
instance and exercise it via the in-process httpx + ASGITransport
client. Same pattern as `test_api_nl_gen_preview.py`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from ownevo_kernel.api.routes.otel_ingest import router

from ._fixture_cases import CASES


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test",
    ) as c:
        yield c


async def test_well_formed_chat_batch_returns_200(client: httpx.AsyncClient) -> None:
    case = next(c for c in CASES if c.name == "01_chat_basic_text")
    resp = await client.post("/api/otel/v1/traces", json=case.payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_events"] == 1
    assert body["warnings"] == []


async def test_malformed_json_returns_400(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/otel/v1/traces",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


async def test_missing_resource_spans_returns_400(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/otel/v1/traces", json={"foo": "bar"})
    assert resp.status_code == 400


async def test_payload_array_returns_400(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/otel/v1/traces", json=[])
    assert resp.status_code == 400


async def test_oversize_payload_returns_413(client: httpx.AsyncClient) -> None:
    huge = b'{"resourceSpans":[]}' + b" " * (9 * 1024 * 1024)
    resp = await client.post(
        "/api/otel/v1/traces",
        content=huge,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413


async def test_unknown_op_surfaces_as_warning_not_error(
    client: httpx.AsyncClient,
) -> None:
    case = next(c for c in CASES if c.name == "14_unknown_operation_skipped")
    resp = await client.post("/api/otel/v1/traces", json=case.payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_events"] == 0
    assert len(body["warnings"]) >= 1


async def test_end_to_end_agent_run_returns_three_events(
    client: httpx.AsyncClient,
) -> None:
    case = next(c for c in CASES if c.name == "12_end_to_end_agent_run")
    resp = await client.post("/api/otel/v1/traces", json=case.payload)
    assert resp.status_code == 200
    body = resp.json()
    # content_delta + tool_call_start + tool_call_result
    assert body["accepted_events"] == 3


async def test_request_body_as_string_round_trips(client: httpx.AsyncClient) -> None:
    """The route reads raw bytes; passing serialised JSON as content works."""
    case = next(c for c in CASES if c.name == "05_tool_call_ok")
    resp = await client.post(
        "/api/otel/v1/traces",
        content=json.dumps(case.payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_events"] == 2  # tool start + result
