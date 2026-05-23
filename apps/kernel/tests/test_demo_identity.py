"""Unit tests for the demo invite mint/verify codec and token accountant.

These cover the cryptographic + structural guarantees of the token: a
correct sign-then-verify roundtrip, rejection of expired tokens,
rejection of tampered signatures, and rejection of malformed payloads.
DB-backed flows are covered in `test_demo_routes.py`.

Also covers `TokenAccountant` / `wrap_client_for_accounting` — pure unit
tests with no DB or network needed.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from ownevo_kernel.api._demo_identity import (
    InviteInvalid,
    mint_invite_token,
    verify_invite_token,
)
from ownevo_kernel.api._demo_token_accountant import (
    TokenAccountant,
    wrap_client_for_accounting,
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


def test_verify_rejects_missing_claim() -> None:
    """A well-signed token with a missing required claim must be rejected."""
    import base64
    import hmac
    import json
    from hashlib import sha256

    # Build a token that has a valid signature but is missing `jti`.
    claims = {"label": "x", "tier": "elevated", "exp": int(time.time()) + 86400}
    payload_bytes = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    payload_s = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    sig_bytes = hmac.new(b"k0", payload_s.encode(), sha256).digest()
    sig_s = base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode("ascii")
    token = f"{payload_s}.{sig_s}"
    with pytest.raises(InviteInvalid, match="missing claim"):
        verify_invite_token(token, "k0")


# ---------------------------------------------------------------------------
# TokenAccountant + wrap_client_for_accounting — pure unit tests
# ---------------------------------------------------------------------------


async def test_accountant_accumulates_across_calls() -> None:
    """Usage from multiple calls on the same client must accumulate."""
    msg1 = MagicMock()
    msg1.usage.input_tokens = 100
    msg1.usage.output_tokens = 50
    msg2 = MagicMock()
    msg2.usage.input_tokens = 200
    msg2.usage.output_tokens = 75
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=[msg1, msg2])
    acc = TokenAccountant()
    wrap_client_for_accounting(client, acc)
    await client.messages.create()
    await client.messages.create()
    assert acc.input_tokens == 300
    assert acc.output_tokens == 125


async def test_accountant_does_not_bleed_across_client_instances() -> None:
    """Patching one client must not affect a separate unpatched client."""
    msg = MagicMock()
    msg.usage.input_tokens = 10
    msg.usage.output_tokens = 5
    client_a = MagicMock()
    client_a.messages.create = AsyncMock(return_value=msg)
    client_b = MagicMock()
    client_b.messages.create = AsyncMock(return_value=msg)

    acc_a = TokenAccountant()
    wrap_client_for_accounting(client_a, acc_a)
    # client_b is deliberately NOT wrapped.

    await client_a.messages.create()
    await client_b.messages.create()

    assert acc_a.input_tokens == 10
    assert acc_a.output_tokens == 5


async def test_accountant_tolerates_missing_usage_attr() -> None:
    """If the response has no usage attribute, recording must not raise."""
    msg = MagicMock(spec=[])  # no attributes
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=msg)
    acc = TokenAccountant()
    wrap_client_for_accounting(client, acc)
    await client.messages.create()
    assert acc.input_tokens == 0
    assert acc.output_tokens == 0
