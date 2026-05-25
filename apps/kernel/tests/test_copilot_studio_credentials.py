"""Unit tests for the structured Copilot Studio credential store.

No database: a fake connection captures the encrypted blob on `execute`
and returns it on `fetchval`, so the real encrypt/decrypt round-trip is
exercised (a master key is set per test) without Postgres. We assert:
the credential round-trips; the blob is encrypted at rest (no plaintext
secret); and a malformed or incomplete blob raises a config error rather
than returning a half-built credential.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("cryptography", reason="cryptography (api extra) not installed")

from ownevo_kernel.api._copilot_studio_credentials import (  # noqa: E402
    PROVIDER,
    get_copilot_studio_credential,
    set_copilot_studio_credential,
)
from ownevo_kernel.api._integration_credentials import set_credential  # noqa: E402
from ownevo_kernel.middleware.copilot_studio import (  # noqa: E402
    CopilotStudioConfigError,
    CopilotStudioCredentials,
)


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from ownevo_kernel.secrets import generate_master_key

    monkeypatch.setenv("OWNEVO_CREDENTIALS_MASTER_KEY", generate_master_key())


class _FakeConn:
    """Minimal asyncpg-shaped stub for the two credential queries."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def execute(self, query: str, *args: object) -> str:
        if "integration_credentials" in query and "INSERT" in query:
            self._store[str(args[0])] = str(args[1])
        return "INSERT 1"

    async def fetchval(self, query: str, *args: object) -> object:
        return self._store.get(str(args[0]))


_CRED = CopilotStudioCredentials(
    tenant_id="tenant-1",
    client_id="client-1",
    client_secret="super-secret",
    environment_url="https://org.crm.dynamics.com",
)


async def test_round_trip() -> None:
    conn = _FakeConn()
    await set_copilot_studio_credential(conn, _CRED)
    got = await get_copilot_studio_credential(conn)
    assert got == _CRED


async def test_blob_is_encrypted_at_rest() -> None:
    conn = _FakeConn()
    await set_copilot_studio_credential(conn, _CRED)
    stored = conn._store[PROVIDER]
    # The plaintext secret must not appear in the stored ciphertext.
    assert "super-secret" not in stored
    assert "tenant-1" not in stored
    assert stored.startswith("v1:")


async def test_unconfigured_returns_none() -> None:
    conn = _FakeConn()
    assert await get_copilot_studio_credential(conn) is None


async def test_malformed_blob_raises_config_error() -> None:
    conn = _FakeConn()
    await set_credential(conn, PROVIDER, "this is not json")
    with pytest.raises(CopilotStudioConfigError):
        await get_copilot_studio_credential(conn)


async def test_missing_field_raises_config_error() -> None:
    conn = _FakeConn()
    await set_credential(conn, PROVIDER, json.dumps({"tenant_id": "t"}))
    with pytest.raises(CopilotStudioConfigError):
        await get_copilot_studio_credential(conn)


async def test_missing_authority_host_falls_back_to_default() -> None:
    conn = _FakeConn()
    await set_credential(
        conn,
        PROVIDER,
        json.dumps(
            {
                "tenant_id": "t",
                "client_id": "c",
                "client_secret": "s",
                "environment_url": "https://org.crm.dynamics.com",
            }
        ),
    )
    got = await get_copilot_studio_credential(conn)
    assert got is not None
    assert got.authority_host == "https://login.microsoftonline.com"
