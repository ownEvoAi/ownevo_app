"""OAuth authorization-code flow for the first-class MCP connectors.

This is the interactive half the token-refresh logic in `auth.py` does not
cover: building the provider consent URL, and exchanging the returned code for
tokens. It also owns the small bit of DB state the flow needs — the OAuth app
credentials the admin registered (`mcp_oauth_clients`) and the short-lived
per-attempt nonce (`mcp_oauth_states`).

The result of a successful exchange is normalized into the same
(auth_kind, auth_config, auth_secret) shape `registry.register_server` and
`auth.resolve_auth` already understand: a provider that returns a refresh token
becomes an auto-refreshing OAUTH server; one that returns only a long-lived
token becomes a static BEARER server.

Token HTTP is injected as a `TokenFetcher` (same as auth.py) so the exchange is
unit-testable without a network.
"""

from __future__ import annotations

import json
import secrets as _secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from ..secrets import decrypt, encrypt
from .auth import TokenFetcher
from .models import AuthKind
from .providers import ProviderPreset, resolve_url

if TYPE_CHECKING:
    import asyncpg

_DEFAULT_TOKEN_LIFETIME_SECONDS = 3600
# Authorization codes are exchanged within seconds of issue; a generous TTL
# still bounds how long an unused state row lingers.
_STATE_TTL_SECONDS = 600


# ---------------------------------------------------------------------------
# OAuth app credentials (mcp_oauth_clients)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthClientStatus:
    provider: str
    configured: bool
    client_id: str | None
    config: dict[str, Any]


async def set_oauth_client(
    conn: asyncpg.Connection,
    provider: str,
    *,
    client_id: str,
    client_secret: str,
    config: dict[str, Any] | None = None,
) -> None:
    """Store (encrypt) the provider's OAuth app credentials."""
    await conn.execute(
        """
        INSERT INTO mcp_oauth_clients
            (provider, client_id, client_secret_ciphertext, config, updated_at)
        VALUES ($1, $2, $3, $4::jsonb, now())
        ON CONFLICT (provider) DO UPDATE SET
            client_id                = EXCLUDED.client_id,
            client_secret_ciphertext = EXCLUDED.client_secret_ciphertext,
            config                   = EXCLUDED.config,
            updated_at               = now()
        """,
        provider,
        client_id,
        encrypt(client_secret),
        json.dumps(config or {}),
    )


async def get_oauth_client_status(
    conn: asyncpg.Connection, provider: str
) -> OAuthClientStatus:
    row = await conn.fetchrow(
        "SELECT client_id, config FROM mcp_oauth_clients WHERE provider = $1",
        provider,
    )
    if row is None:
        return OAuthClientStatus(
            provider=provider, configured=False, client_id=None, config={}
        )
    config = row["config"]
    if isinstance(config, str):
        config = json.loads(config)
    return OAuthClientStatus(
        provider=provider,
        configured=True,
        client_id=row["client_id"],
        config=config or {},
    )


async def get_oauth_client_secret(
    conn: asyncpg.Connection, provider: str
) -> str | None:
    ciphertext = await conn.fetchval(
        "SELECT client_secret_ciphertext FROM mcp_oauth_clients WHERE provider = $1",
        provider,
    )
    return decrypt(ciphertext) if ciphertext is not None else None


async def delete_oauth_client(conn: asyncpg.Connection, provider: str) -> bool:
    result = await conn.execute(
        "DELETE FROM mcp_oauth_clients WHERE provider = $1", provider
    )
    return result.endswith("1")


# ---------------------------------------------------------------------------
# Authorization-code state (mcp_oauth_states)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthState:
    state: str
    provider: str
    server_name: str
    scopes: list[str]
    endpoint_url: str


