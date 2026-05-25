"""MCP-client framework — consume any MCP server as an agent data source.

ownEvo reaches into a customer's connected systems (Slack, Google Workspace,
Microsoft 365, or any MCP-exposed source) by consuming MCP servers rather than
building bespoke per-source connectors. A server is registered once (endpoint
+ auth) in the `mcp_servers` table; a workflow spec declares it as a data
source; the agent runtime then lists and invokes its tools transparently
through `MCPClient`.

Layering:
  models     — typed server / tool / result records
  registry   — DB access, secret blob sealed at rest
  auth       — bearer / oauth-refresh / service-principal token resolution
  transport  — the MCP wire (Protocol + official-SDK implementation)
  client     — MCPClient: list_tools + mcp_call with TTL session caching
"""

from .auth import MCPAuthError, ResolvedAuth, TokenFetcher, resolve_auth
from .client import MCPClient, MCPServerNotFound
from .models import (
    AuthKind,
    MCPServer,
    MCPServerRegistration,
    MCPTool,
    MCPToolResult,
    Transport,
)
from .transport import MCPTransport, SdkStreamableHttpTransport

__all__ = [
    "AuthKind",
    "MCPAuthError",
    "MCPClient",
    "MCPServer",
    "MCPServerNotFound",
    "MCPServerRegistration",
    "MCPTool",
    "MCPToolResult",
    "MCPTransport",
    "ResolvedAuth",
    "SdkStreamableHttpTransport",
    "TokenFetcher",
    "Transport",
    "resolve_auth",
]
