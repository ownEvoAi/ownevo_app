"""Audit log writer + export — DB-backed integration tests (W2.4 / D2).

The WORM contract itself (UPDATE/DELETE/TRUNCATE blocked) is covered by
test_db.py. These tests pin the writer's behavior: append → typed entry,
export → canonical-JSON bytes that round-trip, ordering by seq.
"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest
from ownevo_kernel.audit import append_audit_entry, export_audit_log, to_canonical_json
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.types import AuditEntry, AuditKind

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Append + read-back
# ---------------------------------------------------------------------------


async def test_append_returns_typed_entry(db: asyncpg.Connection):
    entry = await append_audit_entry(
        db,
        kind=AuditKind.PROPOSAL_CREATED,
        payload={"proposal_id": "p1", "skill_id": "m5-feature-engineer"},
        actor="agent:claude-opus-4-7",
    )
    assert isinstance(entry, AuditEntry)
    assert entry.kind == AuditKind.PROPOSAL_CREATED
    assert entry.actor == "agent:claude-opus-4-7"
    assert entry.payload == {"proposal_id": "p1", "skill_id": "m5-feature-engineer"}
    assert entry.seq >= 1


async def test_append_accepts_string_kind(db: asyncpg.Connection):
    """Saves callers from importing the enum for one-off entries."""
    entry = await append_audit_entry(
        db,
        kind="schema-migration",
        payload={"version": "0001"},
        actor="ops:bootstrap",
    )
    assert entry.kind == AuditKind.SCHEMA_MIGRATION


async def test_append_records_related_id(db: asyncpg.Connection):
    rid = uuid.uuid4()
    entry = await append_audit_entry(
        db,
        kind=AuditKind.GATE_RUN_STARTED,
        payload={},
        actor="kernel",
        related_id=rid,
    )
    assert entry.related_id == rid


async def test_append_invalid_kind_rejected(db: asyncpg.Connection):
    """DB enum rejects unknown kind strings — mirrors test_unknown_provenance_rejected_by_db."""
    with pytest.raises(asyncpg.PostgresError):
        await append_audit_entry(db, kind="not-a-real-kind", payload={}, actor="ops")


# ---------------------------------------------------------------------------
# Sequence ordering
# ---------------------------------------------------------------------------


async def test_seq_is_monotonic(db: asyncpg.Connection):
    """`seq` is a bigserial — strictly increasing across appends."""
    seqs = []
    for i in range(5):
        e = await append_audit_entry(
            db,
            kind=AuditKind.SCHEMA_MIGRATION,
            payload={"i": i},
            actor="ops",
        )
        seqs.append(e.seq)
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 5  # strictly increasing, no duplicates


async def test_export_returns_all_in_seq_order(db: asyncpg.Connection):
    for i in range(3):
        await append_audit_entry(
            db, kind="schema-migration", payload={"i": i}, actor="ops",
        )
    entries = await export_audit_log(db)
    assert len(entries) == 3
    assert [e.payload["i"] for e in entries] == [0, 1, 2]


async def test_export_filters_by_since_seq(db: asyncpg.Connection):
    """Incremental export — fetch only new entries since the last export."""
    e1 = await append_audit_entry(db, kind="schema-migration", payload={"n": 1}, actor="ops")
    e2 = await append_audit_entry(db, kind="schema-migration", payload={"n": 2}, actor="ops")
    e3 = await append_audit_entry(db, kind="schema-migration", payload={"n": 3}, actor="ops")

    new_entries = await export_audit_log(db, since_seq=e1.seq)
    assert [e.seq for e in new_entries] == [e2.seq, e3.seq]


async def test_export_filters_by_kind(db: asyncpg.Connection):
    await append_audit_entry(db, kind=AuditKind.PROPOSAL_CREATED, payload={"n": 1}, actor="a")
    await append_audit_entry(db, kind=AuditKind.GATE_RUN_STARTED, payload={"n": 2}, actor="a")
    await append_audit_entry(db, kind=AuditKind.PROPOSAL_CREATED, payload={"n": 3}, actor="a")

    proposals = await export_audit_log(db, kind=AuditKind.PROPOSAL_CREATED)
    assert {e.payload["n"] for e in proposals} == {1, 3}
    assert all(e.kind == AuditKind.PROPOSAL_CREATED for e in proposals)


async def test_export_combined_since_seq_and_kind(db: asyncpg.Connection):
    """since_seq AND kind filters AND together — exercises both clauses in one query."""
    e1 = await append_audit_entry(db, kind=AuditKind.PROPOSAL_CREATED, payload={"n": 1}, actor="a")
    await append_audit_entry(db, kind=AuditKind.GATE_RUN_STARTED, payload={"n": 2}, actor="a")
    e3 = await append_audit_entry(db, kind=AuditKind.PROPOSAL_CREATED, payload={"n": 3}, actor="a")
    await append_audit_entry(db, kind=AuditKind.PROPOSAL_CREATED, payload={"n": 4}, actor="a")

    # since_seq=e1 + kind=PROPOSAL_CREATED → e3 and e4, not e1 or the gate entry
    results = await export_audit_log(db, since_seq=e1.seq, kind=AuditKind.PROPOSAL_CREATED)
    assert {e.payload["n"] for e in results} == {3, 4}
    assert all(e.kind == AuditKind.PROPOSAL_CREATED for e in results)
    assert e3.seq in {e.seq for e in results}


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------


async def test_canonical_json_has_sorted_keys_and_no_whitespace(db: asyncpg.Connection):
    """Bytes are the contract — sorted keys + no whitespace so a customer
    can `diff` two exports byte-for-byte."""
    await append_audit_entry(
        db,
        kind=AuditKind.PROPOSAL_CREATED,
        payload={"z_field": 1, "a_field": 2, "nested": {"y": 1, "x": 2}},
        actor="agent:test",
    )
    entries = await export_audit_log(db)
    blob = to_canonical_json(entries)

    assert b", " not in blob and b": " not in blob  # no whitespace in JSON separators
    decoded = json.loads(blob)
    payload = decoded[0]["payload"]
    keys = list(payload.keys())
    assert keys == sorted(keys), f"top-level keys not sorted: {keys}"
    nested_keys = list(payload["nested"].keys())
    assert nested_keys == sorted(nested_keys)


async def test_canonical_json_round_trips(db: asyncpg.Connection):
    """Encode → decode → re-encode is a fixed-point. Necessary for any
    "import a previously exported log" path."""
    for i in range(3):
        await append_audit_entry(
            db, kind="schema-migration", payload={"i": i, "tag": "x"}, actor="ops",
        )
    entries = await export_audit_log(db)
    blob1 = to_canonical_json(entries)
    decoded = json.loads(blob1)
    blob2 = json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert blob1 == blob2


async def test_canonical_json_empty_log(db: asyncpg.Connection):
    blob = to_canonical_json([])
    assert blob == b"[]"


# ---------------------------------------------------------------------------
# WORM smoke (extends test_db.py — verifies the writer doesn't bypass)
# ---------------------------------------------------------------------------


async def test_writer_path_respects_worm(db: asyncpg.Connection):
    """Belt-and-suspenders: the high-level writer can't UPDATE rows it
    just appended."""
    e = await append_audit_entry(db, kind="schema-migration", payload={}, actor="ops")
    with pytest.raises(asyncpg.PostgresError, match="WORM"):
        await db.execute("UPDATE audit_entries SET actor='hacker' WHERE id=$1", e.id)
