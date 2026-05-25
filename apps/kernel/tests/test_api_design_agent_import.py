"""HTTP tests for `POST /api/design-agent/import-next-question`.

Mounts the router on a bare FastAPI with a fake asyncpg pool so no real
DB is needed. With `ANTHROPIC_API_KEY` unset, the endpoint takes the
static-fallback path and walks the trace-import prompt set — this proves
the route wiring (request validation → trace load/summarise → fallback)
without an LLM.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from ownevo_kernel.api.routes.design_agent_import import router
from ownevo_kernel.design_agent import get_trace_import_discovery_questions

_TRACE_EVENTS = [
    {"type": "tool_call_start", "name": "forecast_demand", "args": {"sku": "A1"}},
    {"type": "tool_call_result", "name": "forecast_demand", "status": "ok",
     "output": {"units": 120}},
]


class _FakeConn:
    async def fetch(self, _query, trace_ids, _limit):
        # Echo back one stored row per requested id.
        return [{"id": tid, "events": _TRACE_EVENTS} for tid in trace_ids]


class _FakeAcquire:
    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def acquire(self):
        return _FakeAcquire()


@pytest.fixture
async def client(monkeypatch) -> AsyncGenerator[httpx.AsyncClient, None]:
    # Force the static-fallback path — no LLM call.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = FastAPI()
    app.include_router(router)
    app.state.pool = _FakePool()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c


async def test_first_question_via_static_fallback(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/design-agent/import-next-question",
        json={"trace_ids": [str(uuid4())], "prior_answers": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["done"] is False
    total = len(get_trace_import_discovery_questions())
    assert body["total_questions"] == total
    nq = body["next_question"]
    assert nq is not None
    # First trace-import prompt is the metric-negotiation question.
    assert nq["kind"] == "metric"
    assert isinstance(nq["options"], list) and nq["options"]


async def test_walk_to_done(client: httpx.AsyncClient):
    questions = get_trace_import_discovery_questions()
    prior: list[dict] = []
    for i in range(len(questions)):
        resp = await client.post(
            "/api/design-agent/import-next-question",
            json={"trace_ids": [str(uuid4())], "prior_answers": prior},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["done"] is False
        prior.append(
            {
                "question_index": i,
                "question": body["next_question"]["question"],
                "answer": "ok",
                "chosen_option": "ok",
            }
        )
    final = await client.post(
        "/api/design-agent/import-next-question",
        json={"trace_ids": [str(uuid4())], "prior_answers": prior},
    )
    assert final.json()["done"] is True


async def test_unknown_trace_ids_404(monkeypatch):
    # A pool that resolves no rows → 404.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    class _EmptyConn:
        async def fetch(self, *_):
            return []

    class _EmptyAcquire:
        async def __aenter__(self):
            return _EmptyConn()

        async def __aexit__(self, *exc):
            return False

    class _EmptyPool:
        def acquire(self):
            return _EmptyAcquire()

    app = FastAPI()
    app.include_router(router)
    app.state.pool = _EmptyPool()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        resp = await c.post(
            "/api/design-agent/import-next-question",
            json={"trace_ids": [str(uuid4())], "prior_answers": []},
        )
    assert resp.status_code == 404
