"""Audit log writer + canonical-JSON export (W2.4 / D2).

The `audit_entries` table is the spine of the kernel. Every state-machine
transition (proposal-created, gate-run-completed, proposal-approved, ...)
writes a row through `append_audit_entry`. The table is append-only at
the DB level: row-level WORM triggers on UPDATE/DELETE plus a statement-
level trigger on TRUNCATE (locked in 0001_substrate.sql). Production also
revokes UPDATE/DELETE/TRUNCATE from the app role — that grant migration
lands when the writer-side role is wired (out of scope here).

`export_audit_log` returns entries in `seq` order; `to_canonical_json`
serializes them sorted-keys + no-whitespace, the marketing-claim form of
"customer-controlled export". Crypto-grade tamper-evidence (Merkle root,
signed transparency log) is a Phase-2 retrofit per D2.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from ..types import AuditEntry, AuditKind


async def append_audit_entry(
    conn: asyncpg.Connection,
    *,
    kind: AuditKind | str,
    payload: dict[str, Any],
    actor: str,
    related_id: UUID | None = None,
) -> AuditEntry:
    """Insert one row and return the canonical Pydantic AuditEntry.

    `kind` accepts the AuditKind enum or its string value — saves the
    caller from importing the enum just to write a single entry.
    """
    kind_value = kind.value if isinstance(kind, AuditKind) else kind
    row = await conn.fetchrow(
        """
        INSERT INTO audit_entries (kind, payload, related_id, actor)
        VALUES ($1::audit_kind, $2::jsonb, $3, $4)
        RETURNING id, seq, kind::text AS kind, payload, related_id, actor, created_at
        """,
        kind_value,
        json.dumps(payload),
        related_id,
        actor,
    )
    return _row_to_entry(row)


async def export_audit_log(
    conn: asyncpg.Connection,
    *,
    since_seq: int | None = None,
    kind: AuditKind | str | None = None,
) -> list[AuditEntry]:
    """All audit entries (or those with `seq > since_seq`), in `seq` order.

    `kind` filters to a single audit_kind — useful for "all proposals" or
    "all gate runs" queries without scanning the full log.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if since_seq is not None:
        params.append(since_seq)
        clauses.append(f"seq > ${len(params)}")
    if kind is not None:
        params.append(kind.value if isinstance(kind, AuditKind) else kind)
        clauses.append(f"kind = ${len(params)}::audit_kind")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await conn.fetch(
        f"""
        SELECT id, seq, kind::text AS kind, payload, related_id, actor, created_at
        FROM audit_entries
        {where}
        ORDER BY seq ASC
        """,
        *params,
    )
    return [_row_to_entry(r) for r in rows]


def to_canonical_json(entries: list[AuditEntry]) -> bytes:
    """Sorted-keys + no-whitespace + UTF-8 encoded.

    Stable byte-form across runs: a customer can `diff` exports from two
    points in time and see only what was appended. Bytes are the contract;
    don't add pretty-printing.
    """
    payload = [e.model_dump(mode="json") for e in entries]
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _row_to_entry(row: asyncpg.Record) -> AuditEntry:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return AuditEntry(
        id=row["id"],
        seq=row["seq"],
        kind=AuditKind(row["kind"]),
        payload=payload,
        related_id=row["related_id"],
        actor=row["actor"],
        created_at=row["created_at"],
    )
