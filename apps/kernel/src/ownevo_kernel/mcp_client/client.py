"""The MCP-client framework's entry point for the rest of the kernel.

`MCPClient` ties together the registry (where servers live), auth resolution
(how to authenticate), and a transport (how to reach the wire). It exposes the
two primitives the plan calls for — `list_tools(server_id)` and
`mcp_call(server_id, tool_name, args)` — and caches resolved auth + tool
listings per server with a TTL so a burst of calls inside one agent turn
doesn't re-authenticate or re-list on every step.

Token expiry takes precedence over the TTL: a cache entry never outlives the
access token it was minted against. Tokens minted/refreshed during resolution
are persisted back through the registry so they survive process restarts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from . import registry
from .auth import TokenFetcher, httpx_token_fetcher, resolve_auth
from .models import MCPServer, MCPTool, MCPToolResult
from .transport import MCPTransport, SdkStreamableHttpTransport

if TYPE_CHECKING:
    import asyncpg

_DEFAULT_TTL_SECONDS = 300


class MCPServerNotFound(LookupError):
    """No `mcp_servers` row matches the given id."""


@dataclass
class _CachedSession:
    headers: dict[str, str]
    tools: list[MCPTool] | None
    expires_at: datetime


class MCPClient:
    """Authenticated access to registered MCP servers.

    One instance can be shared across an agent run; the per-server cache makes
    repeated `mcp_call`s cheap. Pass a `FakeTransport` (and a fake
    `token_fetcher`) in tests to exercise the full path without a network.
    """

    def __init__(
        self,
        *,
        transport: MCPTransport | None = None,
        token_fetcher: TokenFetcher | None = None,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._transport = transport or SdkStreamableHttpTransport()
        self._token_fetcher = token_fetcher or httpx_token_fetcher
        self._ttl = timedelta(seconds=ttl_seconds)
        self._cache: dict[UUID, _CachedSession] = {}

    def invalidate(self, server_id: UUID) -> None:
        """Drop any cached session for a server (e.g. after a config change)."""
        self._cache.pop(server_id, None)

    async def _require_server(
        self, conn: asyncpg.Connection, server_id: UUID
    ) -> MCPServer:
        server = await registry.get_server(conn, server_id)
        if server is None:
            raise MCPServerNotFound(f"no MCP server registered with id {server_id}")
        return server

    async def _resolve_headers(
        self, conn: asyncpg.Connection, server: MCPServer, *, now: datetime
    ) -> dict[str, str]:
        """Resolve (and cache) auth headers, persisting any minted token."""
        secret = await registry.get_auth_secret(conn, server.id)
        resolved = await resolve_auth(
            auth_kind=server.auth_kind,
            auth_config=server.auth_config,
            secret=secret,
            token_fetcher=self._token_fetcher,
            now=now,
        )
        if resolved.refreshed_secret is not None:
            await registry.update_auth_secret(conn, server.id, resolved.refreshed_secret)
            secret = resolved.refreshed_secret
        self._cache[server.id] = _CachedSession(
            headers=resolved.headers,
            tools=None,
            expires_at=self._cache_expiry(secret, now),
        )
        return resolved.headers

    def _cache_expiry(self, secret: dict[str, Any] | None, now: datetime) -> datetime:
        """Cache lives for the TTL, but never past the token's own expiry."""
        ttl_expiry = now + self._ttl
        raw = (secret or {}).get("expires_at")
        if isinstance(raw, str):
            try:
                token_expiry = datetime.fromisoformat(raw)
            except ValueError:
                return ttl_expiry
            if token_expiry.tzinfo is None:
                token_expiry = token_expiry.replace(tzinfo=UTC)
            return min(ttl_expiry, token_expiry)
        return ttl_expiry

    async def _headers(
        self, conn: asyncpg.Connection, server: MCPServer, *, now: datetime
    ) -> dict[str, str]:
        cached = self._cache.get(server.id)
        if cached is not None and now < cached.expires_at:
            return cached.headers
        return await self._resolve_headers(conn, server, now=now)

    async def list_tools(
        self,
        conn: asyncpg.Connection,
        server_id: UUID,
        *,
        now: datetime | None = None,
    ) -> list[MCPTool]:
        """List the tools a connected server advertises (cached for the TTL)."""
        current = now or datetime.now(UTC)
        server = await self._require_server(conn, server_id)
        cached = self._cache.get(server_id)
        if cached is not None and cached.tools is not None and current < cached.expires_at:
            return cached.tools
        headers = await self._headers(conn, server, now=current)
        tools = await self._transport.list_tools(
            endpoint_url=server.endpoint_url,
            headers=headers,
            transport=server.transport,
        )
        entry = self._cache.get(server_id)
        if entry is not None:
            entry.tools = tools
        return tools

    async def mcp_call(
        self,
        conn: asyncpg.Connection,
        server_id: UUID,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> MCPToolResult:
        """Invoke `tool_name` on a connected server with `arguments`.

        Transport and auth failures raise; the caller (agent dispatch) shapes
        those into the agent's tool_call_result error event so they cluster
        like any other failed tool call.
        """
        current = now or datetime.now(UTC)
        server = await self._require_server(conn, server_id)
        headers = await self._headers(conn, server, now=current)
        return await self._transport.call_tool(
            endpoint_url=server.endpoint_url,
            headers=headers,
            transport=server.transport,
            tool_name=tool_name,
            arguments=arguments,
        )


__all__ = ["MCPClient", "MCPServerNotFound"]
