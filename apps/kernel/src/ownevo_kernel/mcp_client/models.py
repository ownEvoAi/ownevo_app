"""Typed models for the MCP-client framework.

These describe a registered MCP server (its endpoint + how to authenticate),
the tools a server exposes, and the result of invoking one. The secret half
of the auth material never appears here — it lives encrypted in the DB and is
resolved into request headers only at call time (see auth.py).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthKind(StrEnum):
    """How ownEvo authenticates to a server.

    NONE — public server, no credentials.
    BEARER — a static long-lived token sent as `Authorization: Bearer …`.
    OAUTH — an OAuth2 access token refreshed via a refresh-token grant.
    SERVICE_PRINCIPAL — a confidential client minting tokens via the
        client-credentials grant (Microsoft Entra app registrations, Google
        service accounts exposed as OAuth clients, etc.).
    """

    NONE = "none"
    BEARER = "bearer"
    OAUTH = "oauth"
    SERVICE_PRINCIPAL = "service_principal"


class Transport(StrEnum):
    """MCP wire transport. Both are HTTP-based; the SDK picks the framing."""

    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


class MCPServer(_Base):
    """A registered MCP server, as stored in the `mcp_servers` table.

    `auth_config` carries only non-secret auth parameters (token endpoint,
    client_id, scopes, tenant id). The secret material is held separately,
    encrypted, and is never part of this model — callers that need it go
    through the registry's plaintext accessor.
    """

    id: UUID
    name: str
    provider: str
    endpoint_url: str
    transport: Transport
    auth_kind: AuthKind
    auth_config: dict[str, Any] = Field(default_factory=dict)
    status: str
    has_secret: bool
    last_validated_at: str | None = None
    validation_status: str | None = None


class MCPServerRegistration(_Base):
    """Input to register (or replace) an MCP server.

    `auth_secret` is the plaintext secret blob — its shape depends on
    `auth_kind` (see auth.py). It is sealed before storage and dropped from
    memory immediately after. None is only valid for `auth_kind=none`.
    """

    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)
    endpoint_url: str = Field(min_length=1, max_length=2048)
    transport: Transport = Transport.STREAMABLE_HTTP
    auth_kind: AuthKind = AuthKind.NONE
    auth_config: dict[str, Any] = Field(default_factory=dict)
    auth_secret: dict[str, Any] | None = None


class MCPTool(_Base):
    """A tool advertised by a connected MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPToolResult(_Base):
    """Outcome of a single `call_tool` against a server.

    `is_error` mirrors MCP's own tool-result error flag (a tool that ran but
    reported failure). Transport/auth failures are raised as exceptions rather
    than returned here — the dispatch layer shapes those into the agent's
    tool_call_result error event.
    """

    content: Any
    is_error: bool = False


__all__ = [
    "AuthKind",
    "MCPServer",
    "MCPServerRegistration",
    "MCPTool",
    "MCPToolResult",
    "Transport",
]
