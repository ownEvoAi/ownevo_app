"""Structured credential storage for the Copilot Studio integration.

LangSmith needs one secret (an API key), so it reuses
`_integration_credentials` directly. Copilot Studio needs four
(`tenant_id`, `client_id`, `client_secret`, `environment_url`) plus an
optional `authority_host` for sovereign clouds — a structured credential,
not a scalar. Rather than widen the single-`ciphertext` schema, we seal
the credential as one JSON blob into that same column: the structure
lives in code here, the table stays provider-agnostic, and the secret is
still encrypted at rest as a single opaque string.

Only `client_secret` is truly secret, but the whole blob is encrypted so
a DB leak doesn't even reveal the tenant / app-registration identifiers.
The non-secret status (configured + last test result) is reported through
the shared `_integration_credentials.get_credential_status`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..middleware.copilot_studio import CopilotStudioConfigError, CopilotStudioCredentials
from ..middleware.copilot_studio.auth import DEFAULT_AUTHORITY_HOST
from ._integration_credentials import get_credential_plaintext, set_credential

if TYPE_CHECKING:
    import asyncpg

PROVIDER = "copilot_studio"


async def set_copilot_studio_credential(
    conn: asyncpg.Connection, cred: CopilotStudioCredentials
) -> None:
    """Seal the structured Copilot Studio credential as an encrypted JSON blob."""
    blob = json.dumps(
        {
            "tenant_id": cred.tenant_id,
            "client_id": cred.client_id,
            "client_secret": cred.client_secret,
            "environment_url": cred.environment_url,
            "authority_host": cred.authority_host,
        }
    )
    await set_credential(conn, PROVIDER, blob)


async def get_copilot_studio_credential(
    conn: asyncpg.Connection,
) -> CopilotStudioCredentials | None:
    """Decrypt and parse the stored credential, or None if unconfigured.

    Raises `CopilotStudioConfigError` when the stored blob is malformed or
    missing a required field — i.e. corrupted at rest, never the
    not-configured case (which returns None).
    """
    plaintext = await get_credential_plaintext(conn, PROVIDER)
    if plaintext is None:
        return None
    try:
        data = json.loads(plaintext)
    except ValueError as exc:
        raise CopilotStudioConfigError(
            "Stored Copilot Studio credential is not valid JSON"
        ) from exc
    try:
        return CopilotStudioCredentials(
            tenant_id=data["tenant_id"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            environment_url=data["environment_url"],
            # Older blobs (and the public-cloud default) may omit the host.
            authority_host=data.get("authority_host") or DEFAULT_AUTHORITY_HOST,
        )
    except KeyError as exc:
        raise CopilotStudioConfigError(
            f"Stored Copilot Studio credential is missing field {exc}"
        ) from exc
