"""DB-backed tests for MCPClient — auth + transport + TTL caching end-to-end.

A fake transport records the headers it was handed (so we can assert auth was
resolved correctly) and returns canned tools/results. A fake token fetcher
exercises the OAuth-refresh path and lets us assert tokens are minted once and
then cached + persisted.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.mcp_client import (
    AuthKind,
    MCPClient,
    MCPServerRegistration,
    MCPTool,
    MCPToolResult,
    Transport,
    registry,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping mcp_client client tests",
)


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from ownevo_kernel.secrets import generate_master_key

    monkeypatch.setenv("OWNEVO_CREDENTIALS_MASTER_KEY", generate_master_key())


class _FakeTransport:
    def __init__(self) -> None:
        self.list_calls: list[dict] = []
        self.call_calls: list[dict] = []

    async def list_tools(self, *, endpoint_url, headers, transport):
        self.list_calls.append({"headers": dict(headers)})
        return [MCPTool(name="list_channels", description="", input_schema={})]

    async def call_tool(self, *, endpoint_url, headers, transport, tool_name, arguments):
        self.call_calls.append(
            {"headers": dict(headers), "tool_name": tool_name, "arguments": arguments}
        )
        return MCPToolResult(content={"ok": True, "tool": tool_name}, is_error=False)


class _FakeTokenFetcher:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list = []

    async def __call__(self, token_url, data):
        self.calls.append((token_url, data))
        return self.response


async def _register_bearer(db: asyncpg.Connection):
    return await registry.register_server(
        db,
        MCPServerRegistration(
            name="slack-bearer",
            provider="slack",
            endpoint_url="https://mcp.test/slack",
            transport=Transport.STREAMABLE_HTTP,
            auth_kind=AuthKind.BEARER,
            auth_secret={"token": "xoxb-1"},
        ),
    )


async def test_list_tools_passes_bearer_header(db: asyncpg.Connection) -> None:
    server = await _register_bearer(db)
    transport = _FakeTransport()
    client = MCPClient(transport=transport)
    tools = await client.list_tools(db, server.id)
    assert [t.name for t in tools] == ["list_channels"]
    assert transport.list_calls[0]["headers"] == {"Authorization": "Bearer xoxb-1"}


async def test_mcp_call_returns_result(db: asyncpg.Connection) -> None:
    server = await _register_bearer(db)
    transport = _FakeTransport()
    client = MCPClient(transport=transport)
    result = await client.mcp_call(db, server.id, "post_message", {"text": "hi"})
    assert isinstance(result, MCPToolResult)
    assert result.content == {"ok": True, "tool": "post_message"}
    assert transport.call_calls[0]["arguments"] == {"text": "hi"}


async def test_tools_cached_within_ttl(db: asyncpg.Connection) -> None:
    server = await _register_bearer(db)
    transport = _FakeTransport()
    client = MCPClient(transport=transport, ttl_seconds=300)
    await client.list_tools(db, server.id)
    await client.list_tools(db, server.id)
    assert len(transport.list_calls) == 1  # second call served from cache


async def test_oauth_refresh_mints_once_then_caches(db: asyncpg.Connection) -> None:
    now = datetime(2026, 5, 25, tzinfo=UTC)
    server = await registry.register_server(
        db,
        MCPServerRegistration(
            name="gws-oauth",
            provider="google_workspace",
            endpoint_url="https://mcp.test/gws",
            auth_kind=AuthKind.OAUTH,
            auth_config={"token_url": "https://oauth.test/token", "client_id": "c"},
            auth_secret={
                "access_token": "old",
                "expires_at": (now - timedelta(minutes=1)).isoformat(),
                "refresh_token": "rt",
            },
        ),
    )
    transport = _FakeTransport()
    fetcher = _FakeTokenFetcher(
        {"access_token": "minted", "expires_in": 3600, "refresh_token": "rt"}
    )
    client = MCPClient(transport=transport, token_fetcher=fetcher)

    await client.mcp_call(db, server.id, "read_sheet", {}, now=now)
    assert transport.call_calls[0]["headers"] == {"Authorization": "Bearer minted"}
    assert len(fetcher.calls) == 1

    # The minted token was persisted, so a fresh client (cold cache) reuses it
    # without minting again.
    persisted = await registry.get_auth_secret(db, server.id)
    assert persisted["access_token"] == "minted"

    fresh_client = MCPClient(transport=_FakeTransport(), token_fetcher=fetcher)
    await fresh_client.mcp_call(db, server.id, "read_sheet", {}, now=now + timedelta(minutes=1))
    assert len(fetcher.calls) == 1  # still one mint total


async def test_unknown_server_raises(db: asyncpg.Connection) -> None:
    from uuid import uuid4

    from ownevo_kernel.mcp_client import MCPServerNotFound

    client = MCPClient(transport=_FakeTransport())
    with pytest.raises(MCPServerNotFound):
        await client.list_tools(db, uuid4())
