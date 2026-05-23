"""Integration tests for the demo gate, quota, budget, and routes.

Each test sets ``DEMO_MODE=true`` for its duration via monkeypatch and
relies on the per-test ``db`` + ``api_client`` fixtures. The fixtures
spin up a fresh database with migrations applied, so the demo tables
land via migration 0016 automatically.
"""

from __future__ import annotations

import asyncpg
import httpx
import pytest
from ownevo_kernel.api._demo_budget import (
    get_budget_status,
    set_budget_status,
)
from ownevo_kernel.api._demo_identity import (
    DemoIdentity,
    mint_invite_token,
)
from ownevo_kernel.api._demo_quota import (
    get_quota_status,
    limit_for_tier,
    record_usage,
)

SIGNING_KEY = "test-signing-key-deadbeef"


@pytest.fixture(autouse=True)
def demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("OWNEVO_DEMO_SIGNING_KEY", SIGNING_KEY)
    # Pin caps to small values so tests can exhaust them deterministically.
    monkeypatch.setenv("OWNEVO_DEMO_ANON_TOKENS_PER_DAY", "1000")
    monkeypatch.setenv("OWNEVO_DEMO_ELEVATED_TOKENS_PER_DAY", "10000")


def _anon_identity(key: str = "c:test-anon") -> DemoIdentity:
    return DemoIdentity(
        identity_key=key,
        tier="anonymous",
        label=None,
        invite_jti=None,
        invite_exp=None,
    )


async def test_quota_status_starts_at_zero(db: asyncpg.Connection) -> None:
    quota = await get_quota_status(db, _anon_identity())
    assert quota.used == 0
    assert quota.limit == 1000
    assert quota.exhausted is False


async def test_record_usage_increments_per_day(db: asyncpg.Connection) -> None:
    identity = _anon_identity()
    await record_usage(db, identity, input_tokens=400, output_tokens=200)
    await record_usage(db, identity, input_tokens=100, output_tokens=50)
    quota = await get_quota_status(db, identity)
    assert quota.used == 750
    assert quota.exhausted is False


async def test_quota_exhausts_at_limit(db: asyncpg.Connection) -> None:
    identity = _anon_identity()
    await record_usage(db, identity, input_tokens=600, output_tokens=500)
    quota = await get_quota_status(db, identity)
    assert quota.used == 1100
    assert quota.exhausted is True


async def test_unlimited_tier_skips_cap(db: asyncpg.Connection) -> None:
    identity = DemoIdentity(
        identity_key="inv:test-jti",
        tier="unlimited",
        label="reviewer-a",
        invite_jti="test-jti",
        invite_exp=None,
    )
    await record_usage(db, identity, input_tokens=10_000, output_tokens=10_000)
    quota = await get_quota_status(db, identity)
    assert quota.limit is None
    assert quota.exhausted is False


async def test_elevated_limit_distinct_from_anon() -> None:
    assert limit_for_tier("anonymous") == 1000
    assert limit_for_tier("elevated") == 10_000
    assert limit_for_tier("unlimited") is None


async def test_budget_state_roundtrip(db: asyncpg.Connection) -> None:
    assert (await get_budget_status(db)).exhausted is False
    await set_budget_status(db, exhausted=True, note="manual test")
    assert (await get_budget_status(db)).exhausted is True
    await set_budget_status(db, exhausted=False, note=None)
    assert (await get_budget_status(db)).exhausted is False


async def test_demo_status_anon_first_visit(api_client: httpx.AsyncClient) -> None:
    r = await api_client.get("/api/demo/status")
    assert r.status_code == 200
    body = r.json()
    assert body["demo_mode"] is True
    assert body["tier"] == "anonymous"
    assert body["used_tokens"] == 0
    assert body["limit_tokens"] == 1000
    assert body["exhausted"] is False
    # First visit must set the anonymous cookie so quota accounting is stable.
    assert "ownevo_demo_id" in r.cookies


async def test_demo_status_reflects_recorded_usage(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    # Prime an anon visitor cookie via the status endpoint.
    r1 = await api_client.get("/api/demo/status")
    cookie_val = r1.cookies["ownevo_demo_id"]
    identity_key = f"c:{cookie_val}"
    await record_usage(
        db, _anon_identity(identity_key), input_tokens=900, output_tokens=200
    )
    r2 = await api_client.get(
        "/api/demo/status", cookies={"ownevo_demo_id": cookie_val}
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["used_tokens"] == 1100
    assert body["exhausted"] is True


async def test_redeem_invite_sets_cookie(api_client: httpx.AsyncClient) -> None:
    token = mint_invite_token(
        label="redeem-test", tier="unlimited", ttl_days=7, signing_key=SIGNING_KEY
    )
    r = await api_client.post("/api/demo/redeem-invite", json={"token": token})
    assert r.status_code == 204
    assert r.cookies.get("ownevo_demo_invite") == token


async def test_redeem_invite_rejects_bad_token(api_client: httpx.AsyncClient) -> None:
    r = await api_client.post(
        "/api/demo/redeem-invite", json={"token": "not-a-real-token"}
    )
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["code"] == "invite_invalid"


async def test_status_reports_elevated_after_redeem(
    api_client: httpx.AsyncClient,
) -> None:
    token = mint_invite_token(
        label="elevated-tester",
        tier="elevated",
        ttl_days=7,
        signing_key=SIGNING_KEY,
    )
    await api_client.post("/api/demo/redeem-invite", json={"token": token})
    r = await api_client.get(
        "/api/demo/status", cookies={"ownevo_demo_invite": token}
    )
    body = r.json()
    assert body["tier"] == "elevated"
    assert body["limit_tokens"] == 10_000
    assert body["label"] == "elevated-tester"


async def test_revoked_invite_falls_through_to_anon(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    token = mint_invite_token(
        label="will-revoke",
        tier="unlimited",
        ttl_days=7,
        signing_key=SIGNING_KEY,
    )
    # Extract jti from the token claims (mint includes it).
    from ownevo_kernel.api._demo_identity import verify_invite_token

    claims = verify_invite_token(token, SIGNING_KEY)
    jti = str(claims["jti"])
    await db.execute(
        "INSERT INTO demo_invite_revocations(jti, reason) VALUES ($1, $2)",
        jti,
        "test revocation",
    )
    r = await api_client.get(
        "/api/demo/status", cookies={"ownevo_demo_invite": token}
    )
    body = r.json()
    assert body["tier"] == "anonymous"


async def test_redeem_invite_404_when_demo_mode_off(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    r = await api_client.post(
        "/api/demo/redeem-invite", json={"token": "anything"}
    )
    assert r.status_code == 404
