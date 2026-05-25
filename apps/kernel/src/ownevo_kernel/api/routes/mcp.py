"""`/api/mcp/servers` — register and manage connected MCP servers.

Backs the per-provider integration admin pages (Track 17.0.2). A server is
registered once with its endpoint + auth; the secret half of the auth is
sealed at rest by the registry and is never returned to the client — GET
reports only the non-secret view plus whether a secret is stored and the last
connection-test result.

"Test connection" lists the server's tools through the live MCP transport and
records the outcome, so the UI can show whether the connection still works
without re-hitting the server on every page load.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from ...mcp_client import (
    MCPClient,
    MCPServer,
    MCPServerRegistration,
    registry,
)
from ..deps import ConnDep, DemoModeCheck

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class MCPTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    tool_count: int | None = None
    detail: str | None = None


@router.get("/servers", response_model=list[MCPServer])
async def list_mcp_servers(conn: ConnDep) -> list[MCPServer]:
    """All registered servers (non-secret view)."""
    return await registry.list_servers(conn)


@router.post("/servers", response_model=MCPServer, status_code=status.HTTP_201_CREATED)
async def register_mcp_server(
    body: MCPServerRegistration,
    conn: ConnDep,
    _demo: DemoModeCheck,
) -> MCPServer:
    """Register (or replace, by name) a server. Seals the auth secret at rest.

    A 500 here means the credential master key isn't configured on the server
    (see secrets/encrypted_field.py).
    """
    return await registry.register_server(conn, body)


@router.get("/servers/{server_id}", response_model=MCPServer)
async def get_mcp_server(server_id: UUID, conn: ConnDep) -> MCPServer:
    server = await registry.get_server(conn, server_id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no MCP server registered with id {server_id}",
        )
    return server


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_id: UUID, conn: ConnDep, _demo: DemoModeCheck
) -> None:
    """Remove a server. Idempotent (204 even if absent)."""
    await registry.delete_server(conn, server_id)


@router.post("/servers/{server_id}/test", response_model=MCPTestResult)
async def test_mcp_server(
    server_id: UUID, conn: ConnDep, _demo: DemoModeCheck
) -> MCPTestResult:
    """List the server's tools to validate the connection; record the result.

    404 when the server isn't registered. Otherwise 'ok' (tools listed) or
    'error' (auth / transport / SDK failure), with the outcome stamped for the
    Settings UI.
    """
    server = await registry.get_server(conn, server_id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no MCP server registered with id {server_id}",
        )
    client = MCPClient()
    try:
        tools = await client.list_tools(conn, server_id)
    except Exception as exc:  # noqa: BLE001 — any failure is a failed test
        await registry.record_validation(conn, server_id, "error")
        return MCPTestResult(status="error", detail=str(exc)[:200])
    await registry.record_validation(conn, server_id, "ok")
    return MCPTestResult(status="ok", tool_count=len(tools))
