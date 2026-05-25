"""DB-backed tests for the mcp_servers registry — secret sealing + CRUD.

Requires a real Postgres (the `db` fixture) and a credentials master key
(set per-test) so the encrypted secret blob round-trips.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.mcp_client import (
    AuthKind,
    MCPServerRegistration,
    Transport,
    registry,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping mcp_client registry tests",
)


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from ownevo_kernel.secrets import generate_master_key

    monkeypatch.setenv("OWNEVO_CREDENTIALS_MASTER_KEY", generate_master_key())


def _registration(**overrides) -> MCPServerRegistration:
    base = dict(
        name="acme-slack",
        provider="slack",
        endpoint_url="https://mcp.acme.test/slack",
        transport=Transport.STREAMABLE_HTTP,
        auth_kind=AuthKind.BEARER,
        auth_config={},
        auth_secret={"token": "xoxb-secret"},
    )
    base.update(overrides)
    return MCPServerRegistration(**base)


async def test_register_then_get_omits_secret(db: asyncpg.Connection) -> None:
    server = await registry.register_server(db, _registration())
    assert server.name == "acme-slack"
    assert server.has_secret is True
    # The view never carries the secret itself.
    assert not hasattr(server, "auth_secret")

    fetched = await registry.get_server(db, server.id)
    assert fetched is not None
    assert fetched.id == server.id
    assert fetched.provider == "slack"


async def test_secret_round_trips_via_encrypted_column(db: asyncpg.Connection) -> None:
    server = await registry.register_server(db, _registration())
    secret = await registry.get_auth_secret(db, server.id)
    assert secret == {"token": "xoxb-secret"}

    # The raw column is ciphertext, not the plaintext token.
    raw = await db.fetchval(
        "SELECT auth_secret_ciphertext FROM mcp_servers WHERE id = $1", server.id
    )
    assert raw is not None
    assert "xoxb-secret" not in raw
    assert raw.startswith("v1:")


async def test_register_upserts_by_name(db: asyncpg.Connection) -> None:
    first = await registry.register_server(db, _registration())
    second = await registry.register_server(
        db, _registration(endpoint_url="https://mcp.acme.test/slack-v2")
    )
    assert first.id == second.id  # same row, replaced
    assert second.endpoint_url.endswith("slack-v2")
    servers = await registry.list_servers(db)
    assert len([s for s in servers if s.name == "acme-slack"]) == 1


async def test_none_auth_stores_no_secret(db: asyncpg.Connection) -> None:
    server = await registry.register_server(
        db,
        _registration(
            name="public-srv", auth_kind=AuthKind.NONE, auth_secret=None
        ),
    )
    assert server.has_secret is False
    assert await registry.get_auth_secret(db, server.id) is None


async def test_update_auth_secret_and_validation(db: asyncpg.Connection) -> None:
    server = await registry.register_server(db, _registration())
    await registry.update_auth_secret(db, server.id, {"token": "rotated"})
    assert await registry.get_auth_secret(db, server.id) == {"token": "rotated"}

    await registry.record_validation(db, server.id, "ok")
    fetched = await registry.get_server(db, server.id)
    assert fetched.validation_status == "ok"
    assert fetched.last_validated_at is not None


async def test_delete_server(db: asyncpg.Connection) -> None:
    server = await registry.register_server(db, _registration())
    assert await registry.delete_server(db, server.id) is True
    assert await registry.get_server(db, server.id) is None
    assert await registry.delete_server(db, server.id) is False


async def test_auth_config_persists(db: asyncpg.Connection) -> None:
    server = await registry.register_server(
        db,
        _registration(
            name="entra-365",
            provider="microsoft_365",
            auth_kind=AuthKind.SERVICE_PRINCIPAL,
            auth_config={
                "token_url": "https://login.test/oauth2/token",
                "client_id": "app-1",
                "scopes": ["https://graph.test/.default"],
            },
            auth_secret={"client_secret": "shh"},
        ),
    )
    fetched = await registry.get_server(db, server.id)
    assert fetched.auth_config["client_id"] == "app-1"
    assert fetched.auth_kind is AuthKind.SERVICE_PRINCIPAL
