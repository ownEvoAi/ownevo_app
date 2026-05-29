"""Bearer-token authentication for the OTLP-JSON ingest receiver.

The receiver shipped without auth ("trusted network" posture). That is
fine for a localhost dev loop but unacceptable for any customer-facing
deployment: anyone who can reach `POST /api/otel/v1/traces` can write
into the workspace's `traces` table. This module closes the gap.

How a token flows
-----------------
1. An operator mints a token via `apps/kernel/scripts/mint_receiver_token.py`
   (`make mint-receiver-token WORKFLOW=... LABEL=...`). The script
   generates 32 random bytes, formats the plaintext as
   `ownevo_rt_<base64url(secret)>`, computes `sha256(secret)`, INSERTs
   the hash into `receiver_tokens`, and prints the plaintext **exactly
   once**. The plaintext is never written to disk.

2. The customer configures their OTLP collector with that plaintext
   in an `Authorization: Bearer <token>` header.

3. On every ingest request, `verify_request_token` reads the header,
   re-derives the hash, looks up the row, rejects on
   missing/revoked/unknown, and returns `ReceiverTokenAuth` carrying
   the bound `workflow_id` (or None for workflow-agnostic tokens).

4. `last_used_at` is touched best-effort; failures here are logged
   and swallowed because they are not security-relevant.

Why prefix the plaintext but hash only the suffix
-------------------------------------------------
The `ownevo_rt_` prefix is non-secret. Carrying it on the plaintext
lets operators grep deploy logs ("did this token appear in any
collector container?") without revealing the secret half. Server-side
we strip the prefix before hashing so a stolen `token_hash` cannot
be replayed by appending the prefix — the verifier requires the
prefix on the wire but does not feed it into SHA-256.

Why SHA-256, not HMAC
---------------------
HMAC's benefit over plain SHA-256 is resistance to length-extension
attacks. We never expose the hash output to an attacker (it lives
server-side, never on the wire), so length-extension is irrelevant.
SHA-256 of a 32-byte secret has 2^256 preimage resistance — overkill
for the actual threat model (offline cracking of a DB dump).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

_log = logging.getLogger(__name__)

# Plaintext format: ``ownevo_rt_<43 chars base64url>``. The prefix
# tags the secret as a *receiver* token, separating it from any
# future token kind (api key, demo invite, etc.) by inspection alone.
TOKEN_PREFIX = "ownevo_rt_"
SECRET_BYTES = 32

# Environment toggle. Production defaults to required (auth must be
# present and valid). Tests and local-dev flows that don't want to mint
# a token can opt out by setting OWNEVO_OTLP_AUTH_OPTIONAL=true; in that
# mode a missing Authorization header is allowed through and the route
# falls back to workflow-agnostic ingest (no workflow_id binding from
# the token). A *present-but-invalid* token still fails — opting out
# doesn't mean "anything goes", just "anonymous is allowed".
AUTH_OPTIONAL_ENV = "OWNEVO_OTLP_AUTH_OPTIONAL"


@dataclass(frozen=True)
class ReceiverTokenAuth:
    """Resolved identity for an authenticated OTLP ingest request.

    `token_id` is the `receiver_tokens.id` row, used by the route to
    update `last_used_at` and to attach the token row to audit-ish
    logs. `workflow_id` is the bound workflow (None on workflow-agnostic
    tokens — those callers must pass `?workflow_id=` on the request).
    `workspace_id` is the workspace the token operates in; the route
    binds the request connection to it before persisting traces.
    """

    token_id: str
    workflow_id: str | None
    workspace_id: str


class ReceiverTokenAuthError(Exception):
    """Base for every auth failure. The HTTP layer maps to 401."""


class MissingTokenError(ReceiverTokenAuthError):
    """No Authorization header. Allowed under AUTH_OPTIONAL_ENV=true."""


class MalformedTokenError(ReceiverTokenAuthError):
    """Header was present but the format was wrong (no prefix, wrong scheme)."""


class UnknownTokenError(ReceiverTokenAuthError):
    """The token hash isn't in the table — either fabricated or never minted."""


class RevokedTokenError(ReceiverTokenAuthError):
    """The token row exists but `revoked_at IS NOT NULL`."""


