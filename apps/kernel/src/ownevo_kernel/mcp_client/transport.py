"""The wire boundary to a remote MCP server.

`MCPTransport` is a Protocol so the rest of the framework — registry, auth,
the agent dispatch tools — depends only on the shape, never on a concrete MCP
library. The shipped implementation (`SdkStreamableHttpTransport`) drives the
official MCP Python SDK; tests inject a fake transport that returns canned
tool lists and results without a network. This mirrors the `SandboxRuntime`
Protocol pattern: swapping the underlying client stays bounded.
"""

from __future__ import annotations

from typing import Any, Protocol

from .models import MCPTool, MCPToolResult, Transport


class MCPTransport(Protocol):
    """Stateless per-call access to one MCP server.

    Each method opens a session, performs the operation, and tears down.
    Auth is fully resolved by the caller and handed in as `headers`.
    """

    async def list_tools(
        self,
        *,
        endpoint_url: str,
        headers: dict[str, str],
        transport: Transport,
    ) -> list[MCPTool]:
        """Return the tools the server advertises."""
        ...

    async def call_tool(
        self,
        *,
        endpoint_url: str,
        headers: dict[str, str],
        transport: Transport,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolResult:
        """Invoke one tool and return its result."""
        ...


class SdkStreamableHttpTransport:
    """`MCPTransport` backed by the official `mcp` Python SDK.

    Requires the optional `mcp` extra. Imported lazily so the kernel core and
    the test suite don't depend on the SDK; only a process that actually talks
    to a live server needs it installed.
    """

    async def _session(self, endpoint_url: str, headers: dict[str, str], transport: Transport):
        try:
            from mcp import ClientSession
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "the `mcp` package is required to talk to MCP servers; "
                "install ownevo-kernel with the `mcp` extra"
            ) from exc

        if transport is Transport.SSE:
            from mcp.client.sse import sse_client

            return sse_client(endpoint_url, headers=headers), ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client(endpoint_url, headers=headers), ClientSession

    async def list_tools(
        self,
        *,
        endpoint_url: str,
        headers: dict[str, str],
        transport: Transport,
    ) -> list[MCPTool]:
        client_cm, ClientSession = await self._session(endpoint_url, headers, transport)
        async with client_cm as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    MCPTool(
                        name=t.name,
                        description=t.description or "",
                        input_schema=t.inputSchema or {},
                    )
                    for t in result.tools
                ]

    async def call_tool(
        self,
        *,
        endpoint_url: str,
        headers: dict[str, str],
        transport: Transport,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolResult:
        client_cm, ClientSession = await self._session(endpoint_url, headers, transport)
        async with client_cm as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                content = [block.model_dump() for block in result.content]
                return MCPToolResult(content=content, is_error=bool(result.isError))


__all__ = ["MCPTransport", "SdkStreamableHttpTransport"]
