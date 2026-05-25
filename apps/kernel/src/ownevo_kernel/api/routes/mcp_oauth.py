"""`/api/mcp/oauth` — OAuth authorization-code flow for MCP connectors.

Drives the interactive connect flow for Slack / Google Workspace / Microsoft
365: the admin registers the provider's OAuth app credentials, clicks
"Connect", gets redirected to the provider consent screen, and the provider
redirects back to the callback here — which exchanges the code for tokens and
registers an `mcp_servers` row.

The callback lives in the kernel (not the web tier) because the kernel owns
credential encryption and the server registry; the web app only needs to
redirect the browser to the authorize URL this module builds.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...mcp_client import (
    MCPServerRegistration,
    Transport,
    UnknownProvider,
    all_presets,
    build_authorize_url,
    exchange_code,
    get_preset,
    registry,
)
from ...mcp_client import oauth as oauth_flow
from ...mcp_client.auth import httpx_token_fetcher
from ..deps import ConnDep, DemoModeCheck

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/oauth", tags=["mcp"])

# Where the provider sends the browser back. Must be identical at authorize and
# token-exchange time and must match the redirect URI registered with the
# provider's OAuth app. Defaults suit local dev.
_DEFAULT_API_URL = "http://localhost:8000"
_DEFAULT_WEB_URL = "http://localhost:3000"
_DEFAULT_WS_SLUG = "acme"

# provider id -> web settings route slug
_UI_SLUG = {
    "slack": "slack",
    "google_workspace": "google-workspace",
    "microsoft_365": "microsoft-365",
}


def _public_api_url() -> str:
    return os.environ.get("OWNEVO_PUBLIC_API_URL", _DEFAULT_API_URL).rstrip("/")


def _web_base_url() -> str:
    return os.environ.get("OWNEVO_WEB_BASE_URL", _DEFAULT_WEB_URL).rstrip("/")


def _redirect_uri(provider: str) -> str:
    return f"{_public_api_url()}/api/mcp/oauth/{provider}/callback"


def _settings_url(provider: str, **query: str) -> str:
    slug = _UI_SLUG.get(provider, provider)
    ws = os.environ.get("OWNEVO_WEB_WORKSPACE_SLUG", _DEFAULT_WS_SLUG)
    url = f"{_web_base_url()}/workspaces/{ws}/settings/integrations/{slug}"
    if query:
        from urllib.parse import urlencode

        url = f"{url}?{urlencode(query)}"
    return url


def _preset_or_404(provider: str):
    try:
        return get_preset(provider)
    except UnknownProvider as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from None


# ---------------------------------------------------------------------------
# Provider catalogue
# ---------------------------------------------------------------------------


class ProviderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    display_name: str
    default_scopes: list[str]
    default_endpoint_url: str
    tenant_scoped: bool


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """The first-class connectors ownEvo ships presets for."""
    return [
        ProviderInfo(
            provider=p.provider,
            display_name=p.display_name,
            default_scopes=list(p.default_scopes),
            default_endpoint_url=p.default_endpoint_url,
            tenant_scoped=p.tenant_scoped,
        )
        for p in all_presets()
    ]


# ---------------------------------------------------------------------------
# OAuth app credentials
# ---------------------------------------------------------------------------


class OAuthClientSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=4096)
    client_secret: str = Field(min_length=1, max_length=4096)
    # Non-secret extras, e.g. {"tenant": "<guid>"} for Microsoft 365.
    config: dict[str, Any] = Field(default_factory=dict)


class OAuthClientView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    configured: bool
    client_id: str | None
    config: dict[str, Any]


@router.get("/{provider}/client", response_model=OAuthClientView)
async def get_client(provider: str, conn: ConnDep) -> OAuthClientView:
    _preset_or_404(provider)
    s = await oauth_flow.get_oauth_client_status(conn, provider)
    return OAuthClientView(
        provider=provider,
        configured=s.configured,
        client_id=s.client_id,
        config=s.config,
    )


@router.put("/{provider}/client", response_model=OAuthClientView)
async def set_client(
    provider: str, body: OAuthClientSet, conn: ConnDep, _demo: DemoModeCheck
) -> OAuthClientView:
    """Store the provider's OAuth app credentials (client secret sealed)."""
    _preset_or_404(provider)
    await oauth_flow.set_oauth_client(
        conn,
        provider,
        client_id=body.client_id.strip(),
        client_secret=body.client_secret.strip(),
        config=body.config,
    )
    return await get_client(provider, conn)


