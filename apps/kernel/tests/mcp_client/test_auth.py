"""Unit tests for MCP auth resolution — no DB, no network.

Token HTTP is a fake `TokenFetcher` that records calls and returns canned
responses, so the OAuth-refresh and service-principal client-credentials paths
are exercised deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ownevo_kernel.mcp_client import AuthKind, MCPAuthError, resolve_auth


class _FakeTokenFetcher:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, token_url: str, data: dict) -> dict:
        self.calls.append((token_url, data))
        return self.response


async def test_none_returns_no_headers() -> None:
    resolved = await resolve_auth(
        auth_kind=AuthKind.NONE, auth_config={}, secret=None
    )
    assert resolved.headers == {}
    assert resolved.refreshed_secret is None


async def test_bearer_sets_authorization_header() -> None:
    resolved = await resolve_auth(
        auth_kind=AuthKind.BEARER,
        auth_config={},
        secret={"token": "tok-123"},
    )
    assert resolved.headers == {"Authorization": "Bearer tok-123"}
    assert resolved.refreshed_secret is None


async def test_bearer_without_token_raises() -> None:
    with pytest.raises(MCPAuthError):
        await resolve_auth(auth_kind=AuthKind.BEARER, auth_config={}, secret={})


async def test_oauth_reuses_valid_access_token() -> None:
    now = datetime(2026, 5, 25, tzinfo=UTC)
    fetcher = _FakeTokenFetcher({"access_token": "should-not-mint"})
    secret = {
        "access_token": "still-good",
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "refresh_token": "rt",
    }
    resolved = await resolve_auth(
        auth_kind=AuthKind.OAUTH,
        auth_config={"token_url": "https://t", "client_id": "c"},
        secret=secret,
        token_fetcher=fetcher,
        now=now,
    )
    assert resolved.headers == {"Authorization": "Bearer still-good"}
    assert resolved.refreshed_secret is None
    assert fetcher.calls == []  # no mint while the token is valid


async def test_oauth_refreshes_expired_token() -> None:
    now = datetime(2026, 5, 25, tzinfo=UTC)
    fetcher = _FakeTokenFetcher(
        {"access_token": "fresh", "expires_in": 3600, "refresh_token": "rt2"}
    )
    secret = {
        "access_token": "expired",
        "expires_at": (now - timedelta(minutes=5)).isoformat(),
        "refresh_token": "rt1",
    }
    resolved = await resolve_auth(
        auth_kind=AuthKind.OAUTH,
        auth_config={"token_url": "https://token", "client_id": "client-1"},
        secret=secret,
        token_fetcher=fetcher,
        now=now,
    )
    assert resolved.headers == {"Authorization": "Bearer fresh"}
    assert resolved.refreshed_secret is not None
    assert resolved.refreshed_secret["access_token"] == "fresh"
    assert resolved.refreshed_secret["refresh_token"] == "rt2"  # rotated
    url, data = fetcher.calls[0]
    assert url == "https://token"
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "rt1"
    assert data["client_id"] == "client-1"


async def test_oauth_keeps_prior_refresh_token_when_not_rotated() -> None:
    now = datetime(2026, 5, 25, tzinfo=UTC)
    fetcher = _FakeTokenFetcher({"access_token": "fresh", "expires_in": 3600})
    resolved = await resolve_auth(
        auth_kind=AuthKind.OAUTH,
        auth_config={"token_url": "https://token", "client_id": "c"},
        secret={"refresh_token": "keep-me"},
        token_fetcher=fetcher,
        now=now,
    )
    assert resolved.refreshed_secret["refresh_token"] == "keep-me"


async def test_oauth_expired_without_refresh_token_raises() -> None:
    with pytest.raises(MCPAuthError):
        await resolve_auth(
            auth_kind=AuthKind.OAUTH,
            auth_config={"token_url": "https://token", "client_id": "c"},
            secret={},
            token_fetcher=_FakeTokenFetcher({}),
        )


async def test_service_principal_mints_via_client_credentials() -> None:
    now = datetime(2026, 5, 25, tzinfo=UTC)
    fetcher = _FakeTokenFetcher({"access_token": "sp-token", "expires_in": 1800})
    resolved = await resolve_auth(
        auth_kind=AuthKind.SERVICE_PRINCIPAL,
        auth_config={
            "token_url": "https://login/oauth2/token",
            "client_id": "app-id",
            "scopes": ["https://graph.example/.default"],
            "tenant_id": "tenant-9",
        },
        secret={"client_secret": "shh"},
        token_fetcher=fetcher,
        now=now,
    )
    assert resolved.headers == {"Authorization": "Bearer sp-token"}
    # client_secret is retained in the refreshed blob so the next mint works.
    assert resolved.refreshed_secret["client_secret"] == "shh"
    assert resolved.refreshed_secret["access_token"] == "sp-token"
    _, data = fetcher.calls[0]
    assert data["grant_type"] == "client_credentials"
    assert data["client_id"] == "app-id"
    assert data["client_secret"] == "shh"
    assert data["scope"] == "https://graph.example/.default"
    assert data["tenant_id"] == "tenant-9"


async def test_service_principal_reuses_cached_token() -> None:
    now = datetime(2026, 5, 25, tzinfo=UTC)
    fetcher = _FakeTokenFetcher({"access_token": "unused"})
    secret = {
        "client_secret": "shh",
        "access_token": "cached",
        "expires_at": (now + timedelta(minutes=20)).isoformat(),
    }
    resolved = await resolve_auth(
        auth_kind=AuthKind.SERVICE_PRINCIPAL,
        auth_config={"token_url": "https://t", "client_id": "a"},
        secret=secret,
        token_fetcher=fetcher,
        now=now,
    )
    assert resolved.headers == {"Authorization": "Bearer cached"}
    assert fetcher.calls == []


async def test_oauth_requires_token_url() -> None:
    with pytest.raises(MCPAuthError):
        await resolve_auth(
            auth_kind=AuthKind.OAUTH,
            auth_config={"client_id": "c"},
            secret={"refresh_token": "rt"},
            token_fetcher=_FakeTokenFetcher({}),
        )


async def test_token_response_without_access_token_raises() -> None:
    with pytest.raises(MCPAuthError):
        await resolve_auth(
            auth_kind=AuthKind.SERVICE_PRINCIPAL,
            auth_config={"token_url": "https://t", "client_id": "a"},
            secret={"client_secret": "s"},
            token_fetcher=_FakeTokenFetcher({"expires_in": 60}),
        )
