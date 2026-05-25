"""Per-provider presets for the three first-class MCP connectors.

A preset captures everything provider-specific about the OAuth handshake and
the resulting MCP server: the authorize + token endpoints, the default scopes
ownEvo requests, the default MCP server endpoint, and any extra authorize
parameters a provider needs to return a refresh token (Google's
`access_type=offline` + `prompt=consent`).

ownEvo ships the auth + config; it does not reimplement the providers' MCP
servers. `default_endpoint_url` points at the MCP server for each provider and
is overridable per registration, since the exact host depends on how the
customer runs the (official or community) server.

Microsoft 365 is tenant-scoped: its authorize/token URLs interpolate a tenant
id (default `common`, overridable when the OAuth client is configured).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import AuthKind


@dataclass(frozen=True)
class ProviderPreset:
    provider: str
    display_name: str
    authorize_url: str
    token_url: str
    default_scopes: tuple[str, ...]
    default_endpoint_url: str
    # Extra static query params on the authorize URL (e.g. Google needs
    # access_type=offline + prompt=consent to return a refresh token).
    extra_authorize_params: dict[str, str] = field(default_factory=dict)
    # When the auth flow yields a refresh token + expiry the server is stored
    # as OAUTH (auto-refreshing); the callback overrides this to BEARER if the
    # provider returns only a long-lived token with no refresh.
    auth_kind: AuthKind = AuthKind.OAUTH
    # True for providers whose endpoints interpolate a tenant id.
    tenant_scoped: bool = False


_SLACK = ProviderPreset(
    provider="slack",
    display_name="Slack",
    authorize_url="https://slack.com/oauth/v2/authorize",
    token_url="https://slack.com/api/oauth.v2.access",
    default_scopes=("channels:read", "channels:history", "chat:write"),
    default_endpoint_url="https://mcp.slack.com/mcp",
)

_GOOGLE = ProviderPreset(
    provider="google_workspace",
    display_name="Google Workspace",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    default_scopes=(
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/documents.readonly",
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/gmail.readonly",
    ),
    default_endpoint_url="https://mcp.google.com/workspace",
    # Without these Google issues an access token but no refresh token, so the
    # connection would die at the first expiry.
    extra_authorize_params={"access_type": "offline", "prompt": "consent"},
)

_MICROSOFT = ProviderPreset(
    provider="microsoft_365",
    display_name="Microsoft 365",
    authorize_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
    default_scopes=(
        "offline_access",
        "Files.Read.All",
        "Mail.Read",
    ),
    default_endpoint_url="https://mcp.microsoft.com/m365",
    tenant_scoped=True,
)

_PRESETS: dict[str, ProviderPreset] = {
    p.provider: p for p in (_SLACK, _GOOGLE, _MICROSOFT)
}


class UnknownProvider(KeyError):
    """No preset is defined for the given provider id."""


def get_preset(provider: str) -> ProviderPreset:
    try:
        return _PRESETS[provider]
    except KeyError as exc:
        raise UnknownProvider(
            f"unknown MCP provider {provider!r}; known: {sorted(_PRESETS)}"
        ) from exc


def all_presets() -> list[ProviderPreset]:
    return list(_PRESETS.values())


def resolve_url(template: str, *, tenant: str | None) -> str:
    """Interpolate a tenant id into a tenant-scoped URL template."""
    if "{tenant}" in template:
        return template.format(tenant=tenant or "common")
    return template


__all__ = [
    "ProviderPreset",
    "UnknownProvider",
    "all_presets",
    "get_preset",
    "resolve_url",
]