@router.delete("/{provider}/client", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(provider: str, conn: ConnDep, _demo: DemoModeCheck) -> None:
    """Remove the stored OAuth app credentials. Idempotent."""
    _preset_or_404(provider)
    await oauth_flow.delete_oauth_client(conn, provider)


# ---------------------------------------------------------------------------
# Authorization-code flow
# ---------------------------------------------------------------------------


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_name: str = Field(min_length=1, max_length=200)
    # Override the preset defaults when set.
    scopes: list[str] | None = None
    endpoint_url: str | None = Field(default=None, max_length=2048)

    @field_validator("endpoint_url")
    @classmethod
    def _endpoint_url_must_be_http(cls, v: str | None) -> str | None:
        if v is None:
            return v
        scheme = urlparse(v).scheme
        if scheme not in {"http", "https"}:
            raise ValueError(
                f"endpoint_url must use http or https; got scheme {scheme!r}"
            )
        return v


class StartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorize_url: str


@router.post("/{provider}/start", response_model=StartResponse)
async def start(
    provider: str, body: StartRequest, conn: ConnDep, _demo: DemoModeCheck
) -> StartResponse:
    """Begin the connect flow: mint a state nonce, return the consent URL."""
    preset = _preset_or_404(provider)
    client = await oauth_flow.get_oauth_client_status(conn, provider)
    if not client.configured or not client.client_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"no OAuth app credentials configured for {provider}; set the "
                "client id + secret first"
            ),
        )
    scopes = body.scopes or list(preset.default_scopes)
    endpoint_url = body.endpoint_url or preset.default_endpoint_url
    state = await oauth_flow.create_state(
        conn,
        provider=provider,
        server_name=body.server_name.strip(),
        scopes=scopes,
        endpoint_url=endpoint_url,
    )
    authorize_url = build_authorize_url(
        preset,
        client_id=client.client_id,
        redirect_uri=_redirect_uri(provider),
        scopes=scopes,
        state=state,
        tenant=client.config.get("tenant") if preset.tenant_scoped else None,
    )
    return StartResponse(authorize_url=authorize_url)


@router.get("/{provider}/callback")
async def callback(
    provider: str,
    conn: ConnDep,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Provider redirect target: exchange the code, register the server.

    Always redirects back to the provider's settings page — with `connected=1`
    on success or `error=...` on any failure — so the admin lands somewhere
    sensible rather than seeing a raw API response.
    """
    preset = _preset_or_404(provider)
    if error:
        # Truncate the provider-supplied error string before reflecting it into
        # the Location header — an unbounded value from a misconfigured provider
        # can exceed HTTP stack header limits and turn the redirect into a 500.
        return RedirectResponse(
            _settings_url(provider, error=error[:256]), status_code=status.HTTP_302_FOUND
        )
    if not state or not code:
        return RedirectResponse(
            _settings_url(provider, error="missing_code_or_state"),
            status_code=status.HTTP_302_FOUND,
        )

    pending = await oauth_flow.consume_state(conn, state)
    if pending is None or pending.provider != provider:
        return RedirectResponse(
            _settings_url(provider, error="invalid_state"),
            status_code=status.HTTP_302_FOUND,
        )

    client = await oauth_flow.get_oauth_client_status(conn, provider)
    client_secret = await oauth_flow.get_oauth_client_secret(conn, provider)
    if not client.configured or not client.client_id or client_secret is None:
        return RedirectResponse(
            _settings_url(provider, error="client_not_configured"),
            status_code=status.HTTP_302_FOUND,
        )

    try:
        result = await exchange_code(
            preset,
            client_id=client.client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=_redirect_uri(provider),
            scopes=pending.scopes,
            token_fetcher=httpx_token_fetcher,
            tenant=client.config.get("tenant") if preset.tenant_scoped else None,
        )
    except Exception as exc:  # noqa: BLE001 — any failure becomes an error redirect
        _log.warning("MCP OAuth code exchange failed for %s: %s", provider, exc)
        return RedirectResponse(
            _settings_url(provider, error="token_exchange_failed"),
            status_code=status.HTTP_302_FOUND,
        )

    await registry.register_server(
        conn,
        MCPServerRegistration(
            name=pending.server_name,
            provider=provider,
            endpoint_url=pending.endpoint_url,
            transport=Transport.STREAMABLE_HTTP,
            auth_kind=result.auth_kind,
            auth_config=result.auth_config,
            auth_secret=result.auth_secret,
        ),
    )
    return RedirectResponse(
        _settings_url(provider, connected="1"), status_code=status.HTTP_302_FOUND
    )
