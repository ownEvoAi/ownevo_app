"""Integration tests for the `/api/agents` registry surface."""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.agents import register_agent_for_workflow
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


async def _seed_workflow(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    description: str = "desc",
    origin: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO workflows (id, description, spec, mode, origin)
        VALUES ($1, $2, '{}'::jsonb, 'gated'::workflow_mode, $3)
        ON CONFLICT (id) DO NOTHING
        """,
        workflow_id,
        description,
        origin,
    )


async def test_list_agents_returns_registered(
    db: asyncpg.Connection, api_client
) -> None:
    await _seed_workflow(db, workflow_id="wf-a", description="Forecast demand")
    await _seed_workflow(db, workflow_id="wf-b", origin="langsmith")
    await register_agent_for_workflow(db, "wf-a")
    await register_agent_for_workflow(db, "wf-b")

    resp = await api_client.get("/api/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    by_wf = {a["workflow_id"]: a for a in body["items"]}
    assert by_wf["wf-a"]["name"] == "Forecast demand"
    assert by_wf["wf-a"]["origin"] == "greenfield"
    assert by_wf["wf-b"]["origin"] == "langsmith"
    # Identity surfaced as a string; all seven display fields present.
    assert isinstance(by_wf["wf-a"]["identity_hash"], str)
    for field in (
        "status",
        "owner",
        "last_iteration_at",
        "eval_coverage_count",
        "iteration_count",
        "created_at",
    ):
        assert field in by_wf["wf-a"]


async def test_get_agent_detail_and_404(db: asyncpg.Connection, api_client) -> None:
    await _seed_workflow(db, workflow_id="wf-x")
    agent = await register_agent_for_workflow(db, "wf-x")
    assert agent is not None

    ok = await api_client.get(f"/api/agents/{agent.id}")
    assert ok.status_code == 200
    assert ok.json()["workflow_id"] == "wf-x"

    import uuid

    missing = await api_client.get(f"/api/agents/{uuid.uuid4()}")
    assert missing.status_code == 404


async def test_patch_status(db: asyncpg.Connection, api_client) -> None:
    await _seed_workflow(db, workflow_id="wf-p")
    agent = await register_agent_for_workflow(db, "wf-p")
    assert agent is not None

    resp = await api_client.patch(
        f"/api/agents/{agent.id}/status", json={"status": "paused"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


async def test_patch_status_unknown_agent_404(api_client) -> None:
    import uuid

    resp = await api_client.patch(
        f"/api/agents/{uuid.uuid4()}/status", json={"status": "archived"}
    )
    assert resp.status_code == 404


async def test_patch_status_rejects_bad_value(
    db: asyncpg.Connection, api_client
) -> None:
    await _seed_workflow(db, workflow_id="wf-bad")
    agent = await register_agent_for_workflow(db, "wf-bad")
    assert agent is not None

    resp = await api_client.patch(
        f"/api/agents/{agent.id}/status", json={"status": "nonsense"}
    )
    assert resp.status_code == 422


async def test_filter_by_status_query(db: asyncpg.Connection, api_client) -> None:
    await _seed_workflow(db, workflow_id="wf-active")
    await _seed_workflow(db, workflow_id="wf-paused")
    await register_agent_for_workflow(db, "wf-active")
    paused = await register_agent_for_workflow(db, "wf-paused")
    assert paused is not None
    await api_client.patch(
        f"/api/agents/{paused.id}/status", json={"status": "paused"}
    )

    resp = await api_client.get("/api/agents", params={"status": "paused"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [a["workflow_id"] for a in items] == ["wf-paused"]


async def test_filter_by_origin_query(db: asyncpg.Connection, api_client) -> None:
    await _seed_workflow(db, workflow_id="wf-gf")
    await _seed_workflow(db, workflow_id="wf-ls", origin="langsmith")
    await register_agent_for_workflow(db, "wf-gf")
    await register_agent_for_workflow(db, "wf-ls")

    resp = await api_client.get("/api/agents", params={"origin": "langsmith"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [a["workflow_id"] for a in items] == ["wf-ls"]


async def test_filter_invalid_origin_returns_422(api_client) -> None:
    resp = await api_client.get("/api/agents", params={"origin": "bogus"})
    assert resp.status_code == 422


async def test_filter_invalid_status_returns_422(api_client) -> None:
    resp = await api_client.get("/api/agents", params={"status": "bogus"})
    assert resp.status_code == 422


async def test_patch_status_blocked_in_demo_mode(
    db: asyncpg.Connection,
    api_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH /api/agents/:id/status returns 503 when DEMO_MODE is active."""
    await _seed_workflow(db, workflow_id="wf-demo")
    agent = await register_agent_for_workflow(db, "wf-demo")
    assert agent is not None

    monkeypatch.setenv("DEMO_MODE", "true")
    resp = await api_client.patch(
        f"/api/agents/{agent.id}/status", json={"status": "paused"}
    )
    assert resp.status_code == 503