def mint_token() -> tuple[str, str]:
    """Generate a fresh token. Returns `(plaintext, token_hash)`.

    The plaintext is the value the operator hands to the customer; the
    hash is what the operator INSERTs into `receiver_tokens`. The
    plaintext is never stored — losing it means re-minting.
    """
    secret = secrets.token_bytes(SECRET_BYTES)
    secret_b64 = base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii")
    plaintext = f"{TOKEN_PREFIX}{secret_b64}"
    token_hash = hashlib.sha256(secret_b64.encode("ascii")).hexdigest()
    return plaintext, token_hash


def hash_token(plaintext: str) -> str:
    """Re-derive a stored hash from a wire-format token.

    Raises `MalformedTokenError` if the prefix is missing — the prefix
    is part of the format contract, not an optional shorthand.
    """
    if not plaintext.startswith(TOKEN_PREFIX):
        raise MalformedTokenError(
            f"token does not start with {TOKEN_PREFIX!r}"
        )
    secret = plaintext[len(TOKEN_PREFIX):]
    if not secret:
        raise MalformedTokenError("token has empty secret part")
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def _parse_bearer(header_value: str | None) -> str:
    """Extract the token from an `Authorization: Bearer <token>` header.

    Raises `MissingTokenError` when the header isn't present;
    `MalformedTokenError` when the scheme is wrong. The caller decides
    whether MissingTokenError is fatal (AUTH_OPTIONAL_ENV gate).
    """
    if header_value is None or not header_value.strip():
        raise MissingTokenError("no Authorization header")
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise MalformedTokenError("Authorization header is not Bearer-scheme")
    token = parts[1].strip()
    if not token:
        raise MalformedTokenError("empty bearer token")
    return token


def is_auth_optional() -> bool:
    """True when missing Authorization headers should be allowed through."""
    return os.environ.get(AUTH_OPTIONAL_ENV, "").lower() == "true"


async def verify_token(
    conn: asyncpg.Connection,
    plaintext: str,
) -> ReceiverTokenAuth:
    """Look up a wire-format token and return the resolved identity.

    Raises `UnknownTokenError` when the hash isn't in the table, and
    `RevokedTokenError` when the row's `revoked_at` is set. Both map to
    HTTP 401 at the route layer — we deliberately don't distinguish in
    the error response because telling an attacker "this token exists
    but is revoked" leaks information.

    `last_used_at` is updated in the same SELECT-then-UPDATE; the
    update is best-effort and swallowed on failure (logged at WARNING)
    because a busy table dropping a write here is not a security
    failure.
    """
    token_hash = hash_token(plaintext)

    row = await conn.fetchrow(
        """
        SELECT id::text AS id,
               workflow_id,
               workspace_id,
               revoked_at
        FROM receiver_tokens
        WHERE token_hash = $1
        """,
        token_hash,
    )
    if row is None:
        raise UnknownTokenError("token not found")
    if row["revoked_at"] is not None:
        raise RevokedTokenError("token has been revoked")

    try:
        await conn.execute(
            "UPDATE receiver_tokens SET last_used_at = NOW() WHERE id = $1::uuid",
            row["id"],
        )
    except Exception as exc:  # pragma: no cover - best-effort logging
        _log.warning("receiver_tokens: last_used_at update failed: %s", exc)

    return ReceiverTokenAuth(
        token_id=row["id"],
        workflow_id=row["workflow_id"],
        workspace_id=row["workspace_id"],
    )


async def verify_request_token(
    conn: asyncpg.Connection,
    authorization_header: str | None,
) -> ReceiverTokenAuth | None:
    """Verify the Authorization header on an OTLP ingest request.

    Returns the resolved `ReceiverTokenAuth` on a valid token. Returns
    None when no header is present AND `OWNEVO_OTLP_AUTH_OPTIONAL=true`
    is set (the test/local-dev escape valve).

    Raises `ReceiverTokenAuthError` (or a subclass) on every other
    failure path — the route maps that to HTTP 401 uniformly.
    """
    try:
        plaintext = _parse_bearer(authorization_header)
    except MissingTokenError:
        if is_auth_optional():
            return None
        raise
    return await verify_token(conn, plaintext)
