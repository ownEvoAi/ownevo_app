"""Unit tests for the Entra service-principal token exchange.

The `httpx` transport is mocked — no network, no Entra tenant. We assert:
the happy path returns a fresh token with a sane expiry; a rejected
service principal maps to `CopilotStudioAuthError`; a connection failure
maps to `CopilotStudioNetworkError`; and the request hits the right
endpoint with the client-credentials grant.
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx", reason="httpx (api extra) not installed")

import httpx  # noqa: E402
from ownevo_kernel.middleware.copilot_studio import (  # noqa: E402
    CopilotStudioAuthError,
    CopilotStudioError,
    CopilotStudioNetworkError,
    acquire_token,
)

_ENV_URL = "https://org.crm.dynamics.com"


def _client(handler) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _acquire(handler, **overrides):  # noqa: ANN001, ANN003
    kwargs = {
        "tenant_id": "tenant-1",
        "client_id": "client-1",
        "client_secret": "secret-1",
        "environment_url": _ENV_URL,
    }
    kwargs.update(overrides)
    async with _client(handler) as c:
        return await acquire_token(http_client=c, **kwargs)


async def test_acquire_token_happy_path() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = req.content.decode()
        return httpx.Response(200, json={"access_token": "tok-abc", "expires_in": 3600})

    tok = await _acquire(handler)
    assert tok.token == "tok-abc"
    assert tok.is_fresh()
    # client-credentials grant against the tenant token endpoint
    assert seen["path"] == "/tenant-1/oauth2/v2.0/token"
    assert "grant_type=client_credentials" in seen["body"]
    # scope is the environment .default
    assert "org.crm.dynamics.com%2F.default" in seen["body"]


async def test_expiry_applies_skew() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

    import time

    before = time.monotonic()
    tok = await _acquire(handler)
    # 3600s lifetime minus the 60s skew → deadline is < 3600s out.
    assert tok.expires_at < before + 3600
    assert tok.expires_at > before + 3000


async def test_rejected_principal_maps_to_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": "invalid_client", "error_description": "bad secret"},
        )

    with pytest.raises(CopilotStudioAuthError, match="bad secret"):
        await _acquire(handler)


async def test_connection_failure_maps_to_network_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure")

    with pytest.raises(CopilotStudioNetworkError):
        await _acquire(handler)


async def test_200_without_token_is_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 3600})

    with pytest.raises(CopilotStudioError):
        await _acquire(handler)


async def test_unexpected_status_is_generic_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server exploded")

    with pytest.raises(CopilotStudioError):
        await _acquire(handler)
