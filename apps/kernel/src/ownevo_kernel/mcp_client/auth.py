"""Resolve a registered server's auth into request headers.

Three auth flows are supported, unified behind one entry point
(`resolve_auth`): a static bearer token, an OAuth2 refresh-token grant, and a
service-principal client-credentials grant. OAuth and service-principal both
mint short-lived access tokens; when `resolve_auth` mints or refreshes one it
returns the updated secret blob via `ResolvedAuth.refreshed_secret` so the
caller can persist it and avoid re-minting on every call.

Token HTTP is injected as a `TokenFetcher` callable rather than reaching for
httpx directly, so the auth logic is unit-testable without a network and the
core kernel install stays HTTP-client-free (httpx lives in the `[api]` extra).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import AuthKind

# Refresh a token this many seconds before it actually expires, so a token
# resolved right at the boundary doesn't fail mid-request.
_EXPIRY_MARGIN_SECONDS = 60

# Fallback lifetime when a token response omits `expires_in`.
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600

TokenFetcher = Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]]
"""Posts form-encoded `data` to a token endpoint, returns the parsed JSON
token response (`access_token`, optional `expires_in`, optional
`refresh_token`)."""


class MCPAuthError(RuntimeError):
    """Auth config or secret material is missing or malformed for the flow."""


@dataclass(frozen=True)
class ResolvedAuth:
    """Headers to attach to a server request.

    `refreshed_secret` is non-None when a token was minted or refreshed during
    resolution; the caller persists it so the next call reuses the cached
    token instead of hitting the token endpoint again.
    """

    headers: dict[str, str]
    refreshed_secret: dict[str, Any] | None


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _parse_expires_at(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _token_still_valid(secret: dict[str, Any], now: datetime) -> bool:
    if not secret.get("access_token"):
        return False
    expires_at = _parse_expires_at(secret.get("expires_at"))
    if expires_at is None:
        return False
    return now < expires_at - timedelta(seconds=_EXPIRY_MARGIN_SECONDS)


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _scope_param(config: dict[str, Any]) -> str | None:
    if isinstance(config.get("scope"), str):
        return config["scope"]
    scopes = config.get("scopes")
    if isinstance(scopes, list) and scopes:
        return " ".join(str(s) for s in scopes)
    return None


def _token_response_to_secret(
    response: dict[str, Any],
    *,
    now: datetime,
    prior_refresh_token: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise MCPAuthError("token endpoint response is missing `access_token`")
    lifetime = response.get("expires_in")
    seconds = (
        int(lifetime)
        if isinstance(lifetime, (int, float))
        else DEFAULT_TOKEN_LIFETIME_SECONDS
    )
    expires_at = (now + timedelta(seconds=seconds)).isoformat()
    secret: dict[str, Any] = {"access_token": access_token, "expires_at": expires_at}
    # Some providers rotate the refresh token; prefer the new one, fall back
    # to the one we already held so a non-rotating provider keeps working.
    refresh_token = response.get("refresh_token") or prior_refresh_token
    if refresh_token:
        secret["refresh_token"] = refresh_token
    if extra:
        secret.update(extra)
    return secret


async def resolve_auth(
    *,
    auth_kind: AuthKind,
    auth_config: dict[str, Any],
    secret: dict[str, Any] | None,
    token_fetcher: TokenFetcher | None = None,
    now: datetime | None = None,
) -> ResolvedAuth:
    """Build request headers for a server, minting/refreshing tokens as needed.

    Raises `MCPAuthError` when the flow's required config or secret is absent.
    """
    current = _now(now)
    secret = secret or {}

    if auth_kind is AuthKind.NONE:
        return ResolvedAuth(headers={}, refreshed_secret=None)

    if auth_kind is AuthKind.BEARER:
        token = secret.get("token")
        if not isinstance(token, str) or not token:
            raise MCPAuthError("bearer auth requires a `token` in the stored secret")
        return ResolvedAuth(headers=_bearer_headers(token), refreshed_secret=None)

    # OAuth + service-principal both need a token endpoint and a fetcher.
    token_url = auth_config.get("token_url")
    if not isinstance(token_url, str) or not token_url:
        raise MCPAuthError(f"{auth_kind.value} auth requires `token_url` in auth_config")
    if token_fetcher is None:
        raise MCPAuthError("no token_fetcher available to mint a token")

    # A previously cached/minted token that hasn't expired is reused as-is.
    if _token_still_valid(secret, current):
        return ResolvedAuth(headers=_bearer_headers(secret["access_token"]), refreshed_secret=None)

    client_id = auth_config.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise MCPAuthError(f"{auth_kind.value} auth requires `client_id` in auth_config")

    if auth_kind is AuthKind.OAUTH:
        refresh_token = secret.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise MCPAuthError(
                "oauth access token is expired and no `refresh_token` is stored"
            )
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if isinstance(secret.get("client_secret"), str):
            data["client_secret"] = secret["client_secret"]
        response = await token_fetcher(token_url, data)
        refreshed = _token_response_to_secret(
            response,
            now=current,
            prior_refresh_token=refresh_token,
            extra=(
                {"client_secret": secret["client_secret"]}
                if isinstance(secret.get("client_secret"), str)
                else None
            ),
        )
        return ResolvedAuth(
            headers=_bearer_headers(refreshed["access_token"]),
            refreshed_secret=refreshed,
        )

    # SERVICE_PRINCIPAL — client-credentials grant.
    client_secret = secret.get("client_secret")
    if not isinstance(client_secret, str) or not client_secret:
        raise MCPAuthError(
            "service_principal auth requires a `client_secret` in the stored secret"
        )
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    scope = _scope_param(auth_config)
    if scope:
        data["scope"] = scope
    if isinstance(auth_config.get("tenant_id"), str):
        data["tenant_id"] = auth_config["tenant_id"]
    response = await token_fetcher(token_url, data)
    refreshed = _token_response_to_secret(
        response,
        now=current,
        prior_refresh_token=None,
        # Keep the client_secret in the blob so the next mint (after the
        # cached access token expires) still has it.
        extra={"client_secret": client_secret},
    )
    return ResolvedAuth(
        headers=_bearer_headers(refreshed["access_token"]),
        refreshed_secret=refreshed,
    )


async def httpx_token_fetcher(token_url: str, data: dict[str, str]) -> dict[str, Any]:
    """Default `TokenFetcher` — POSTs form-encoded data via httpx.

    httpx is imported lazily so this module imports cleanly without the
    `[api]` extra; only callers that actually mint tokens need it installed.
    """
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(token_url, data=data)
        resp.raise_for_status()
        return resp.json()


__all__ = [
    "MCPAuthError",
    "ResolvedAuth",
    "TokenFetcher",
    "httpx_token_fetcher",
    "resolve_auth",
]
