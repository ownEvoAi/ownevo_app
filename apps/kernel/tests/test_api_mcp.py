"""Integration tests for /api/mcp/servers against a real DB.

Exercises the encrypted server registry end-to-end. The secret is accepted on
register but never echoed back; "test connection" reports an error here since
no live MCP server is reachable in CI.
"""

from __future__ import annotations

import os

import httpx
import pytest
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping mcp API tests",
)


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from ownevo_kernel.secrets import generate_master_key

    monkeypatch.setenv("OWNEVO_CREDENTIALS_MASTER_KEY", generate_master_key())


_BODY = {
    "name": "acme-slack",
    "provider": "slack",
    "endpoint_url": "https://mcp.acme.test/slack",
    "auth_kind": "bearer",
    "auth_secret": {"token": "xoxb-secret"},
}


async def test_register_list_get_delete(api_client: httpx.AsyncClient) -> None:
    create = await api_client.post("/api/mcp/servers", json=_BODY)
    assert create.status_code == 201
    server = create.json()
    assert server["name"] == "acme-slack"
    assert server["has_secret"] is True
    # The plaintext secret must never come back over the wire.
    assert "auth_secret" not in server
    assert "xoxb-secret" not in create.text

    server_id = server["id"]
    listed = await api_client.get("/api/mcp/servers")
    assert listed.status_code == 200
    assert any(s["id"] == server_id for s in listed.json())

    got = await api_client.get(f"/api/mcp/servers/{server_id}")
    assert got.status_code == 200
    assert got.json()["provider"] == "slack"

    deleted = await api_client.delete(f"/api/mcp/servers/{server_id}")
    assert deleted.status_code == 204
    assert (await api_client.get(f"/api/mcp/servers/{server_id}")).status_code == 404


async def test_register_is_upsert_by_name(api_client: httpx.AsyncClient) -> None:
    first = await api_client.post("/api/mcp/servers", json=_BODY)
    updated = {**_BODY, "endpoint_url": "https://mcp.acme.test/slack-v2"}
    second = await api_client.post("/api/mcp/servers", json=updated)
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["endpoint_url"].endswith("slack-v2")


async def test_get_unknown_is_404(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.get("/api/mcp/servers/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


async def test_test_connection_records_error_without_live_server(
    api_client: httpx.AsyncClient,
) -> None:
    server_id = (await api_client.post("/api/mcp/servers", json=_BODY)).json()["id"]
    resp = await api_client.post(f"/api/mcp/servers/{server_id}/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    # The failed test is stamped on the row.
    status = (await api_client.get(f"/api/mcp/servers/{server_id}")).json()
    assert status["validation_status"] == "error"
