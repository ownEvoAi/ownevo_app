"""DB-backed store for registered MCP servers (`mcp_servers`, migration 0026).

The secret half of a server's auth (bearer token, OAuth tokens,
service-principal client secret) is serialized to JSON and sealed with the
app credentials master key before storage; the plaintext is never persisted
and is decrypted only when a call needs to mint headers. Everything the admin
UI shows — endpoint, provider, non-secret auth_config, validation status —
comes back through `MCPServer`, which deliberately omits the secret.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ..secrets import decrypt, encrypt
from .models import AuthKind, MCPServer, MCPServerRegistration, Transport

if TYPE_CHECKING:
    import asyncpg


def _row_to_server(row: asyncpg.Record) -> MCPServer:
    auth_config = row["auth_config"]
    if isinstance(auth_config, str):
        auth_config = json.loads(auth_config)
    return MCPServer(
        id=row["id"],
        name=row["name"],
        provider=row["provider"],
        endpoint_url=row["endpoint_url"],
        transport=Transport(row["transport"]),
        auth_kind=AuthKind(row["auth_kind"]),
        auth_config=auth_config or {},
        status=row["status"],
        has_secret=row["auth_secret_ciphertext"] is not None,
        last_validated_at=(
            row["last_validated_at"].isoformat() if row["last_validated_at"] else None
        ),
        validation_status=row["validation_status"],
    )


async def register_server(
    conn: asyncpg.Connection, registration: MCPServerRegistration
) -> MCPServer:
    """Insert (or replace, by name) a server. Seals the secret blob at rest.

    Upsert keyed on `name` so re-running an integration's connect flow updates
    the existing row rather than erroring on the unique constraint.
    """
    ciphertext = (
        encrypt(json.dumps(registration.auth_secret, sort_keys=True))
        if registration.auth_secret is not None
        else None
    )
    row = await conn.fetchrow(
        """
        INSERT INTO mcp_servers (
            name, provider, endpoint_url, transport, auth_kind,
            auth_config, auth_secret_ciphertext, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, now())
        ON CONFLICT (name) DO UPDATE SET
            provider               = EXCLUDED.provider,
            endpoint_url           = EXCLUDED.endpoint_url,
            transport              = EXCLUDED.transport,
            auth_kind              = EXCLUDED.auth_kind,
            auth_config            = EXCLUDED.auth_config,
            auth_secret_ciphertext = EXCLUDED.auth_secret_ciphertext,
            last_validated_at      = NULL,
            validation_status      = NULL,
            updated_at             = now()
        RETURNING *
        """,
        registration.name,
        registration.provider,
        registration.endpoint_url,
        registration.transport.value,
        registration.auth_kind.value,
        json.dumps(registration.auth_config),
        ciphertext,
    )
    return _row_to_server(row)


async def get_server(conn: asyncpg.Connection, server_id: UUID) -> MCPServer | None:
    row = await conn.fetchrow("SELECT * FROM mcp_servers WHERE id = $1", server_id)
    return _row_to_server(row) if row is not None else None


async def list_servers(conn: asyncpg.Connection) -> list[MCPServer]:
    rows = await conn.fetch("SELECT * FROM mcp_servers ORDER BY created_at")
    return [_row_to_server(r) for r in rows]


async def delete_server(conn: asyncpg.Connection, server_id: UUID) -> bool:
    """Remove a server. Returns True if a row was deleted."""
    result = await conn.execute("DELETE FROM mcp_servers WHERE id = $1", server_id)
    return result.endswith("1")


async def get_auth_secret(
    conn: asyncpg.Connection, server_id: UUID
) -> dict[str, Any] | None:
    """Decrypt and return a server's secret blob, or None if it stores none."""
    ciphertext = await conn.fetchval(
        "SELECT auth_secret_ciphertext FROM mcp_servers WHERE id = $1", server_id
    )
    if ciphertext is None:
        return None
    return json.loads(decrypt(ciphertext))


async def update_auth_secret(
    conn: asyncpg.Connection, server_id: UUID, secret: dict[str, Any]
) -> None:
    """Persist a refreshed/minted token blob so the next call reuses it."""
    ciphertext = encrypt(json.dumps(secret, sort_keys=True))
    await conn.execute(
        "UPDATE mcp_servers SET auth_secret_ciphertext = $2, updated_at = now() "
        "WHERE id = $1",
        server_id,
        ciphertext,
    )


async def record_validation(
    conn: asyncpg.Connection, server_id: UUID, status: str
) -> None:
    """Stamp the last connection-test result for the Settings UI."""
    await conn.execute(
        "UPDATE mcp_servers SET last_validated_at = now(), validation_status = $2, "
        "updated_at = now() WHERE id = $1",
        server_id,
        status,
    )


__all__ = [
    "delete_server",
    "get_auth_secret",
    "get_server",
    "list_servers",
    "record_validation",
    "register_server",
    "update_auth_secret",
]
