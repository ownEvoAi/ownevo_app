"""Tests for credential field encryption (secrets/encrypted_field.py).

Pure-Python, no DB. A fresh master key is generated per test and set in
the environment via monkeypatch.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.secrets import (
    CredentialsDecryptError,
    CredentialsKeyMissingError,
    decrypt,
    encrypt,
    generate_master_key,
)
from ownevo_kernel.secrets.encrypted_field import MASTER_KEY_ENV, _SCHEME_PREFIX


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = generate_master_key()
    monkeypatch.setenv(MASTER_KEY_ENV, key)
    return key


def test_round_trip(master_key: str) -> None:
    secret = "lsv2_pt_deadbeef_cafe"
    sealed = encrypt(secret)
    assert sealed.startswith(_SCHEME_PREFIX)
    assert secret not in sealed  # plaintext must not appear in the token
    assert decrypt(sealed) == secret


def test_each_encrypt_is_nondeterministic(master_key: str) -> None:
    # Fernet embeds a random IV, so two seals of the same plaintext
    # differ — but both decrypt back to the same value.
    a = encrypt("same")
    b = encrypt("same")
    assert a != b
    assert decrypt(a) == decrypt(b) == "same"


def test_unicode_round_trip(master_key: str) -> None:
    secret = "clé-secrète-日本語-🔑"
    assert decrypt(encrypt(secret)) == secret


def test_tamper_detected(master_key: str) -> None:
    sealed = encrypt("secret")
    # Flip a character in the token body.
    body = sealed[len(_SCHEME_PREFIX):]
    tampered = _SCHEME_PREFIX + ("A" if body[0] != "A" else "B") + body[1:]
    with pytest.raises(CredentialsDecryptError):
        decrypt(tampered)


def test_wrong_key_fails(master_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sealed = encrypt("secret")
    # Rotate to a different key and try to decrypt the old token.
    monkeypatch.setenv(MASTER_KEY_ENV, generate_master_key())
    with pytest.raises(CredentialsDecryptError):
        decrypt(sealed)


def test_missing_prefix_rejected(master_key: str) -> None:
    with pytest.raises(CredentialsDecryptError):
        decrypt("no-prefix-token")


def test_missing_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    with pytest.raises(CredentialsKeyMissingError):
        encrypt("secret")
    with pytest.raises(CredentialsKeyMissingError):
        decrypt(f"{_SCHEME_PREFIX}whatever")


def test_malformed_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-valid-fernet-key")
    with pytest.raises(CredentialsKeyMissingError):
        encrypt("secret")
