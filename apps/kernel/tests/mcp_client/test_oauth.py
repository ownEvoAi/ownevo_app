"""Tests for the MCP OAuth authorization-code flow.

Unit coverage (presets, authorize URL, code exchange) needs no DB. The state +
client-credential helpers are DB-backed and need a master key.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.mcp_client import (
    AuthKind,
    build_authorize_url,
    exchange_code,
    get_preset,
)
from ownevo_kernel.mcp_client import oauth as oauth_flow
from ownevo_kernel.mcp_client.providers import UnknownProvider, all_presets


class _FakeTokenFetcher:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list = []

    async def __call__(self, token_url, data):
        self.calls.append((token_url, data))
        return self.response


# ---------------------------------------------------------------------------
# Presets + authorize URL (no DB)
# ---------------------------------------------------------------------------


def test_three_first_class_providers_present() -> None:
    ids = {p.provider for p in all_presets()}
    assert ids == {"slack", "google_workspace", "microsoft_365"}


def test_unknown_provider_raises() -> None:
    with pytest.raises(UnknownProvider):
        get_preset("dropbox")


def test_authorize_url_has_standard_params() -> None:
    preset = get_preset("slack")
    url = build_authorize_url(
        preset,
        client_id="cid",
        redirect_uri="https://api.test/cb",
        scopes=["channels:read", "chat:write"],
        state="st8",
    )
    q = parse_qs(urlparse(url).query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["cid"]
    assert q["redirect_uri"] == ["https://api.test/cb"]
    assert q["scope"] == ["channels:read chat:write"]
    assert q["state"] == ["st8"]


def test_google_authorize_url_requests_offline_refresh() -> None:
    preset = get_preset("google_workspace")
    url = build_authorize_url(
        preset,
        client_id="c",
        redirect_uri="https://api.test/cb",
        scopes=list(preset.default_scopes),
        state="s",
    )
    q = parse_qs(urlparse(url).query)
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]


def test_microsoft_authorize_url_interpolates_tenant() -> None:
    preset = get_preset("microsoft_365")
    url = build_authorize_url(
        preset,
        client_id="c",
        redirect_uri="https://api.test/cb",
        scopes=list(preset.default_scopes),
        state="s",
        tenant="contoso",
    )
    assert "login.microsoftonline.com/contoso/" in url


def test_microsoft_authorize_url_defaults_tenant_to_common() -> None:
    preset = get_preset("microsoft_365")
    url = build_authorize_url(
        preset, client_id="c", redirect_uri="r", scopes=[], state="s", tenant=None
    )
    assert "login.microsoftonline.com/common/" in url


# ---------------------------------------------------------------------------
# Code exchange (no DB)
# ---------------------------------------------------------------------------


async def test_exchange_with_refresh_token_yields_oauth() -> None:
    preset = get_preset("google_workspace")
    now = datetime(2026, 5, 25, tzinfo=UTC)
    fetcher = _FakeTokenFetcher(
        {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    )
    result = await exchange_code(
        preset,
        client_id="cid",
        client_secret="csec",
        code="authcode",
        redirect_uri="https://api.test/cb",
        scopes=["a", "b"],
        token_fetcher=fetcher,
        now=now,
    )
    assert result.auth_kind is AuthKind.OAUTH
    assert result.auth_secret["access_token"] == "at"
    assert result.auth_secret["refresh_token"] == "rt"
    assert result.auth_secret["client_secret"] == "csec"  # kept for refresh
    assert result.auth_config["token_url"].endswith("/token")
    assert result.auth_config["scopes"] == ["a", "b"]
    _, data = fetcher.calls[0]
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "authcode"
    assert data["redirect_uri"] == "https://api.test/cb"


async def test_exchange_without_refresh_token_yields_bearer() -> None:
    preset = get_preset("slack")
    fetcher = _FakeTokenFetcher({"access_token": "xoxb-static"})
    result = await exchange_code(
        preset,
        client_id="cid",
        client_secret="csec",
        code="c",
        redirect_uri="r",
        scopes=[],
        token_fetcher=fetcher,
    )
    assert result.auth_kind is AuthKind.BEARER
    assert result.auth_secret == {"token": "xoxb-static"}


async def test_exchange_missing_access_token_raises() -> None:
    preset = get_preset("slack")
    with pytest.raises(ValueError):
        await exchange_code(
            preset,
            client_id="c",
            client_secret="s",
            code="c",
            redirect_uri="r",
            scopes=[],
            token_fetcher=_FakeTokenFetcher({"ok": False}),
        )


# ---------------------------------------------------------------------------
# DB-backed: client creds + state
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping DB-backed oauth tests",
)


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from ownevo_kernel.secrets import generate_master_key

    monkeypatch.setenv("OWNEVO_CREDENTIALS_MASTER_KEY", generate_master_key())


@pytestmark_db
async def test_client_creds_round_trip_encrypted(db: asyncpg.Connection) -> None:
    await oauth_flow.set_oauth_client(
        db, "microsoft_365", client_id="app", client_secret="shh", config={"tenant": "t1"}
    )
    status = await oauth_flow.get_oauth_client_status(db, "microsoft_365")
    assert status.configured is True
    assert status.client_id == "app"
    assert status.config["tenant"] == "t1"
    assert await oauth_flow.get_oauth_client_secret(db, "microsoft_365") == "shh"

    raw = await db.fetchval(
        "SELECT client_secret_ciphertext FROM mcp_oauth_clients WHERE provider=$1",
        "microsoft_365",
    )
    assert "shh" not in raw and raw.startswith("v1:")

    assert await oauth_flow.delete_oauth_client(db, "microsoft_365") is True
    assert (await oauth_flow.get_oauth_client_status(db, "microsoft_365")).configured is False


@pytestmark_db
async def test_state_create_then_consume_single_use(db: asyncpg.Connection) -> None:
    state = await oauth_flow.create_state(
        db,
        provider="slack",
        server_name="acme-slack",
        scopes=["channels:read"],
        endpoint_url="https://mcp.test/slack",
    )
    consumed = await oauth_flow.consume_state(db, state)
    assert consumed is not None
    assert consumed.provider == "slack"
    assert consumed.server_name == "acme-slack"
    assert consumed.scopes == ["channels:read"]
    # Single-use: a replay finds nothing.
    assert await oauth_flow.consume_state(db, state) is None


@pytestmark_db
async def test_expired_state_is_rejected(db: asyncpg.Connection) -> None:
    state = await oauth_flow.create_state(
        db,
        provider="slack",
        server_name="s",
        scopes=[],
        endpoint_url="https://mcp.test/slack",
    )
    future = datetime.now(UTC) + timedelta(hours=1)
    assert await oauth_flow.consume_state(db, state, now=future) is None
