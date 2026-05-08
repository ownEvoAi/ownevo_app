"""Integration tests for the W7 slice 4 `/api/audit` surface.

The `api_client` fixture is shared via `conftest.py`.
"""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import httpx
import pytest
from ownevo_kernel.audit.writer import append_audit_entry
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


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
    # All filtered entries fit under the limit, so `truncated` must be False
    # even though `total` reflects the unfiltered table count.
    assert body["truncated"] is False
    assert body["total"] == 3  # unfiltered count of all seeded entries


async def test_list_audit_invalid_kind_returns_422(api_client: httpx.AsyncClient):
    """Unknown audit_kind enum values must return 422, not 500.

    Regression test: asyncpg raises InvalidTextRepresentationError (not
    ValueError) for unknown enum casts; the route must catch both.
    """
    res = await api_client.get("/api/audit?kind=not-a-real-kind")
    assert res.status_code == 422


async def test_list_audit_since_seq_filters(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    related = uuid4()
    seqs = []
    for i in range(5):
        entry = await _seed_entry(db, related_id=related, payload={"i": i})
        seqs.append(entry.seq)

    cutoff = seqs[2]  # third seeded entry's seq
    res = await api_client.get(f"/api/audit?since_seq={cutoff}")
    body = res.json()
    returned_seqs = sorted(item["seq"] for item in body["items"])
    assert returned_seqs == sorted(seqs[3:])


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


async def test_list_audit_rejects_zero_limit(api_client: httpx.AsyncClient):
    """Lower bound (limit=0) must also be rejected — `ge=1` constraint."""
    res = await api_client.get("/api/audit?limit=0")
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


async def test_verify_detects_seq_gap(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """The D2 structural integrity claim — gaps must be reported.

    BIGSERIAL prevents duplicate seqs, but skipped values are possible
    (rolled-back transactions, sequence cycle). Force a gap by advancing
    the underlying sequence between inserts and assert the verify
    endpoint surfaces the missing seqs.
    """
    related = uuid4()
    await _seed_entry(db, related_id=related, payload={"i": 0})
    # Skip the next 3 seq values to manufacture a gap.
    seq_name = await db.fetchval(
        "SELECT pg_get_serial_sequence('audit_entries', 'seq')"
    )
    current = await db.fetchval(f"SELECT last_value FROM {seq_name}")
    await db.execute(f"SELECT setval('{seq_name}', $1)", current + 3)
    await _seed_entry(db, related_id=related, payload={"i": 1})

    res = await api_client.post("/api/audit/verify")
    body = res.json()
    assert body["valid"] is False
    assert body["total_entries"] == 2
    assert len(body["missing_seqs"]) == 3
    assert body["duplicate_seqs"] == []
