"""Unit tests for the demo invite mint/verify codec.

These cover the cryptographic + structural guarantees of the token: a
correct sign-then-verify roundtrip, rejection of expired tokens,
rejection of tampered signatures, and rejection of malformed payloads.
DB-backed flows are covered in `test_demo_routes.py`.
"""

from __future__ import annotations

import time

import pytest
from ownevo_kernel.api._demo_identity import (
    InviteInvalid,
    mint_invite_token,
    verify_invite_token,
)


def test_mint_verify_roundtrip() -> None:
    token = mint_invite_token(
        label="acme-pilot", tier="elevated", ttl_days=30, signing_key="k0"
    )
    claims = verify_invite_token(token, "k0")
    assert claims["label"] == "acme-pilot"
    assert claims["tier"] == "elevated"
    assert isinstance(claims["jti"], str) and claims["jti"]
    assert claims["exp"] > int(time.time())


def test_mint_rejects_invalid_tier() -> None:
    with pytest.raises(ValueError, match="invite tier must be"):
        mint_invite_token(label="x", tier="anonymous", ttl_days=1, signing_key="k0")  # type: ignore[arg-type]


def test_mint_rejects_zero_ttl() -> None:
    with pytest.raises(ValueError, match="ttl_days must be positive"):
        mint_invite_token(label="x", tier="elevated", ttl_days=0, signing_key="k0")


def test_mint_requires_signing_key() -> None:
    with pytest.raises(RuntimeError, match="OWNEVO_DEMO_SIGNING_KEY"):
        # No env var, no explicit key — refuses to mint.
        mint_invite_token(label="x", tier="elevated", ttl_days=1, signing_key=None)


def test_verify_rejects_expired() -> None:
    token = mint_invite_token(
        label="x",
        tier="elevated",
        ttl_days=1,
        signing_key="k0",
        issued_at=int(time.time()) - 2 * 86400,
    )
    with pytest.raises(InviteInvalid, match="expired"):
        verify_invite_token(token, "k0")


def test_verify_rejects_tampered_signature() -> None:
    token = mint_invite_token(
        label="x", tier="elevated", ttl_days=1, signing_key="k0"
    )
    payload, sig = token.split(".")
    bad = f"{payload}.{'A' * len(sig)}"
    with pytest.raises(InviteInvalid, match="bad signature"):
        verify_invite_token(bad, "k0")


def test_verify_rejects_wrong_signing_key() -> None:
    token = mint_invite_token(
        label="x", tier="elevated", ttl_days=1, signing_key="k0"
    )
    with pytest.raises(InviteInvalid, match="bad signature"):
        verify_invite_token(token, "k-different")


def test_verify_rejects_malformed_token() -> None:
    with pytest.raises(InviteInvalid, match="malformed token"):
        verify_invite_token("not-a-token", "k0")
