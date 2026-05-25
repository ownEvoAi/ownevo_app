"""Integration tests for POST /api/workflows/{id}/push-eval-cases-copilot-studio.

The Power Platform Evaluation API can't be reached without a Microsoft
tenant, so `create_test_set` (the adapter) and the credential loader are
monkeypatched. The assertions target the route's preconditions, the
ownEvo-eval-case -> {input, expected_output} mapping it hands the adapter,
and the audit entry it writes on success.
"""

from __future__ import annotations

import json
import os

import asyncpg
import httpx
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.middleware.copilot_studio import (
    CopilotStudioAuthError,
    CopilotStudioCredentials,
    CopilotStudioError,
)
# Aliased so pytest doesn't try to collect the dataclass as a test class.
from ownevo_kernel.middleware.copilot_studio.evaluation_api import (
    TestSetResult as _TestSetResult,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping push-eval-cases-copilot-studio tests",
)

_DUMMY_CRED = CopilotStudioCredentials(
    tenant_id="t",
    client_id="c",
    client_secret="s",
    environment_url="https://org.crm.dynamics.com",
)


async def _seed_workflow(
    db: asyncpg.Connection,
    *,
    wf_id: str = "wf-cs-evalpush",
    origin: str | None = "copilot_studio",
) -> None:
    await db.execute(
        "INSERT INTO workflows (id, description, spec, origin) "
        "VALUES ($1, 'cs eval-push test', '{}'::jsonb, $2)",
        wf_id,
        origin,
    )


async def _seed_case(
    db: asyncpg.Connection,
    wf_id: str,
    *,
    input_payload: dict,
    expected: dict,
    cluster_id=None,
    is_test_fold: bool = False,
) -> None:
    await db.execute(
        """
        INSERT INTO eval_cases (workflow_id, provenance, cluster_id,
                                input, expected_behavior, is_test_fold)
        VALUES ($1, 'cluster-derived', $2, $3::jsonb, $4::jsonb, $5)
        """,
        wf_id,
        cluster_id,
        json.dumps(input_payload),
        json.dumps(expected),
        is_test_fold,
    )


def _patch_credential(monkeypatch) -> None:
    async def _fake_load(_conn):  # noqa: ANN001, ANN202
        return _DUMMY_CRED

    monkeypatch.setattr(
        "ownevo_kernel.api.routes.integrations.load_copilot_credential_or_raise",
        _fake_load,
    )


def _patch_create_test_set(monkeypatch, *, captured: list) -> None:
    async def _fake_create(cred, *, agent_id, name, cases, **_kw):  # noqa: ANN001, ANN202
        captured.append({"agent_id": agent_id, "name": name, "cases": list(cases)})
        return _TestSetResult(test_set_id="ts-123", case_count=len(list(cases)))

    monkeypatch.setattr(
        "ownevo_kernel.middleware.copilot_studio.create_test_set",
        _fake_create,
    )


