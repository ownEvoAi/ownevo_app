"""Tests for receiver-token auth.

Two layers: pure-Python helpers (mint / hash / parse, no DB) and
DB-backed verify path (insert a hash, look it up, exercise revoked
+ unknown cases).
"""

from __future__ import annotations

import base64
import hashlib
import os

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.middleware.otel_receiver.auth import (
    AUTH_OPTIONAL_ENV,
    SECRET_BYTES,
    TOKEN_PREFIX,
    MalformedTokenError,
    MissingTokenError,
    RevokedTokenError,
    UnknownTokenError,
    _parse_bearer,
    hash_token,
    is_auth_optional,
    mint_token,
    verify_request_token,
    verify_token,
)

# ---------------------------------------------------------------------------
# Pure-Python tests (no DB)
# ---------------------------------------------------------------------------


def test_mint_token_returns_prefixed_plaintext_and_matching_hash() -> None:
    plaintext, token_hash = mint_token()
    assert plaintext.startswith(TOKEN_PREFIX)
    secret = plaintext[len(TOKEN_PREFIX):]
    # base64url with padding stripped is 4*ceil(n/3) ≈ 43 chars for 32 bytes.
    raw = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    assert len(raw) == SECRET_BYTES
    expected_hash = hashlib.sha256(secret.encode("ascii")).hexdigest()
    assert token_hash == expected_hash


def test_mint_token_is_unique_per_call() -> None:
    a, ah = mint_token()
    b, bh = mint_token()
    assert a != b
    assert ah != bh


def test_hash_token_roundtrips_mint() -> None:
    plaintext, expected = mint_token()
    assert hash_token(plaintext) == expected


def test_hash_token_rejects_missing_prefix() -> None:
    with pytest.raises(MalformedTokenError):
        hash_token("AAAAAAAAAAAAAAAAAA")


def test_hash_token_rejects_empty_secret() -> None:
    with pytest.raises(MalformedTokenError):
        hash_token(TOKEN_PREFIX)


def test_parse_bearer_extracts_token() -> None:
    assert _parse_bearer("Bearer abc123") == "abc123"
    assert _parse_bearer("bearer abc123") == "abc123"


def test_parse_bearer_missing_header() -> None:
    with pytest.raises(MissingTokenError):
        _parse_bearer(None)
    with pytest.raises(MissingTokenError):
        _parse_bearer("")
    with pytest.raises(MissingTokenError):
        _parse_bearer("   ")


def test_parse_bearer_wrong_scheme() -> None:
    with pytest.raises(MalformedTokenError):
        _parse_bearer("Basic abc123")
    with pytest.raises(MalformedTokenError):
        _parse_bearer("abc123")  # no scheme at all


def test_parse_bearer_empty_token_after_scheme() -> None:
    with pytest.raises(MalformedTokenError):
        _parse_bearer("Bearer ")


def test_is_auth_optional_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_OPTIONAL_ENV, "true")
    assert is_auth_optional() is True
    monkeypatch.setenv(AUTH_OPTIONAL_ENV, "TRUE")
    assert is_auth_optional() is True
    monkeypatch.setenv(AUTH_OPTIONAL_ENV, "false")
    assert is_auth_optional() is False
    monkeypatch.delenv(AUTH_OPTIONAL_ENV, raising=False)
    assert is_auth_optional() is False


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping DB-backed auth tests",
)


@pytestmark_db
async def test_verify_token_accepts_a_minted_token(db: asyncpg.Connection) -> None:
    plaintext, token_hash = mint_token()
    row = await db.fetchrow(
        """
        INSERT INTO receiver_tokens (token_hash, label)
        VALUES ($1, 'unit-test')
        RETURNING id::text AS id
        """,
        token_hash,
    )
    assert row is not None

    auth = await verify_token(db, plaintext)
    assert auth.token_id == row["id"]
    assert auth.workflow_id is None


@pytestmark_db
async def test_verify_token_updates_last_used_at(db: asyncpg.Connection) -> None:
    plaintext, token_hash = mint_token()
    await db.execute(
        "INSERT INTO receiver_tokens (token_hash, label) VALUES ($1, 'unit-test')",
        token_hash,
    )

    before = await db.fetchval(
        "SELECT last_used_at FROM receiver_tokens WHERE token_hash = $1",
        token_hash,
    )
    assert before is None

    await verify_token(db, plaintext)

    after = await db.fetchval(
        "SELECT last_used_at FROM receiver_tokens WHERE token_hash = $1",
        token_hash,
    )
    assert after is not None


@pytestmark_db
async def test_verify_token_rejects_unknown(db: asyncpg.Connection) -> None:
    plaintext, _ = mint_token()
    with pytest.raises(UnknownTokenError):
        await verify_token(db, plaintext)


@pytestmark_db
async def test_verify_token_rejects_revoked(db: asyncpg.Connection) -> None:
    plaintext, token_hash = mint_token()
    await db.execute(
        """
        INSERT INTO receiver_tokens (token_hash, label, revoked_at)
        VALUES ($1, 'unit-test', NOW())
        """,
        token_hash,
    )
    with pytest.raises(RevokedTokenError):
        await verify_token(db, plaintext)


@pytestmark_db
async def test_verify_request_token_returns_none_when_optional(
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTH_OPTIONAL_ENV, "true")
    result = await verify_request_token(db, None)
    assert result is None


@pytestmark_db
async def test_verify_request_token_required_rejects_missing(
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(AUTH_OPTIONAL_ENV, raising=False)
    with pytest.raises(MissingTokenError):
        await verify_request_token(db, None)


@pytestmark_db
async def test_verify_request_token_rejects_malformed_even_when_optional(
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Opting auth out does NOT mean "anything goes" — a present-but-wrong
    # header still fails. Otherwise an attacker could send garbage and
    # still get the route to process their body.
    monkeypatch.setenv(AUTH_OPTIONAL_ENV, "true")
    with pytest.raises(MalformedTokenError):
        await verify_request_token(db, "Bearer not-a-real-token")


@pytestmark_db
async def test_workflow_id_binding_round_trips(db: asyncpg.Connection) -> None:
    # Insert a workflow so the FK is satisfiable.
    await db.execute(
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ('wf-auth-test', 'test wf', '{}'::jsonb)
        """,
    )
    plaintext, token_hash = mint_token()
    await db.execute(
        """
        INSERT INTO receiver_tokens (token_hash, label, workflow_id)
        VALUES ($1, 'unit-test', 'wf-auth-test')
        """,
        token_hash,
    )

    auth = await verify_token(db, plaintext)
    assert auth.workflow_id == "wf-auth-test"
