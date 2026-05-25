"""Secrets handling — encryption-at-rest for third-party credentials.

Currently just the symmetric field encryption used to store integration
API keys (LangSmith, etc.) in `integration_credentials.ciphertext`.
"""

from .encrypted_field import (
    CredentialsDecryptError,
    CredentialsKeyMissingError,
    decrypt,
    encrypt,
    generate_master_key,
)

__all__ = [
    "CredentialsDecryptError",
    "CredentialsKeyMissingError",
    "decrypt",
    "encrypt",
    "generate_master_key",
]
