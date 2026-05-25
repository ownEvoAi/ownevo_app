"""Unit tests for the mcp_list_tools / mcp_call agent dispatch — no DB.

A fake MCPClient stands in for the real one so dispatch behaviour (scope
enforcement, success shaping, error surfacing) is tested without a network or
a database. Calls go through the public `dispatch_tool` so the error-shaping
wrapper is exercised too.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from ownevo_kernel.mcp_client import MCPTool, MCPToolResult
from ownevo_kernel.middleware.claude_sdk import KernelContext
from ownevo_kernel.middleware.claude_sdk.tool_definitions import dispatch_tool

SERVER_ID = str(uuid4())


class _FakeMCPClient:
    def __init__(self, *, tools=None, result=None, raises=None) -> None:
        self._tools = tools or []
        self._result = result
        self._raises = raises
        self.calls: list[tuple] = []

    async def list_tools(self, conn, server_id, *, now=None):
        self.calls.append(("list_tools", server_id))
        if self._raises is not None:
            raise self._raises
        return self._tools

    async def mcp_call(self, conn, server_id, tool_name, arguments, *, now=None):
        self.calls.append(("mcp_call", server_id, tool_name, arguments))
        if self._raises is not None:
            raise self._raises
        return self._result


def _ctx(client) -> KernelContext:
    return KernelContext(
        conn=None,  # fake client ignores it
        sandbox=None,  # type: ignore[arg-type]
        actor="agent:test",
        mcp_client=client,
        mcp_server_ids=(SERVER_ID,),
    )


async def test_list_tools_success() -> None:
    client = _FakeMCPClient(
        tools=[MCPTool(name="search", description="d", input_schema={"type": "object"})]
    )
    res = await dispatch_tool("mcp_list_tools", {"server_id": SERVER_ID}, _ctx(client))
    assert res.is_error is False
    assert res.output["tools"][0]["name"] == "search"


async def test_call_success() -> None:
    client = _FakeMCPClient(result=MCPToolResult(content={"rows": 3}, is_error=False))
    res = await dispatch_tool(
        "mcp_call",
        {"server_id": SERVER_ID, "tool_name": "read_sheet", "arguments": {"id": "x"}},
        _ctx(client),
    )
    assert res.is_error is False
    assert res.output["content"] == {"rows": 3}
    assert client.calls[0] == ("mcp_call", UUID(SERVER_ID), "read_sheet", {"id": "x"})


async def test_call_tool_reported_error_surfaces_as_error() -> None:
    client = _FakeMCPClient(result=MCPToolResult(content="boom", is_error=True))
    res = await dispatch_tool(
        "mcp_call",
        {"server_id": SERVER_ID, "tool_name": "t"},
        _ctx(client),
    )
    assert res.is_error is True
    assert res.output["is_error"] is True


async def test_transport_exception_surfaces_as_tool_error() -> None:
    client = _FakeMCPClient(raises=RuntimeError("connection refused"))
    res = await dispatch_tool(
        "mcp_call",
        {"server_id": SERVER_ID, "tool_name": "t"},
        _ctx(client),
    )
    assert res.is_error is True
    assert "connection refused" in res.output


async def test_undeclared_server_id_is_rejected() -> None:
    client = _FakeMCPClient()
    res = await dispatch_tool(
        "mcp_call",
        {"server_id": str(uuid4()), "tool_name": "t"},
        _ctx(client),
    )
    assert res.is_error is True
    assert client.calls == []  # never reached the client


async def test_missing_client_is_tool_error() -> None:
    ctx = KernelContext(
        conn=None,
        sandbox=None,  # type: ignore[arg-type]
        actor="agent:test",
        mcp_client=None,
        mcp_server_ids=(SERVER_ID,),
    )
    res = await dispatch_tool("mcp_list_tools", {"server_id": SERVER_ID}, ctx)
    assert res.is_error is True


async def test_invalid_uuid_is_tool_error() -> None:
    # An id present in scope but not a UUID still fails the parse.
    ctx = KernelContext(
        conn=None,
        sandbox=None,  # type: ignore[arg-type]
        actor="agent:test",
        mcp_client=_FakeMCPClient(),
        mcp_server_ids=("not-a-uuid",),
    )
    res = await dispatch_tool("mcp_call", {"server_id": "not-a-uuid", "tool_name": "t"}, ctx)
    assert res.is_error is True
