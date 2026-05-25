"""DB-backed store for third-party integration credentials.

Wraps the `integration_credentials` table (migration 0022): one row per
provider, the API key sealed at rest via `secrets.encrypted_field`. The
plaintext key only exists transiently — written through `encrypt` on
set, read back through `decrypt` when the push adapter needs it, and
never returned to the API surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ..secrets import decrypt, encrypt

if TYPE_CHECKING:
    import asyncpg


@dataclass(frozen=True)
class CredentialStatus:
    """Non-secret view of a stored credential for the Settings UI.

    Never carries the key itself — only whether one is configured and
    the result of the last connection test.
    """

    provider: str
    configured: bool
    last_validated_at: datetime | None
    validation_status: str | None


async def set_credential(
    conn: asyncpg.Connection, provider: str, plaintext: str
) -> None:
    """Encrypt and upsert the API key for `provider`.

    Resets `last_validated_at` / `validation_status` — a new key hasn't
    been tested yet.
    """
    ciphertext = encrypt(plaintext)
    await conn.execute(
        """
        INSERT INTO integration_credentials (provider, ciphertext, updated_at)
        VALUES ($1, $2, now())
        ON CONFLICT (provider) DO UPDATE SET
            ciphertext        = EXCLUDED.ciphertext,
            last_validated_at = NULL,
            validation_status = NULL,
            updated_at        = now()
        """,
        provider,
        ciphertext,
    )


async def get_credential_status(
    conn: asyncpg.Connection, provider: str
) -> CredentialStatus:
    """Return the non-secret status for `provider` (configured + validation)."""
    row = await conn.fetchrow(
        "SELECT last_validated_at, validation_status "
        "FROM integration_credentials WHERE provider = $1",
        provider,
    )
    if row is None:
        return CredentialStatus(
            provider=provider,
            configured=False,
            last_validated_at=None,
            validation_status=None,
        )
    return CredentialStatus(
        provider=provider,
        configured=True,
        last_validated_at=row["last_validated_at"],
        validation_status=row["validation_status"],
    )


async def get_credential_plaintext(
    conn: asyncpg.Connection, provider: str
) -> str | None:
    """Decrypt and return the stored API key, or None if unconfigured."""
    ciphertext = await conn.fetchval(
        "SELECT ciphertext FROM integration_credentials WHERE provider = $1",
        provider,
    )
    if ciphertext is None:
        return None
    return decrypt(ciphertext)


async def delete_credential(conn: asyncpg.Connection, provider: str) -> bool:
    """Remove the stored credential. Returns True if a row was deleted."""
    result = await conn.execute(
        "DELETE FROM integration_credentials WHERE provider = $1",
        provider,
    )
    # asyncpg returns e.g. "DELETE 1"
    return result.endswith("1")


async def record_validation(
    conn: asyncpg.Connection, provider: str, status: str
) -> None:
    """Stamp the last connection-test result for `provider`."""
    await conn.execute(
        "UPDATE integration_credentials "
        "SET last_validated_at = now(), validation_status = $2, updated_at = now() "
        "WHERE provider = $1",
        provider,
        status,
    )
