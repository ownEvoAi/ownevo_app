"""Integration tests for /api/integrations/langsmith.

The connection-test calls are mocked at `verify_api_key`; the rest
exercise the encrypted credential store end-to-end against a real DB.
A credentials master key is set per test.
"""

from __future__ import annotations

import os

import httpx
import pytest
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integrations tests",
)


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from ownevo_kernel.secrets import generate_master_key

    monkeypatch.setenv("OWNEVO_CREDENTIALS_MASTER_KEY", generate_master_key())


async def test_status_unconfigured(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.get("/api/integrations/langsmith")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["last_validated_at"] is None


async def test_set_then_status_configured(api_client: httpx.AsyncClient) -> None:
    set_resp = await api_client.post(
        "/api/integrations/langsmith", json={"api_key": "lsv2_pt_secret"}
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["configured"] is True

    get_resp = await api_client.get("/api/integrations/langsmith")
    assert get_resp.json()["configured"] is True


async def test_key_never_returned(api_client: httpx.AsyncClient) -> None:
    await api_client.post(
        "/api/integrations/langsmith", json={"api_key": "lsv2_pt_topsecret"}
    )
    body = await (await api_client.get("/api/integrations/langsmith")).aread()
    assert b"lsv2_pt_topsecret" not in body


async def test_empty_key_rejected(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.post(
        "/api/integrations/langsmith", json={"api_key": "   "}
    )
    assert resp.status_code == 422


async def test_delete_credential(api_client: httpx.AsyncClient) -> None:
    await api_client.post(
        "/api/integrations/langsmith", json={"api_key": "lsv2_pt_x"}
    )
    del_resp = await api_client.delete("/api/integrations/langsmith")
    assert del_resp.status_code == 204
    assert (await api_client.get("/api/integrations/langsmith")).json()["configured"] is False


async def test_delete_is_idempotent(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.delete("/api/integrations/langsmith")
    assert resp.status_code == 204


async def test_test_connection_404_when_unconfigured(
    api_client: httpx.AsyncClient,
) -> None:
    resp = await api_client.post("/api/integrations/langsmith/test")
    assert resp.status_code == 404


async def test_test_connection_ok(
    api_client: httpx.AsyncClient, db, monkeypatch: pytest.MonkeyPatch
) -> None:
    await api_client.post("/api/integrations/langsmith", json={"api_key": "lsv2_pt_x"})
    from ownevo_kernel.middleware import langsmith_push

    monkeypatch.setattr(langsmith_push, "verify_api_key", lambda **kw: None)

    resp = await api_client.post("/api/integrations/langsmith/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    # Status is stamped.
    assert (await api_client.get("/api/integrations/langsmith")).json()[
        "validation_status"
    ] == "ok"


async def test_test_connection_invalid_key(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await api_client.post("/api/integrations/langsmith", json={"api_key": "bad"})
    from ownevo_kernel.middleware import langsmith_push

    def _raise(**kw):
        raise langsmith_push.LangSmithAuthError("401")

    monkeypatch.setattr(langsmith_push, "verify_api_key", _raise)

    resp = await api_client.post("/api/integrations/langsmith/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "invalid"
