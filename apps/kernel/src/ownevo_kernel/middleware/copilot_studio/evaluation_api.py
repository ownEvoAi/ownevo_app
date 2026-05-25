"""Power Platform Evaluation API — push ownEvo eval cases into Copilot Studio.

Copilot Studio is the only enterprise agent platform with a documented,
externally-callable eval-push API: a customer's deployed agent can be
tested against synthetic cases we send in, via the Power Platform
Evaluation API (`POST .../testsets?api-version=2024-10-01`). This module
wraps that surface so ownEvo can turn a failure cluster's eval cases into
a Copilot Studio test set without the customer hand-entering them.

Auth is the Entra service-principal token from `auth.py`, cached here
(`TokenCache`) so a burst of calls mints one token, not one per request.

What this module does **not** do: trigger a test run or poll results.
The create-test-set contract is the documented, stable surface; the
run/poll lifecycle is preview and its response shapes aren't pinned, so
it's deferred rather than coded against a guess (see MAPPING.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from .auth import DEFAULT_AUTHORITY_HOST, AccessToken, acquire_token
from .errors import (
    CopilotStudioAuthError,
    CopilotStudioError,
    CopilotStudioNetworkError,
    CopilotStudioNotFoundError,
    CopilotStudioRateLimitError,
)

# Pinned Evaluation API version. Microsoft versions Power Platform APIs by
# date query-param; this is the version the request shapes below target.
EVAL_API_VERSION = "2024-10-01"

# Timeout for Power Platform data calls. Test-set creation is a single write;
# a long stall is a network problem, surfaced as a network error.
_API_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class CopilotStudioCredentials:
    """Everything needed to authenticate against one Copilot Studio environment.

    Mirrors the structured credential stored (encrypted) in
    `integration_credentials`. `environment_url` is the Dataverse org URL
    (e.g. `https://org.crm.dynamics.com`); the token is scoped to it.
    """

    tenant_id: str
    client_id: str
    client_secret: str
    environment_url: str
    authority_host: str = DEFAULT_AUTHORITY_HOST


@dataclass(frozen=True)
class TestSetResult:
    """Outcome of creating a Copilot Studio test set from ownEvo eval cases."""

    test_set_id: str
    case_count: int


class TokenCache:
    """Caches one Entra token and re-mints it lazily when it goes stale.

    Not thread-safe and not concurrency-deduplicated — under a burst it
    may mint a couple of tokens before the cache settles, which Entra
    tolerates. Scoped to one credential set; build a new cache when the
    credential changes.
    """

    def __init__(
        self,
        credentials: CopilotStudioCredentials,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._creds = credentials
        self._http = http_client
        self._token: AccessToken | None = None

    async def token(self) -> str:
        """Return a fresh bearer token, minting one if the cache is stale."""
        if self._token is None or not self._token.is_fresh():
            self._token = await acquire_token(
                tenant_id=self._creds.tenant_id,
                client_id=self._creds.client_id,
                client_secret=self._creds.client_secret,
                environment_url=self._creds.environment_url,
                authority_host=self._creds.authority_host,
                http_client=self._http,
            )
        return self._token.token


async def verify_connection(
    credentials: CopilotStudioCredentials,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """Check the service principal authenticates against the environment.

    Mints a token (the cheapest authenticated round-trip) and returns
    None on success. Raises `CopilotStudioAuthError` when Entra rejects
    the credentials and `CopilotStudioNetworkError` on a connection
    failure — the same error surface the "test connection" action reports.
    """
    await acquire_token(
        tenant_id=credentials.tenant_id,
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
        environment_url=credentials.environment_url,
        authority_host=credentials.authority_host,
        http_client=http_client,
    )


def _testsets_endpoint(environment_url: str) -> str:
    base = environment_url.rstrip("/")
    return f"{base}/api/copilotstudio/testsets?api-version={EVAL_API_VERSION}"


async def create_test_set(
    credentials: CopilotStudioCredentials,
    *,
    agent_id: str,
    name: str,
    cases: Sequence[dict],
    token_cache: TokenCache | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TestSetResult:
    """Push a set of eval cases into Copilot Studio as a named test set.

    `cases` are ownEvo eval cases already shaped for the Evaluation API
    (each `{input, expected_output}`); the caller owns that mapping.
    Raises a typed adapter error on any failure: auth (401/403), not
    found (404, e.g. bad environment or agent id), rate limit (429),
    network, or generic.
    """
    cache = token_cache or TokenCache(credentials, http_client=http_client)
    bearer = await cache.token()

    body = {
        "agentId": agent_id,
        "name": name,
        "testCases": list(cases),
    }
    endpoint = _testsets_endpoint(credentials.environment_url)

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_API_TIMEOUT_SECONDS)
    try:
        resp = await client.post(
            endpoint,
            json=body,
            headers={"Authorization": f"Bearer {bearer}"},
        )
    except httpx.TimeoutException as exc:
        raise CopilotStudioNetworkError(f"Evaluation API request timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise CopilotStudioNetworkError(f"Could not reach Power Platform: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    _raise_for_status(resp)

    payload = resp.json() if resp.content else {}
    test_set_id = str(payload.get("id") or payload.get("testSetId") or "")
    return TestSetResult(test_set_id=test_set_id, case_count=len(body["testCases"]))


def _raise_for_status(resp: httpx.Response) -> None:
    """Map a Power Platform error response onto a typed adapter error."""
    if resp.status_code // 100 == 2:
        return
    detail = _error_detail(resp)
    if resp.status_code in (401, 403):
        raise CopilotStudioAuthError(f"Power Platform rejected the token: {detail}")
    if resp.status_code == 404:
        raise CopilotStudioNotFoundError(f"Environment or agent not found: {detail}")
    if resp.status_code == 429:
        raise CopilotStudioRateLimitError(f"Power Platform throttled the request: {detail}")
    raise CopilotStudioError(f"Evaluation API call failed ({resp.status_code}): {detail}")


def _error_detail(resp: httpx.Response) -> str:
    """Pull a reason out of a Power Platform error body.

    Dataverse/Power Platform errors are `{"error": {"message": "..."}}`;
    falls back to the raw text (truncated) when the body isn't JSON.
    """
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message", ""))[:200]
    return str(err or body)[:200]
