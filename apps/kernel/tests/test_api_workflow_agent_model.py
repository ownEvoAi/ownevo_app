"""Integration tests for GET /api/models + PATCH /api/workflows/{id}/agent-model.

Same in-process httpx + ASGITransport pattern as `test_api_workflows.py`.
Tests skip when `OWNEVO_DATABASE_URL` is unset so unit-only CI stays
green.
"""

from __future__ import annotations

import json
import os

import asyncpg
import httpx
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.llm import PROVIDERS

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# Derived from PROVIDERS so adding a new provider automatically covers cleanup.
_PROVIDER_ENVS = tuple(name for p in PROVIDERS for name in (p.enabled_env, p.models_env))


@pytest.fixture
def clean_provider_env(monkeypatch: pytest.MonkeyPatch):
    """Wipe every `OWNEVO_PROVIDER_*` env var so each test starts blank."""
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)


async def _seed_workflow(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    description: str = "Test workflow",
    agent_model_id: str | None = None,
) -> None:
    if agent_model_id is None:
        await conn.execute(
            "INSERT INTO workflows (id, description, spec, mode) "
            "VALUES ($1, $2, '{}'::jsonb, 'gated') "
            "ON CONFLICT DO NOTHING",
            workflow_id,
            description,
        )
    else:
        await conn.execute(
            "INSERT INTO workflows (id, description, spec, mode, agent_model_id) "
            "VALUES ($1, $2, '{}'::jsonb, 'gated', $3) "
            "ON CONFLICT DO NOTHING",
            workflow_id,
            description,
            agent_model_id,
        )


# ---------------------------------------------------------------------------
# GET /api/models
# ---------------------------------------------------------------------------


async def test_models_empty_when_no_providers_enabled(
    api_client: httpx.AsyncClient,
    clean_provider_env: None,
):
    res = await api_client.get("/api/models")
    assert res.status_code == 200
    assert res.json() == {"providers": []}


async def test_models_returns_enabled_providers_grouped(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    clean_provider_env: None,
):
    monkeypatch.setenv("OWNEVO_PROVIDER_ANTHROPIC_ENABLED", "true")
    monkeypatch.setenv(
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS",
        "claude-sonnet-4-6,claude-opus-4-7",
    )
    monkeypatch.setenv("OWNEVO_PROVIDER_FIREWORKS_ENABLED", "true")
    monkeypatch.setenv("OWNEVO_PROVIDER_FIREWORKS_MODELS", "kimi-k2p6")

    res = await api_client.get("/api/models")
    assert res.status_code == 200
    body = res.json()
    assert [p["id"] for p in body["providers"]] == ["anthropic", "fireworks"]
    assert body["providers"][0]["label"] == "Anthropic"
    assert body["providers"][0]["models"] == [
        "claude-sonnet-4-6",
        "claude-opus-4-7",
    ]
    assert body["providers"][1]["models"] == ["kimi-k2p6"]


# ---------------------------------------------------------------------------
# PATCH /api/workflows/{id}/agent-model
# ---------------------------------------------------------------------------


async def test_default_agent_model_id_is_sonnet(
    db: asyncpg.Connection,
    api_client: httpx.AsyncClient,
    clean_provider_env: None,
):
    """A newly-inserted workflow without an explicit model gets the default."""
    await _seed_workflow(db, workflow_id="wf-default")
    res = await api_client.get("/api/workflows/wf-default")
    assert res.status_code == 200
    assert res.json()["agent_model_id"] == "anthropic:claude-sonnet-4-6"


async def test_patch_agent_model_happy_path(
    db: asyncpg.Connection,
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    clean_provider_env: None,
):
    monkeypatch.setenv("OWNEVO_PROVIDER_ANTHROPIC_ENABLED", "true")
    monkeypatch.setenv(
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS",
        "claude-sonnet-4-6,claude-opus-4-7",
    )
    await _seed_workflow(db, workflow_id="wf-swap")

    res = await api_client.patch(
        "/api/workflows/wf-swap/agent-model",
        json={"agent_model_id": "anthropic:claude-opus-4-7"},
    )
    assert res.status_code == 200
    assert res.json()["agent_model_id"] == "anthropic:claude-opus-4-7"

    # Persisted on the row
    stored = await db.fetchval(
        "SELECT agent_model_id FROM workflows WHERE id = $1",
        "wf-swap",
    )
    assert stored == "anthropic:claude-opus-4-7"

    # Audit entry written
    audit_rows = await db.fetch(
        "SELECT kind, payload, actor "
        "FROM audit_entries "
        "WHERE kind = 'workflow-agent-model-changed' "
        "ORDER BY seq DESC LIMIT 1",
    )
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row["actor"] == "api:patch-agent-model"
    payload = json.loads(row["payload"])
    assert payload["workflow_id"] == "wf-swap"
    assert payload["agent_model_id"] == "anthropic:claude-opus-4-7"


async def test_patch_rejects_disabled_provider(
    db: asyncpg.Connection,
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    clean_provider_env: None,
):
    # Only Anthropic enabled — Fireworks must be rejected.
    monkeypatch.setenv("OWNEVO_PROVIDER_ANTHROPIC_ENABLED", "true")
    monkeypatch.setenv(
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS", "claude-sonnet-4-6"
    )
    await _seed_workflow(db, workflow_id="wf-blocked")

    res = await api_client.patch(
        "/api/workflows/wf-blocked/agent-model",
        json={"agent_model_id": "fireworks:kimi-k2p6"},
    )
    assert res.status_code == 400
    # Row unchanged
    stored = await db.fetchval(
        "SELECT agent_model_id FROM workflows WHERE id = $1",
        "wf-blocked",
    )
    assert stored == "anthropic:claude-sonnet-4-6"


async def test_patch_rejects_unknown_model_under_enabled_provider(
    db: asyncpg.Connection,
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    clean_provider_env: None,
):
    monkeypatch.setenv("OWNEVO_PROVIDER_ANTHROPIC_ENABLED", "true")
    monkeypatch.setenv(
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS", "claude-sonnet-4-6"
    )
    await _seed_workflow(db, workflow_id="wf-unknown")

    res = await api_client.patch(
        "/api/workflows/wf-unknown/agent-model",
        json={"agent_model_id": "anthropic:claude-haiku-4-5"},
    )
    assert res.status_code == 400


async def test_patch_rejects_malformed_slug(
    db: asyncpg.Connection,
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    clean_provider_env: None,
):
    monkeypatch.setenv("OWNEVO_PROVIDER_ANTHROPIC_ENABLED", "true")
    monkeypatch.setenv(
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS", "claude-sonnet-4-6"
    )
    await _seed_workflow(db, workflow_id="wf-bad")

    res = await api_client.patch(
        "/api/workflows/wf-bad/agent-model",
        json={"agent_model_id": "no-colon-here"},
    )
    assert res.status_code == 400


async def test_patch_404_on_unknown_workflow(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    clean_provider_env: None,
):
    monkeypatch.setenv("OWNEVO_PROVIDER_ANTHROPIC_ENABLED", "true")
    monkeypatch.setenv(
        "OWNEVO_PROVIDER_ANTHROPIC_MODELS", "claude-sonnet-4-6"
    )
    res = await api_client.patch(
        "/api/workflows/nope/agent-model",
        json={"agent_model_id": "anthropic:claude-sonnet-4-6"},
    )
    assert res.status_code == 404
