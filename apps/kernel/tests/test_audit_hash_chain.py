"""Tests for the TODO-3 audit hash-chain implementation.

Coverage:
  - entry_hash / parent_hash are computed and stored on write
  - genesis sentinel is used for the first entry
  - parent_hash of entry N+1 equals entry_hash of entry N
  - compute_entry_hash is deterministic
  - /api/audit/verify reports hash_chain_valid=True for a correct chain
  - /api/audit/verify reports hash_chain_valid=True when the log is empty
  - /api/audit/verify returns first_broken_seq when chain is corrupted
    (simulated by inserting a row with a wrong parent_hash via the
    sequence-advance trick — no UPDATE needed, WORM stays intact)
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest
from ownevo_kernel.audit.writer import (
    _GENESIS_HASH,
    _SEQ_NAME,
    append_audit_entry,
    compute_entry_hash,
)
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.types import AuditKind

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Unit: compute_entry_hash determinism
# ---------------------------------------------------------------------------


def test_compute_entry_hash_is_deterministic():
    ts = datetime(2026, 5, 14, 10, 0, 0, tzinfo=UTC)
    kwargs = dict(
        seq=1,
        kind="proposal-approved",
        payload={"proposal_id": "p1"},
        related_id=None,
        actor="human:test",
        created_at=ts,
        parent_hash=_GENESIS_HASH,
    )
    assert compute_entry_hash(**kwargs) == compute_entry_hash(**kwargs)


def test_compute_entry_hash_differs_on_payload_change():
    ts = datetime(2026, 5, 14, 10, 0, 0, tzinfo=UTC)
    base = dict(seq=1, kind="proposal-approved", related_id=None,
                actor="human:test", created_at=ts, parent_hash=_GENESIS_HASH)
    h1 = compute_entry_hash(**base, payload={"x": 1})
    h2 = compute_entry_hash(**base, payload={"x": 2})
    assert h1 != h2


def test_compute_entry_hash_is_64_hex_chars():
    ts = datetime(2026, 5, 14, 10, 0, 0, tzinfo=UTC)
    h = compute_entry_hash(
        seq=1, kind="proposal-approved", payload={},
        related_id=None, actor="test", created_at=ts,
        parent_hash=_GENESIS_HASH,
    )
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Integration: hash fields on write
# ---------------------------------------------------------------------------


async def test_first_entry_uses_genesis_parent_hash(db: asyncpg.Connection):
    entry = await append_audit_entry(
        db, kind="proposal-approved", payload={"n": 0}, actor="human:test",
    )
    assert entry.parent_hash == _GENESIS_HASH
    assert entry.entry_hash is not None
    assert len(entry.entry_hash) == 64


async def test_second_entry_parent_hash_links_to_first(db: asyncpg.Connection):
    e1 = await append_audit_entry(
        db, kind="proposal-approved", payload={"n": 1}, actor="human:test",
    )
    e2 = await append_audit_entry(
        db, kind="gate-run-completed", payload={"n": 2}, actor="agent:loop",
    )
    assert e2.parent_hash == e1.entry_hash


async def test_entry_hash_matches_recomputed_value(db: asyncpg.Connection):
    entry = await append_audit_entry(
        db,
        kind=AuditKind.PROPOSAL_APPROVED,
        payload={"proposal_id": "p-abc"},
        actor="human:reviewer",
        related_id=None,
    )
    kind_str = entry.kind.value if isinstance(entry.kind, AuditKind) else entry.kind
    expected = compute_entry_hash(
        seq=entry.seq,
        kind=kind_str,
        payload=entry.payload,
        related_id=entry.related_id,
        actor=entry.actor,
        created_at=entry.created_at,
        parent_hash=entry.parent_hash or _GENESIS_HASH,
    )
    assert entry.entry_hash == expected


# ---------------------------------------------------------------------------
# Integration: /api/audit/verify with hash chain
# ---------------------------------------------------------------------------


async def test_verify_empty_log_hash_chain_valid(api_client: httpx.AsyncClient):
    res = await api_client.post("/api/audit/verify")
    assert res.status_code == 200
    body = res.json()
    assert body["hash_chain_valid"] is True
    assert body["hash_chain_entries"] == 0
    assert body["first_broken_seq"] is None


async def test_verify_valid_chain_after_two_writes(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await append_audit_entry(db, kind="proposal-approved", payload={"i": 0}, actor="test")
    await append_audit_entry(db, kind="gate-run-completed", payload={"i": 1}, actor="test")

    res = await api_client.post("/api/audit/verify")
    body = res.json()
    assert body["valid"] is True
    assert body["hash_chain_valid"] is True
    assert body["hash_chain_entries"] == 2
    assert body["first_broken_seq"] is None


async def test_verify_chain_broken_by_wrong_parent_hash(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """Simulate a broken chain by inserting an entry with a wrong
    parent_hash (not via UPDATE, which WORM blocks, but by bypassing
    append_audit_entry and using a raw INSERT with a fabricated hash).

    This exercises the verify endpoint's recomputation check: the
    entry_hash in the DB won't match what compute_entry_hash returns
    for the stored parent_hash, so the chain is detected as broken.
    """
    # Write a legitimate first entry via the standard writer.
    await append_audit_entry(
        db, kind="proposal-approved", payload={"legit": True}, actor="test",
    )

    # Insert a second entry with an intentionally wrong parent_hash.
    bad_parent = "a" * 64
    seq: int = await db.fetchval(f"SELECT nextval('{_SEQ_NAME}')")
    created_at = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
    kind_str = "gate-run-completed"
    payload = {"tampered": True}
    # Compute entry_hash using the wrong parent_hash so the stored hash
    # is self-consistent for the wrong parent — the verify endpoint
    # recomputes from the canonical source and sees parent_hash mismatch.
    bad_entry_hash = compute_entry_hash(
        seq=seq, kind=kind_str, payload=payload,
        related_id=None, actor="test", created_at=created_at,
        parent_hash=bad_parent,
    )
    await db.execute(
        """
        INSERT INTO audit_entries
            (seq, kind, payload, related_id, actor, created_at, parent_hash, entry_hash)
        VALUES ($1, $2::audit_kind, $3::jsonb, NULL, 'test', $4, $5, $6)
        """,
        seq, kind_str, json.dumps(payload), created_at, bad_parent, bad_entry_hash,
    )

    res = await api_client.post("/api/audit/verify")
    body = res.json()
    assert body["hash_chain_valid"] is False
    assert body["first_broken_seq"] == seq
