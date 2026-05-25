"""Unit tests for the Power Platform Evaluation API client.

The `httpx` transport is mocked — one handler serves both the Entra
token mint and the test-set create, branching on path. We assert: the
happy path posts the eval cases and parses the test-set id; each error
status maps to its typed adapter error; the token cache mints once and
reuses; and `verify_connection` is a token-only round-trip.
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx", reason="httpx (api extra) not installed")

import httpx  # noqa: E402
from ownevo_kernel.middleware.copilot_studio import (  # noqa: E402
    CopilotStudioAuthError,
    CopilotStudioCredentials,
    CopilotStudioError,
    CopilotStudioNotFoundError,
    CopilotStudioRateLimitError,
    TokenCache,
    create_test_set,
    verify_connection,
)

_CREDS = CopilotStudioCredentials(
    tenant_id="tenant-1",
    client_id="client-1",
    client_secret="secret-1",
    environment_url="https://org.crm.dynamics.com",
)


def _client(handler) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _token_then(testsets_response):  # noqa: ANN001
    """Handler that mints a token, then serves `testsets_response` for testsets."""
    mint_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth2/v2.0/token"):
            mint_count["n"] += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if "testsets" in req.url.path:
            return testsets_response(req)
        return httpx.Response(404, text="unexpected path")

    handler.mint_count = mint_count  # type: ignore[attr-defined]
    return handler


async def test_create_test_set_happy_path() -> None:
    captured: dict = {}

    def testsets(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization")
        captured["body"] = req.content.decode()
        return httpx.Response(201, json={"id": "ts-42"})

    handler = _token_then(testsets)
    async with _client(handler) as c:
        result = await create_test_set(
            _CREDS,
            agent_id="agent-x",
            name="cluster-7 failures",
            cases=[{"input": "a", "expected_output": "b"}],
            http_client=c,
        )
    assert result.test_set_id == "ts-42"
    assert result.case_count == 1
    assert captured["auth"] == "Bearer tok"
    assert "agent-x" in captured["body"]


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, CopilotStudioAuthError),
        (403, CopilotStudioAuthError),
        (404, CopilotStudioNotFoundError),
        (429, CopilotStudioRateLimitError),
        (500, CopilotStudioError),
    ],
)
async def test_error_status_mapping(status_code: int, expected: type) -> None:
    def testsets(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": "nope"}})

    handler = _token_then(testsets)
    async with _client(handler) as c:
        with pytest.raises(expected):
            await create_test_set(
                _CREDS, agent_id="a", name="n", cases=[], http_client=c
            )


async def test_token_cache_mints_once_for_multiple_calls() -> None:
    def testsets(req: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "ts"})

    handler = _token_then(testsets)
    async with _client(handler) as c:
        cache = TokenCache(_CREDS, http_client=c)
        for _ in range(2):
            await create_test_set(
                _CREDS, agent_id="a", name="n", cases=[], token_cache=cache, http_client=c
            )
    # Two test-set creates, but the cache minted only one token.
    assert handler.mint_count["n"] == 1  # type: ignore[attr-defined]


async def test_verify_connection_is_token_only() -> None:
    hit: dict = {"testsets": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        hit["testsets"] = True
        return httpx.Response(200, json={})

    async with _client(handler) as c:
        await verify_connection(_CREDS, http_client=c)
    assert hit["testsets"] is False


async def test_verify_connection_propagates_auth_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    async with _client(handler) as c:
        with pytest.raises(CopilotStudioAuthError):
            await verify_connection(_CREDS, http_client=c)
