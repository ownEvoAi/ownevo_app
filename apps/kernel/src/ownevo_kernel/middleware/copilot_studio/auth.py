"""Entra ID (Azure AD) service-principal auth for Power Platform.

Copilot Studio has no API keys: every Power Platform REST call is
authenticated with an OAuth2 bearer token minted by Microsoft Entra ID
under the **client-credentials** grant (a service principal, not a
user). This module owns that one exchange — POST the client id/secret to
the tenant's token endpoint, get back a short-lived access token scoped
to the target environment — and translates Entra's error bodies into the
adapter's typed errors.

The token is scoped to the Dataverse/Power Platform environment via the
`{environment_url}/.default` scope, which grants the service principal
every application permission an admin has consented to for it. The token
lifetime is ~1 hour; callers cache it (see `evaluation_api.TokenCache`)
rather than re-minting per request.

Async by design: we own the HTTP here (unlike the LangSmith SDK, which
is synchronous and offloaded to a thread), so an `httpx.AsyncClient`
keeps the event loop free without a thread hop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .errors import (
    CopilotStudioAuthError,
    CopilotStudioError,
    CopilotStudioNetworkError,
)

# Public Microsoft cloud authority. Sovereign clouds (US Gov, China) use a
# different host; left configurable on the call rather than hard-coded so a
# customer in those clouds can override it without an adapter change.
DEFAULT_AUTHORITY_HOST = "https://login.microsoftonline.com"

# Connect/read timeout for the token exchange. The endpoint is fast; a long
# stall almost always means a network/DNS problem, which we surface as a
# network error rather than hanging the request.
_TOKEN_TIMEOUT_SECONDS = 15.0

# Re-mint this many seconds before the stated expiry so a token never expires
# mid-flight between the auth check and the downstream Power Platform call.
_EXPIRY_SKEW_SECONDS = 60.0


@dataclass(frozen=True)
class AccessToken:
    """A minted bearer token plus the monotonic clock time it goes stale.

    `expires_at` is on `time.monotonic()`'s timeline (not wall-clock) so
    cache freshness checks are immune to system clock adjustments.
    """

    token: str
    expires_at: float

    def is_fresh(self, *, now: float | None = None) -> bool:
        """True while the token is still safely usable (with skew margin)."""
        return (now if now is not None else time.monotonic()) < self.expires_at


def _token_endpoint(authority_host: str, tenant_id: str) -> str:
    return f"{authority_host.rstrip('/')}/{tenant_id}/oauth2/v2.0/token"


async def acquire_token(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    environment_url: str,
    authority_host: str = DEFAULT_AUTHORITY_HOST,
    http_client: httpx.AsyncClient | None = None,
) -> AccessToken:
    """Mint an Entra access token for the service principal.

    Performs the OAuth2 client-credentials exchange against the tenant's
    token endpoint, scoped to `{environment_url}/.default`. Returns the
    token and its monotonic expiry. Raises `CopilotStudioAuthError` when
    Entra rejects the credentials (invalid client/secret, unauthorized
    service principal) and `CopilotStudioNetworkError` on a connection
    failure; any other non-2xx becomes a generic `CopilotStudioError`.

    Pass `http_client` to reuse a connection pool (and to inject a mock
    transport in tests); a transient client is created otherwise.
    """
    scope = f"{environment_url.rstrip('/')}/.default"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
    }
    endpoint = _token_endpoint(authority_host, tenant_id)

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_TOKEN_TIMEOUT_SECONDS)
    try:
        resp = await client.post(endpoint, data=data)
    except httpx.TimeoutException as exc:
        raise CopilotStudioNetworkError(f"Entra token request timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise CopilotStudioNetworkError(f"Could not reach Entra: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code == 200:
        payload = resp.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise CopilotStudioError("Entra returned 200 with no access_token")
        # `expires_in` is seconds-from-now; convert to a monotonic deadline
        # with skew so the cache re-mints before the real expiry.
        expires_in = float(payload.get("expires_in", 3600))
        expires_at = time.monotonic() + max(0.0, expires_in - _EXPIRY_SKEW_SECONDS)
        return AccessToken(token=access_token, expires_at=expires_at)

    # Entra returns 400/401 with an OAuth error body for bad credentials.
    detail = _error_detail(resp)
    if resp.status_code in (400, 401, 403):
        raise CopilotStudioAuthError(f"Entra rejected the service principal: {detail}")
    raise CopilotStudioError(f"Entra token request failed ({resp.status_code}): {detail}")


def _error_detail(resp: httpx.Response) -> str:
    """Pull a human-readable reason out of an Entra error response.

    Entra error bodies are `{"error": "...", "error_description": "..."}`.
    Falls back to the raw text (truncated) when the body isn't JSON.
    """
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    description = body.get("error_description") or body.get("error") or ""
    return str(description)[:200]
