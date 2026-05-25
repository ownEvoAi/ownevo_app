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
  providers  — Slack / Google / Microsoft 365 OAuth + endpoint presets
  oauth      — authorization-code flow: authorize URL, code exchange, state
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
from .oauth import (
    ExchangeResult,
    OAuthClientStatus,
    OAuthState,
    build_authorize_url,
    exchange_code,
)
from .providers import ProviderPreset, UnknownProvider, all_presets, get_preset
from .transport import MCPTransport, SdkStreamableHttpTransport

__all__ = [
    "AuthKind",
    "ExchangeResult",
    "MCPAuthError",
    "MCPClient",
    "MCPServer",
    "MCPServerNotFound",
    "MCPServerRegistration",
    "MCPTool",
    "MCPToolResult",
    "MCPTransport",
    "OAuthClientStatus",
    "OAuthState",
    "ProviderPreset",
    "ResolvedAuth",
    "SdkStreamableHttpTransport",
    "TokenFetcher",
    "Transport",
    "UnknownProvider",
    "all_presets",
    "build_authorize_url",
    "exchange_code",
    "get_preset",
    "resolve_auth",
]
