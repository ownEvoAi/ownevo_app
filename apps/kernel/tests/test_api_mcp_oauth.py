"""Integration tests for /api/mcp/oauth against a real DB.

The provider token endpoint is mocked by monkeypatching the route module's
`httpx_token_fetcher`, so the full start -> consent -> callback -> register
flow runs without a network or real OAuth app.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping mcp oauth API tests",
)


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from ownevo_kernel.secrets import generate_master_key

    monkeypatch.setenv("OWNEVO_CREDENTIALS_MASTER_KEY", generate_master_key())


async def test_lists_three_providers(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.get("/api/mcp/oauth/providers")
    assert resp.status_code == 200
    providers = {p["provider"] for p in resp.json()}
    assert providers == {"slack", "google_workspace", "microsoft_365"}


async def test_client_creds_set_get_delete(api_client: httpx.AsyncClient) -> None:
    get0 = await api_client.get("/api/mcp/oauth/slack/client")
    assert get0.json()["configured"] is False

    put = await api_client.put(
        "/api/mcp/oauth/slack/client",
        json={"client_id": "cid", "client_secret": "csec"},
    )
    assert put.status_code == 200
    assert put.json()["configured"] is True
    assert put.json()["client_id"] == "cid"
    assert "csec" not in put.text  # secret never echoed

    deleted = await api_client.delete("/api/mcp/oauth/slack/client")
    assert deleted.status_code == 204
    assert (await api_client.get("/api/mcp/oauth/slack/client")).json()["configured"] is False


async def test_start_requires_client_creds(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.post(
        "/api/mcp/oauth/slack/start", json={"server_name": "acme-slack"}
    )
    assert resp.status_code == 409


async def test_unknown_provider_is_404(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/api/mcp/oauth/dropbox/client")).status_code == 404


async def test_full_authorization_code_flow(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Configure the OAuth app credentials.
    await api_client.put(
        "/api/mcp/oauth/google_workspace/client",
        json={"client_id": "gcid", "client_secret": "gsec"},
    )

    # 2. Start: mint a state nonce + authorize URL.
    start = await api_client.post(
        "/api/mcp/oauth/google_workspace/start",
        json={"server_name": "acme-gws"},
    )
    assert start.status_code == 200
    authorize_url = start.json()["authorize_url"]
    state = parse_qs(urlparse(authorize_url).query)["state"][0]

    # 3. Mock the provider token endpoint the callback will hit.
    async def _fake_fetch(token_url, data):
        assert data["grant_type"] == "authorization_code"
        assert data["code"] == "the-code"
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

    monkeypatch.setattr(
        "ownevo_kernel.api.routes.mcp_oauth.httpx_token_fetcher", _fake_fetch
    )

    # 4. Provider redirects back to the callback.
    cb = await api_client.get(
        f"/api/mcp/oauth/google_workspace/callback?state={state}&code=the-code"
    )
    assert cb.status_code == 302
    assert "connected=1" in cb.headers["location"]

    # 5. The server is now registered.
    servers = (await api_client.get("/api/mcp/servers")).json()
    gws = [s for s in servers if s["name"] == "acme-gws"]
    assert len(gws) == 1
    assert gws[0]["provider"] == "google_workspace"
    assert gws[0]["auth_kind"] == "oauth"
    assert gws[0]["has_secret"] is True


async def test_callback_rejects_invalid_state(api_client: httpx.AsyncClient) -> None:
    cb = await api_client.get(
        "/api/mcp/oauth/slack/callback?state=bogus&code=x"
    )
    assert cb.status_code == 302
    assert "error=invalid_state" in cb.headers["location"]


async def test_callback_passes_through_provider_error(api_client: httpx.AsyncClient) -> None:
    cb = await api_client.get("/api/mcp/oauth/slack/callback?error=access_denied")
    assert cb.status_code == 302
    assert "error=access_denied" in cb.headers["location"]
