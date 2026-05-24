"""HTTP-level tests for `POST /api/otel/v1/traces`.

The route resolves `ConnDep` (a per-request asyncpg connection) before
the handler runs, so even the 4xx-path tests need a real pool wired
through the FastAPI app. We use the shared `api_client` fixture from
`conftest.py` — same `create_app(pool=...)` plumbing as the rest of
the API surface, skipped automatically when `OWNEVO_DATABASE_URL` is
unset.

Persistence-shape assertions live in `test_route_otel_ingest_persist.py`;
this file focuses on HTTP-level semantics (status codes + response
envelope shape) so the two concerns stay legible separately.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.middleware.otel_receiver import DEFAULT_MAX_BODY_BYTES

from ._fixture_cases import CASES

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


async def test_well_formed_chat_batch_returns_200(api_client: httpx.AsyncClient) -> None:
    case = next(c for c in CASES if c.name == "01_chat_basic_text")
    resp = await api_client.post("/api/otel/v1/traces", json=case.payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_events"] == 1
    assert body["warnings"] == []
    assert len(body["created_trace_ids"]) == 1
    assert body["appended_trace_ids"] == []
    assert body["saturated_trace_ids"] == []


async def test_malformed_json_returns_400(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.post(
        "/api/otel/v1/traces",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


async def test_missing_resource_spans_returns_400(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.post("/api/otel/v1/traces", json={"foo": "bar"})
    assert resp.status_code == 400


async def test_payload_array_returns_400(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.post("/api/otel/v1/traces", json=[])
    assert resp.status_code == 400


async def test_oversize_payload_returns_413(api_client: httpx.AsyncClient) -> None:
    huge = b'{"resourceSpans":[]}' + b" " * (DEFAULT_MAX_BODY_BYTES + 1024 * 1024)
    resp = await api_client.post(
        "/api/otel/v1/traces",
        content=huge,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413


async def test_unknown_op_surfaces_as_warning_not_error(
    api_client: httpx.AsyncClient,
) -> None:
    case = next(c for c in CASES if c.name == "14_unknown_operation_skipped")
    resp = await api_client.post("/api/otel/v1/traces", json=case.payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_events"] == 0
    assert len(body["warnings"]) >= 1
    # Zero events → no trace rows touched.
    assert body["created_trace_ids"] == []
    assert body["appended_trace_ids"] == []
    assert body["saturated_trace_ids"] == []


async def test_end_to_end_agent_run_returns_three_events(
    api_client: httpx.AsyncClient,
) -> None:
    case = next(c for c in CASES if c.name == "12_end_to_end_agent_run")
    resp = await api_client.post("/api/otel/v1/traces", json=case.payload)
    assert resp.status_code == 200
    body = resp.json()
    # content_delta + tool_call_start + tool_call_result
    assert body["accepted_events"] == 3


async def test_request_body_as_string_round_trips(api_client: httpx.AsyncClient) -> None:
    """The route reads raw bytes; passing serialised JSON as content works."""
    case = next(c for c in CASES if c.name == "05_tool_call_ok")
    resp = await api_client.post(
        "/api/otel/v1/traces",
        content=json.dumps(case.payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_events"] == 2  # tool start + result
