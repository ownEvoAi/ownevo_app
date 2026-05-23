"""Audit log writer + canonical-JSON export (W2.4 / D2).

The `audit_entries` table is the spine of the kernel. Every state-machine
transition (proposal-created, gate-run-completed, proposal-approved, ...)
writes a row through `append_audit_entry`. The table is append-only at
the DB level: row-level WORM triggers on UPDATE/DELETE plus a statement-
level trigger on TRUNCATE (locked in 0001_substrate.sql). For full WORM
enforcement, `0010_grants_and_constraints.sql` also REVOKEs UPDATE/DELETE
from the application role — run it after substituting the actual DB user.

`export_audit_log` returns entries in `seq` order; `to_canonical_json`
serializes them sorted-keys + no-whitespace, the marketing-claim form of
"customer-controlled export". Crypto-grade tamper-evidence (Merkle root,
signed transparency log) is a Phase-2 retrofit per D2.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from ..types import AuditEntry, AuditKind

# SHA-256 all-zeros: the sentinel parent_hash for the first entry in a
# chain (or the first entry after migrating a pre-hash-epoch DB).
_GENESIS_HASH = "0" * 64

# Sequence name for the `seq bigserial` column on `audit_entries`.
_SEQ_NAME = "audit_entries_seq_seq"


def compute_entry_hash(
    *,
    seq: int,
    kind: str,
    payload: dict[str, Any],
    related_id: UUID | None,
    actor: str,
    created_at: datetime,
    parent_hash: str,
) -> str:
    """SHA-256 over the canonical JSON of an entry's content fields.

    The hash commits to every field that constitutes the entry's identity
    plus `parent_hash` (which chains it to its predecessor). `entry_hash`
    itself is excluded to avoid circularity. `created_at` is serialised
    with `.isoformat()` — Python's datetime.isoformat() is stable for the
    same value, and we supply `created_at` explicitly from Python rather
    than relying on DB `now()`.
    """
    canonical = json.dumps(
        {
            "actor": actor,
            "created_at": created_at.isoformat(),
            "kind": kind,
            "parent_hash": parent_hash,
            "payload": payload,
            "related_id": str(related_id) if related_id else None,
            "seq": seq,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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

    Hash-chain logic (TODO-3): we pre-claim the seq via nextval and
    supply `created_at` from Python so both values are known before
    hashing. This avoids a two-phase INSERT+UPDATE, which is incompatible
    with the WORM trigger on `audit_entries`.
    """
    kind_value = kind.value if isinstance(kind, AuditKind) else kind
    try:
        payload_json = json.dumps(payload)
    except TypeError as exc:
        raise TypeError(
            f"append_audit_entry: payload is not JSON-serializable: {exc}"
        ) from exc

    # Advisory lock serializes concurrent hash-chain writes. Without this,
    # two simultaneous callers could read the same prev_hash and insert two
    # entries with identical parent_hash, silently forking the chain.
    # pg_advisory_xact_lock is released automatically when the transaction ends.
    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext('ownevo.audit_chain'))"
        )

        # Resolve parent_hash from the most-recent hashed entry. Entries
        # written before 0009_audit_hash_chain.sql have NULL entry_hash and
        # are skipped. If no hashed entry exists yet, start the chain from
        # the genesis sentinel.
        prev_hash: str | None = await conn.fetchval(
            "SELECT entry_hash FROM audit_entries "
            "WHERE entry_hash IS NOT NULL "
            "ORDER BY seq DESC LIMIT 1",
        )
        parent_hash = prev_hash if prev_hash is not None else _GENESIS_HASH

        seq: int = await conn.fetchval(f"SELECT nextval('{_SEQ_NAME}')")
        created_at = datetime.now(tz=UTC)

        entry_hash = compute_entry_hash(
            seq=seq,
            kind=kind_value,
            payload=payload,
            related_id=related_id,
            actor=actor,
            created_at=created_at,
            parent_hash=parent_hash,
        )

        row = await conn.fetchrow(
            """
            INSERT INTO audit_entries
                (seq, kind, payload, related_id, actor, created_at, parent_hash, entry_hash)
            VALUES ($1, $2::audit_kind, $3::jsonb, $4, $5, $6, $7, $8)
            RETURNING id, seq, kind::text AS kind, payload, related_id, actor,
                      created_at, parent_hash, entry_hash
            """,
            seq,
            kind_value,
            payload_json,
            related_id,
            actor,
            created_at,
            parent_hash,
            entry_hash,
        )
    return _row_to_entry(row)


_EXPORT_MAX_ROWS: int = 100_000


async def export_audit_log(
    conn: asyncpg.Connection,
    *,
    since_seq: int | None = None,
    kind: AuditKind | str | None = None,
    max_rows: int = _EXPORT_MAX_ROWS,
) -> list[AuditEntry]:
    """All audit entries (or those with `seq > since_seq`), in `seq` order.

    `kind` filters to a single audit_kind — useful for "all proposals" or
    "all gate runs" queries without scanning the full log.

    `max_rows` caps the result set (default 100 000). Callers that need
    the full log beyond the cap should paginate using `since_seq`. The
    export route sets a ``X-Audit-Truncated`` header when the cap fires.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if since_seq is not None:
        params.append(since_seq)
        clauses.append(f"seq > ${len(params)}")
    if kind is not None:
        params.append(kind.value if isinstance(kind, AuditKind) else kind)
        clauses.append(f"kind = ${len(params)}::audit_kind")

    params.append(max_rows)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await conn.fetch(
        f"""
        SELECT id, seq, kind::text AS kind, payload, related_id, actor, created_at,
               parent_hash, entry_hash
        FROM audit_entries
        {where}
        ORDER BY seq ASC
        LIMIT ${len(params)}
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
    try:
        kind_enum = AuditKind(row["kind"])
    except ValueError:
        # DB enum has a value not yet in this Python deploy — preserve raw string
        # so export_audit_log doesn't crash on unknown kinds during rolling deploys.
        kind_enum = row["kind"]  # type: ignore[assignment]
    return AuditEntry(
        id=row["id"],
        seq=row["seq"],
        kind=kind_enum,
        payload=payload,
        related_id=row["related_id"],
        actor=row["actor"],
        created_at=row["created_at"],
        parent_hash=row["parent_hash"],
        entry_hash=row["entry_hash"],
    )
