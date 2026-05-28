"""Symmetric encryption for credential fields stored in Postgres.

Third-party integration credentials — the LangSmith API key first —
must not sit in the database as plaintext: a DB dump or a read-replica
leak would hand an attacker the customer's vendor account. This module
seals them with Fernet (AES-128-CBC + HMAC-SHA256) under a single
app-level master key held only in the environment, never in the DB.

Master key
----------
`OWNEVO_CREDENTIALS_MASTER_KEY` holds a Fernet key (32 url-safe base64
bytes). Generate one with `make gen-credentials-key` (or
`generate_master_key()`), store it in the deployment's secret manager,
and keep it out of the repo. Losing the key makes every stored
ciphertext unrecoverable — the customer must re-enter their API keys.

Storage format
--------------
Ciphertext is stored as `v1:<fernet-token>`. The `v1:` prefix is a
scheme version reserved for future key rotation (a `v2:` could mark
tokens sealed under a rotated key); today only `v1` exists. Fernet's
own token already carries an internal version byte and timestamp, so
the prefix is purely our scheme marker.

This module does no DB I/O — callers pass plaintext in and store the
returned string. Keeping it I/O-free makes it trivially unit-testable
and reusable by both the API layer and the push adapter.
"""

from __future__ import annotations

import os

MASTER_KEY_ENV = "OWNEVO_CREDENTIALS_MASTER_KEY"
_SCHEME_PREFIX = "v1:"


class CredentialsKeyMissingError(RuntimeError):
    """`OWNEVO_CREDENTIALS_MASTER_KEY` is unset or malformed."""


class CredentialsDecryptError(RuntimeError):
    """Ciphertext failed to decrypt — tampered, truncated, or wrong key."""


def _load_fernet():  # -> cryptography.fernet.Fernet
    """Build a Fernet from the env master key, or raise a typed error.

    Imported lazily so importing this module doesn't hard-require the
    `cryptography` dep on code paths that never touch credentials.
    """
    raw = os.environ.get(MASTER_KEY_ENV)
    if not raw:
        raise CredentialsKeyMissingError(
            f"{MASTER_KEY_ENV} is not set. Generate one with "
            "`make gen-credentials-key` and store it in the deployment's "
            "secret manager.",
        )
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise CredentialsKeyMissingError(
            "the `cryptography` package is required for credential encryption",
        ) from exc
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise CredentialsKeyMissingError(
            f"{MASTER_KEY_ENV} is not a valid Fernet key "
            "(expected 32 url-safe base64-encoded bytes).",
        ) from exc


def generate_master_key() -> str:
    """Return a fresh Fernet master key (url-safe base64 string)."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


def encrypt(plaintext: str) -> str:
    """Seal `plaintext` and return a `v1:`-prefixed storable string.

    Raises `CredentialsKeyMissingError` when the master key is unset.
    """
    fernet = _load_fernet()
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_SCHEME_PREFIX}{token}"


def decrypt(ciphertext: str) -> str:
    """Recover the plaintext from a `v1:`-prefixed stored string.

    Raises `CredentialsKeyMissingError` when the master key is unset and
    `CredentialsDecryptError` when the token is tampered, truncated, or
    sealed under a different key.
    """
    if not ciphertext.startswith(_SCHEME_PREFIX):
        raise CredentialsDecryptError(
            f"ciphertext is missing the {_SCHEME_PREFIX!r} scheme prefix",
        )
    token = ciphertext[len(_SCHEME_PREFIX):]
    fernet = _load_fernet()
    try:
        from cryptography.fernet import InvalidToken

        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialsDecryptError(
            "ciphertext failed to decrypt — tampered, truncated, or wrong key",
        ) from exc
