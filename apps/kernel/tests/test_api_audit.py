"""Integration tests for the W7 slice 4 `/api/audit` surface."""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import httpx
import pytest
from httpx import ASGITransport
from ownevo_kernel.api.app import create_app
from ownevo_kernel.audit.writer import append_audit_entry
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


@pytest.fixture
async def api_client(db: asyncpg.Connection):
    dbname = await db.fetchval("SELECT current_database()")
    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        app = create_app(pool=pool, cors_origins=[])
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=transport, base_url="http://api.test",
        ) as client:
            yield client
    finally:
        await pool.close()


async def _seed_entry(
    conn: asyncpg.Connection,
    *,
    kind: str = "proposal-approved",
    actor: str = "human:test",
    related_id=None,
    payload: dict | None = None,
):
    return await append_audit_entry(
        conn,
        kind=kind,
        actor=actor,
        related_id=related_id,
        payload=payload or {"note": "seed"},
    )


# ---------------------------------------------------------------------------
# GET /api/audit
# ---------------------------------------------------------------------------


async def test_list_audit_empty(api_client: httpx.AsyncClient):
    res = await api_client.get("/api/audit")
    assert res.status_code == 200
    body = res.json()
    assert body == {"items": [], "total": 0, "truncated": False}


async def test_list_audit_returns_newest_first(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    related = uuid4()
    await _seed_entry(db, kind="skill-version-created", related_id=related)
    await _seed_entry(db, kind="proposal-created", related_id=related)
    await _seed_entry(db, kind="proposal-approved", related_id=related)

    res = await api_client.get("/api/audit")
    body = res.json()
    assert body["total"] == 3
    assert body["truncated"] is False

    seqs = [item["seq"] for item in body["items"]]
    # Newest first — seqs descending.
    assert seqs == sorted(seqs, reverse=True)
    assert body["items"][0]["kind"] == "proposal-approved"


async def test_list_audit_filter_by_kind(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    related = uuid4()
    await _seed_entry(db, kind="skill-version-created", related_id=related)
    await _seed_entry(db, kind="proposal-approved", related_id=related)
    await _seed_entry(db, kind="proposal-rejected", related_id=related)

    res = await api_client.get("/api/audit?kind=proposal-approved")
    body = res.json()
    kinds = {item["kind"] for item in body["items"]}
    assert kinds == {"proposal-approved"}


async def test_list_audit_limit_caps_items(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    related = uuid4()
    for _ in range(5):
        await _seed_entry(db, related_id=related)

    res = await api_client.get("/api/audit?limit=2")
    body = res.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["truncated"] is True


async def test_list_audit_rejects_oversize_limit(api_client: httpx.AsyncClient):
    res = await api_client.get("/api/audit?limit=10000")
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/audit/verify
# ---------------------------------------------------------------------------


async def test_verify_empty_chain_is_valid(api_client: httpx.AsyncClient):
    res = await api_client.post("/api/audit/verify")
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["total_entries"] == 0
    assert body["min_seq"] is None
    assert body["max_seq"] is None
    assert body["missing_seqs"] == []
    assert body["duplicate_seqs"] == []
    assert body["canonical_export_bytes"] == len(b"[]")


async def test_verify_contiguous_chain_is_valid(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    related = uuid4()
    for i in range(3):
        await _seed_entry(db, kind="proposal-created", related_id=related,
                          payload={"i": i})

    res = await api_client.post("/api/audit/verify")
    body = res.json()
    assert body["valid"] is True
    assert body["total_entries"] == 3
    assert body["min_seq"] is not None
    assert body["max_seq"] is not None
    assert body["max_seq"] - body["min_seq"] == 2
    assert body["canonical_export_bytes"] > 0
