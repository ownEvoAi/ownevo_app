"""Unit tests for the web->kernel identity assertion (no DB, no app)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from ownevo_kernel.api._internal_auth import (
    DEFAULT_WORKSPACE_ID,
    DEV_USER_ID,
    AssertionInvalid,
    Principal,
    bearer_token,
    dev_auth_enabled,
    dev_principal,
    mint_workspace_assertion,
    verify_internal_service_token,
    verify_workspace_assertion,
)

_KEY = "test-internal-key"


def _mint(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "user_id": "alice",
        "workspace_id": "acme",
        "ttl_seconds": 300,
        "signing_key": _KEY,
    }
    kwargs.update(overrides)
    return mint_workspace_assertion(**kwargs)  # type: ignore[arg-type]


def test_mint_verify_roundtrip() -> None:
    token = _mint()
    principal = verify_workspace_assertion(token, _KEY)
    assert principal == Principal(user_id="alice", workspace_id="acme")


def test_verify_rejects_expired() -> None:
    token = _mint(ttl_seconds=1, issued_at=int(time.time()) - 3600)
    with pytest.raises(AssertionInvalid, match="expired"):
        verify_workspace_assertion(token, _KEY)


def test_verify_rejects_wrong_key() -> None:
    token = _mint()
    with pytest.raises(AssertionInvalid, match="bad signature"):
        verify_workspace_assertion(token, "a-different-key")


def test_verify_rejects_tampered_payload() -> None:
    token = _mint()
    payload_s, _, sig_s = token.partition(".")
    tampered = payload_s[:-1] + ("A" if payload_s[-1] != "A" else "B")
    with pytest.raises(AssertionInvalid, match="bad signature"):
        verify_workspace_assertion(f"{tampered}.{sig_s}", _KEY)


def test_verify_rejects_malformed_token() -> None:
    with pytest.raises(AssertionInvalid, match="malformed token"):
        verify_workspace_assertion("no-dot-here", _KEY)


def test_verify_rejects_missing_claim() -> None:
    # A correctly signed token whose payload omits the workspace claim.
    import json

    from ownevo_kernel.api._signing import b64url_encode, sign

    payload = json.dumps({"u": "alice", "e": int(time.time()) + 300}).encode()
    payload_s = b64url_encode(payload)
    token = f"{payload_s}.{sign(payload_s, _KEY)}"
    with pytest.raises(AssertionInvalid, match="missing claim: w"):
        verify_workspace_assertion(token, _KEY)


def test_mint_rejects_empty_ids() -> None:
    with pytest.raises(ValueError, match="required"):
        _mint(user_id="")


def test_mint_rejects_nonpositive_ttl() -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        _mint(ttl_seconds=0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("TRUE", True), ("false", False), ("", False), ("1", False)],
)
def test_dev_auth_enabled(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("OWNEVO_DEV_AUTH", value)
    assert dev_auth_enabled() is expected


def test_dev_auth_disabled_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OWNEVO_DEV_AUTH", raising=False)
    assert dev_auth_enabled() is False


def test_dev_principal_is_seeded_default_owner() -> None:
    assert dev_principal() == Principal(
        user_id=DEV_USER_ID, workspace_id=DEFAULT_WORKSPACE_ID
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc.def", "abc.def"),
        ("bearer abc.def", "abc.def"),
        ("Bearer    spaced  ", "spaced"),
        ("Basic abc", None),
        ("Bearer ", None),
        ("", None),
    ],
)
def test_bearer_token(header: str, expected: str | None) -> None:
    request = SimpleNamespace(headers={"authorization": header})
    assert bearer_token(request) == expected  # type: ignore[arg-type]


def test_bearer_token_missing_header() -> None:
    request = SimpleNamespace(headers={})
    assert bearer_token(request) is None  # type: ignore[arg-type]


# --- verify_internal_service_token ---


def test_verify_service_token_match() -> None:
    assert verify_internal_service_token("secret", "secret") is True


def test_verify_service_token_wrong() -> None:
    assert verify_internal_service_token("wrong", "secret") is False


def test_verify_service_token_none_token() -> None:
    assert verify_internal_service_token(None, "secret") is False


def test_verify_service_token_none_key() -> None:
    assert verify_internal_service_token("token", None) is False


def test_verify_service_token_both_none() -> None:
    assert verify_internal_service_token(None, None) is False


def test_verify_service_token_empty_token() -> None:
    assert verify_internal_service_token("", "secret") is False


def test_verify_service_token_empty_key() -> None:
    assert verify_internal_service_token("sometoken", "") is False