async def test_push_happy_path_maps_cases_and_writes_audit(
    api_client: httpx.AsyncClient, db: asyncpg.Connection, monkeypatch
) -> None:
    await _seed_workflow(db)
    await _seed_case(
        db, "wf-cs-evalpush", input_payload={"q": "hi"}, expected={"a": "hello"}
    )
    await _seed_case(
        db, "wf-cs-evalpush", input_payload={"q": "bye"}, expected={"a": "goodbye"}
    )
    _patch_credential(monkeypatch)
    captured: list = []
    _patch_create_test_set(monkeypatch, captured=captured)

    resp = await api_client.post(
        "/api/workflows/wf-cs-evalpush/push-eval-cases-copilot-studio",
        json={"agent_id": "agent-xyz"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["test_set_id"] == "ts-123"
    assert body["case_count"] == 2
    assert body["workflow_id"] == "wf-cs-evalpush"

    # The adapter received cases shaped as {input, expected_output}.
    assert len(captured) == 1
    sent = captured[0]
    assert sent["agent_id"] == "agent-xyz"
    assert {"input", "expected_output"} == set(sent["cases"][0].keys())
    assert sent["cases"][0]["input"] == {"q": "hi"}
    assert sent["cases"][0]["expected_output"] == {"a": "hello"}

    audit = await db.fetchrow(
        "SELECT payload FROM audit_entries "
        "WHERE kind = 'eval-cases-pushed-copilot-studio' "
        "ORDER BY created_at DESC LIMIT 1"
    )
    assert audit is not None
    raw = audit["payload"]
    payload = json.loads(raw) if isinstance(raw, str) else raw
    assert payload["workflow_id"] == "wf-cs-evalpush"
    assert payload["case_count"] == 2
    assert payload["test_set_id"] == "ts-123"


async def test_push_cluster_filter_only_pushes_cluster_cases(
    api_client: httpx.AsyncClient, db: asyncpg.Connection, monkeypatch
) -> None:
    await _seed_workflow(db, wf_id="wf-cs-cluster")
    cluster_id = await db.fetchval(
        """
        INSERT INTO failure_clusters (workflow_id, label, severity, cluster_size)
        VALUES ('wf-cs-cluster', 'holiday misses', 'high', 1)
        RETURNING id
        """
    )
    await _seed_case(
        db, "wf-cs-cluster", input_payload={"q": "in"}, expected={"a": "1"},
        cluster_id=cluster_id,
    )
    await _seed_case(
        db, "wf-cs-cluster", input_payload={"q": "out"}, expected={"a": "2"}
    )
    _patch_credential(monkeypatch)
    captured: list = []
    _patch_create_test_set(monkeypatch, captured=captured)

    resp = await api_client.post(
        "/api/workflows/wf-cs-cluster/push-eval-cases-copilot-studio",
        json={"agent_id": "a", "cluster_id": str(cluster_id)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["case_count"] == 1
    assert captured[0]["cases"][0]["input"] == {"q": "in"}

    # cluster-scoped push anchors the audit row's related_id to the cluster.
    related = await db.fetchval(
        "SELECT related_id FROM audit_entries "
        "WHERE kind = 'eval-cases-pushed-copilot-studio' "
        "ORDER BY created_at DESC LIMIT 1"
    )
    assert str(related) == str(cluster_id)


async def test_push_404_unknown_workflow(
    api_client: httpx.AsyncClient, monkeypatch
) -> None:
    _patch_credential(monkeypatch)
    _patch_create_test_set(monkeypatch, captured=[])
    resp = await api_client.post(
        "/api/workflows/nope/push-eval-cases-copilot-studio",
        json={"agent_id": "a"},
    )
    assert resp.status_code == 404


async def test_push_422_non_copilot_origin(
    api_client: httpx.AsyncClient, db: asyncpg.Connection, monkeypatch
) -> None:
    await _seed_workflow(db, wf_id="wf-cs-greenfield", origin=None)
    await _seed_case(
        db, "wf-cs-greenfield", input_payload={"q": "x"}, expected={"a": "y"}
    )
    _patch_credential(monkeypatch)
    _patch_create_test_set(monkeypatch, captured=[])
    resp = await api_client.post(
        "/api/workflows/wf-cs-greenfield/push-eval-cases-copilot-studio",
        json={"agent_id": "a"},
    )
    assert resp.status_code == 422


async def test_push_422_no_eval_cases(
    api_client: httpx.AsyncClient, db: asyncpg.Connection, monkeypatch
) -> None:
    await _seed_workflow(db, wf_id="wf-cs-empty")
    _patch_credential(monkeypatch)
    _patch_create_test_set(monkeypatch, captured=[])
    resp = await api_client.post(
        "/api/workflows/wf-cs-empty/push-eval-cases-copilot-studio",
        json={"agent_id": "a"},
    )
    assert resp.status_code == 422


async def test_push_adapter_error_maps_to_502(
    api_client: httpx.AsyncClient, db: asyncpg.Connection, monkeypatch
) -> None:
    await _seed_workflow(db, wf_id="wf-cs-err")
    await _seed_case(db, "wf-cs-err", input_payload={"q": "x"}, expected={"a": "y"})
    _patch_credential(monkeypatch)

    async def _boom(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
        raise CopilotStudioError("upstream blew up")

    monkeypatch.setattr(
        "ownevo_kernel.middleware.copilot_studio.create_test_set", _boom
    )
    resp = await api_client.post(
        "/api/workflows/wf-cs-err/push-eval-cases-copilot-studio",
        json={"agent_id": "a"},
    )
    assert resp.status_code == 502


async def test_push_auth_error_maps_to_401(
    api_client: httpx.AsyncClient, db: asyncpg.Connection, monkeypatch
) -> None:
    await _seed_workflow(db, wf_id="wf-cs-auth")
    await _seed_case(db, "wf-cs-auth", input_payload={"q": "x"}, expected={"a": "y"})
    _patch_credential(monkeypatch)

    async def _denied(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
        raise CopilotStudioAuthError("bad token")

    monkeypatch.setattr(
        "ownevo_kernel.middleware.copilot_studio.create_test_set", _denied
    )
    resp = await api_client.post(
        "/api/workflows/wf-cs-auth/push-eval-cases-copilot-studio",
        json={"agent_id": "a"},
    )
    assert resp.status_code == 401
