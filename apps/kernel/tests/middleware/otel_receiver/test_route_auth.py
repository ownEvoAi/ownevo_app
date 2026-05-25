"""HTTP-layer auth tests for `POST /api/otel/v1/traces`.

Exercises the auth gate at the route boundary: the per-directory
conftest sets `OWNEVO_OTLP_AUTH_OPTIONAL=true` for the rest of the
receiver test suite, but these tests delete that env var to flip the
default back to "required" and assert the 401 path holds.
"""

from __future__ import annotations

import os

import httpx
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.middleware.otel_receiver.auth import (
    AUTH_OPTIONAL_ENV,
    mint_token,
)

from ._fixture_cases import CASES

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping route-auth tests",
)


def _well_formed_payload() -> dict:
    return next(c for c in CASES if c.name == "01_chat_basic_text").payload


async def test_required_mode_rejects_missing_authorization(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(AUTH_OPTIONAL_ENV, raising=False)
    resp = await api_client.post("/api/otel/v1/traces", json=_well_formed_payload())
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


async def test_required_mode_rejects_malformed_authorization(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(AUTH_OPTIONAL_ENV, raising=False)
    resp = await api_client.post(
        "/api/otel/v1/traces",
        json=_well_formed_payload(),
        headers={"Authorization": "Basic foo"},
    )
    assert resp.status_code == 401


async def test_required_mode_rejects_unknown_token(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(AUTH_OPTIONAL_ENV, raising=False)
    plaintext, _ = mint_token()  # generated but not inserted → unknown
    resp = await api_client.post(
        "/api/otel/v1/traces",
        json=_well_formed_payload(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 401


async def test_required_mode_accepts_valid_token(
    api_client: httpx.AsyncClient,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(AUTH_OPTIONAL_ENV, raising=False)
    plaintext, token_hash = mint_token()
    await db.execute(
        "INSERT INTO receiver_tokens (token_hash, label) VALUES ($1, 'route-test')",
        token_hash,
    )
    resp = await api_client.post(
        "/api/otel/v1/traces",
        json=_well_formed_payload(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200, resp.text


async def test_required_mode_rejects_revoked_token(
    api_client: httpx.AsyncClient,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(AUTH_OPTIONAL_ENV, raising=False)
    plaintext, token_hash = mint_token()
    await db.execute(
        """
        INSERT INTO receiver_tokens (token_hash, label, revoked_at)
        VALUES ($1, 'route-test', NOW())
        """,
        token_hash,
    )
    resp = await api_client.post(
        "/api/otel/v1/traces",
        json=_well_formed_payload(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 401


async def test_optional_mode_accepts_no_header(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTH_OPTIONAL_ENV, "true")
    resp = await api_client.post("/api/otel/v1/traces", json=_well_formed_payload())
    assert resp.status_code == 200, resp.text
