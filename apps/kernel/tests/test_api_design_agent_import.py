"""HTTP tests for the trace-import design-agent endpoints.

Tests both `POST /api/design-agent/import-next-question` (static-fallback
path exercised by unsetting ANTHROPIC_API_KEY) and
`POST /api/design-agent/import-generate` (LLM generators mocked at module
level to avoid real API calls).

A fake asyncpg pool is wired into app.state so no real DB is needed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from ownevo_kernel.api.routes.design_agent_import import router
from ownevo_kernel.design_agent import get_trace_import_discovery_questions
from ownevo_kernel.nl_gen.workflow_spec_from_traces import NoToolUseError

_TRACE_EVENTS = [
    {"type": "tool_call_start", "name": "forecast_demand", "args": {"sku": "A1"}},
    {"type": "tool_call_result", "name": "forecast_demand", "status": "ok",
     "output": {"units": 120}},
]


# ---------------------------------------------------------------------------
# Fake asyncpg plumbing
# ---------------------------------------------------------------------------

class _FakeConn:
    """Minimal asyncpg.Connection substitute used by the question endpoint."""

    async def fetch(self, _query: str, trace_ids: Any, _limit: Any) -> list[dict]:
        return [{"id": tid, "events": _TRACE_EVENTS} for tid in trace_ids]


class _FakeTransaction:
    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False


class _FakeGenerateConn:
    """Connection substitute used by the generate endpoint.

    Supports fetch (trace load), fetchrow (workflow INSERT), and a
    transaction() context manager. `fetchrow_result` controls whether the
    INSERT appears to succeed (returns a row) or conflict (returns None).
    """

    _MISSING = object()  # sentinel so callers can explicitly pass None

    def __init__(self, fetchrow_result: dict | None = _MISSING) -> None:  # type: ignore[assignment]
        self._fetchrow_result = (
            {"id": "test-workflow"}
            if fetchrow_result is self.__class__._MISSING
            else fetchrow_result
        )

    async def fetch(self, _query: str, trace_ids: Any, _limit: Any) -> list[dict]:
        return [{"id": tid, "events": _TRACE_EVENTS} for tid in trace_ids]

    async def fetchrow(self, *_args: Any) -> dict | None:
        return self._fetchrow_result

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakeAcquire:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *_: Any) -> bool:
        return False


class _FakePool:
    """Question-endpoint pool: returns a plain _FakeConn on every acquire."""

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(_FakeConn())


class _FakeGeneratePool:
    """Generate-endpoint pool: returns a _FakeGenerateConn on every acquire."""

    def __init__(self, fetchrow_result: dict | None = None) -> None:
        self._conn = _FakeGenerateConn(fetchrow_result)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


# ---------------------------------------------------------------------------
# Fake workflow artifacts — stand-ins for WorkflowSpec / SimPlan / Metric
# ---------------------------------------------------------------------------

@dataclass
class _FakeSpec:
    id: str = "test-workflow"

    def model_dump_json(self) -> str:
        return json.dumps({"id": self.id})

    def model_dump(self) -> dict:
        return {"id": self.id}


@dataclass
class _FakePlan:
    def model_dump_json(self) -> str:
        return json.dumps({})


@dataclass
class _FakeMetric:
    def model_dump_json(self) -> str:
        return json.dumps({})


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


# ---------------------------------------------------------------------------
# POST /api/design-agent/import-summary tests
# ---------------------------------------------------------------------------


async def test_import_summary_fallback_without_llm(client: httpx.AsyncClient):
    """No ANTHROPIC_API_KEY → deterministic fallback summary."""
    resp = await client.post(
        "/api/design-agent/import-summary",
        json={"trace_ids": [str(uuid4())]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert body["basis"] == "traces"
    # Fallback names the observed tool from _TRACE_EVENTS.
    assert "forecast_demand" in body["summary"]


async def test_import_summary_basis_with_definition(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/design-agent/import-summary",
        json={
            "trace_ids": [str(uuid4())],
            "agent_definition": "You are a demand-planning assistant.",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["basis"] == "definition+traces"


async def test_import_summary_unknown_trace_ids_404(monkeypatch):
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
            "/api/design-agent/import-summary",
            json={"trace_ids": [str(uuid4())]},
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/design-agent/import-generate tests
# ---------------------------------------------------------------------------

def _gen_app(pool: Any) -> FastAPI:
    """Bare FastAPI with the import router and a fake pool."""
    app = FastAPI()
    app.include_router(router)
    app.state.pool = pool
    return app


async def test_import_generate_503_when_no_api_key(monkeypatch):
    """Endpoint returns 503 when ANTHROPIC_API_KEY is absent."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = _gen_app(_FakeGeneratePool())
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        resp = await c.post(
            "/api/design-agent/import-generate",
            json={"trace_ids": [str(uuid4())]},
        )
    assert resp.status_code == 503
    # Must NOT expose the env-var name in the response body.
    assert "ANTHROPIC_API_KEY" not in resp.text