async def create_state(
    conn: asyncpg.Connection,
    *,
    provider: str,
    server_name: str,
    scopes: list[str],
    endpoint_url: str,
) -> str:
    """Mint and persist a CSRF state nonce; returns it for the authorize URL."""
    state = _secrets.token_urlsafe(32)
    await conn.execute(
        """
        INSERT INTO mcp_oauth_states (state, provider, server_name, scopes, endpoint_url)
        VALUES ($1, $2, $3, $4::jsonb, $5)
        """,
        state,
        provider,
        server_name,
        json.dumps(scopes),
        endpoint_url,
    )
    return state


async def consume_state(
    conn: asyncpg.Connection, state: str, *, now: datetime | None = None
) -> OAuthState | None:
    """Atomically fetch + delete a state row. None if absent or expired.

    Single-use by construction: the DELETE … RETURNING means a replayed
    callback finds nothing. Expired rows are treated as absent.
    """
    row = await conn.fetchrow(
        "DELETE FROM mcp_oauth_states WHERE state = $1 "
        "RETURNING provider, server_name, scopes, endpoint_url, created_at",
        state,
    )
    if row is None:
        return None
    current = now or datetime.now(row["created_at"].tzinfo)
    if current - row["created_at"] > timedelta(seconds=_STATE_TTL_SECONDS):
        return None
    scopes = row["scopes"]
    if isinstance(scopes, str):
        scopes = json.loads(scopes)
    return OAuthState(
        state=state,
        provider=row["provider"],
        server_name=row["server_name"],
        scopes=list(scopes or []),
        endpoint_url=row["endpoint_url"],
    )


# ---------------------------------------------------------------------------
# Authorize URL + code exchange
# ---------------------------------------------------------------------------


def build_authorize_url(
    preset: ProviderPreset,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    tenant: str | None = None,
) -> str:
    """Construct the provider consent-screen URL for the code grant."""
    base = resolve_url(preset.authorize_url, tenant=tenant)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        **preset.extra_authorize_params,
    }
    return f"{base}?{urlencode(params)}"


@dataclass(frozen=True)
class ExchangeResult:
    """Normalized output of a code exchange, ready for register_server."""

    auth_kind: AuthKind
    auth_config: dict[str, Any]
    auth_secret: dict[str, Any]


async def exchange_code(
    preset: ProviderPreset,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    scopes: list[str],
    token_fetcher: TokenFetcher,
    tenant: str | None = None,
    now: datetime | None = None,
) -> ExchangeResult:
    """Exchange an authorization code for tokens; normalize to storage shape.

    A response carrying a refresh token becomes an auto-refreshing OAUTH
    server (the access token will be refreshed by `auth.resolve_auth`); one
    without becomes a static BEARER server.
    """
    token_url = resolve_url(preset.token_url, tenant=tenant)
    response = await token_fetcher(
        token_url,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("token endpoint response is missing `access_token`")

    refresh_token = response.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        lifetime = response.get("expires_in")
        seconds = (
            int(lifetime)
            if isinstance(lifetime, (int, float))
            else _DEFAULT_TOKEN_LIFETIME_SECONDS
        )
        current = now or datetime.now()
        expires_at = (current + timedelta(seconds=seconds)).isoformat()
        return ExchangeResult(
            auth_kind=AuthKind.OAUTH,
            auth_config={
                "token_url": token_url,
                "client_id": client_id,
                "scopes": scopes,
            },
            auth_secret={
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                # Confidential clients (Google, Microsoft) need the secret at
                # refresh time; keep it in the sealed blob.
                "client_secret": client_secret,
            },
        )

    # No refresh token — a long-lived token (e.g. a non-rotating Slack token).
    return ExchangeResult(
        auth_kind=AuthKind.BEARER,
        auth_config={},
        auth_secret={"token": access_token},
    )


__all__ = [
    "ExchangeResult",
    "OAuthClientStatus",
    "OAuthState",
    "build_authorize_url",
    "consume_state",
    "create_state",
    "delete_oauth_client",
    "exchange_code",
    "get_oauth_client_secret",
    "get_oauth_client_status",
    "set_oauth_client",
]