async def test_import_generate_502_on_llm_failure(monkeypatch):
    """Endpoint returns 502 when the spec generator raises NoToolUseError."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.build_async_anthropic",
        lambda _key: object(),  # client stub — not called directly by route
    )

    async def _raise_no_tool(*_args: Any, **_kwargs: Any) -> None:
        raise NoToolUseError(
            "no tool call",
            stop_reason="end_turn",
            content_preview="",
        )

    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.generate_workflow_spec_from_traces",
        _raise_no_tool,
    )

    app = _gen_app(_FakeGeneratePool())
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        resp = await c.post(
            "/api/design-agent/import-generate",
            json={"trace_ids": [str(uuid4())]},
        )
    assert resp.status_code == 502


async def test_import_generate_409_on_workflow_conflict(monkeypatch):
    """Endpoint returns 409 when the workflow_id already exists in the DB."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.build_async_anthropic",
        lambda _key: object(),
    )

    async def _ok_spec(*_a: Any, **_kw: Any) -> _FakeSpec:
        return _FakeSpec()

    async def _ok_plan(*_a: Any, **_kw: Any) -> _FakePlan:
        return _FakePlan()

    async def _ok_metric(*_a: Any, **_kw: Any) -> _FakeMetric:
        return _FakeMetric()

    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.generate_workflow_spec_from_traces",
        _ok_spec,
    )
    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.generate_simulation_plan",
        _ok_plan,
    )
    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.generate_metric_definition",
        _ok_metric,
    )

    # fetchrow returns None → ON CONFLICT DO NOTHING fired, workflow exists.
    app = _gen_app(_FakeGeneratePool(fetchrow_result=None))
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        resp = await c.post(
            "/api/design-agent/import-generate",
            json={"trace_ids": [str(uuid4())]},
        )
    assert resp.status_code == 409


async def test_import_generate_happy_path(monkeypatch):
    """Happy path: returns workflow_id, description, and spec dict."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.build_async_anthropic",
        lambda _key: object(),
    )

    async def _ok_spec(*_a: Any, **_kw: Any) -> _FakeSpec:
        return _FakeSpec(id="demand-forecast")

    async def _ok_plan(*_a: Any, **_kw: Any) -> _FakePlan:
        return _FakePlan()

    async def _ok_metric(*_a: Any, **_kw: Any) -> _FakeMetric:
        return _FakeMetric()

    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.generate_workflow_spec_from_traces",
        _ok_spec,
    )
    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.generate_simulation_plan",
        _ok_plan,
    )
    monkeypatch.setattr(
        "ownevo_kernel.api.routes.design_agent_import.generate_metric_definition",
        _ok_metric,
    )

    app = _gen_app(_FakeGeneratePool(fetchrow_result={"id": "demand-forecast"}))
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        resp = await c.post(
            "/api/design-agent/import-generate",
            json={"trace_ids": [str(uuid4())]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_id"] == "demand-forecast"
    assert "description" in body
    assert isinstance(body["spec"], dict)
