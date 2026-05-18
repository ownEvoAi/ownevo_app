"""`/api/audit` — workspace-level audit trail surface.

D2-aligned: the audit log is append-only WORM in the DB and the API is
read-only. The "verify chain" endpoint runs a structural integrity
check (seq contiguity + canonical-JSON byte count); the SHA-256 hash chain (TODO-3, now landed) extends this with
parent_hash / entry_hash verification.

Pagination: the list endpoint enforces a hard `limit <= 500`. With
real customer log volume the endpoint switches to keyset pagination
via `since_seq` (TODO-18) — `since_seq` is already wired through the
underlying `export_audit_log` call.
"""

from __future__ import annotations

import hmac
from collections import Counter
from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, HTTPException, Query, status

from ...audit.writer import _GENESIS_HASH, compute_entry_hash, export_audit_log, to_canonical_json
from ...types import AuditKind
from ..deps import ConnDep, DemoModeCheck
from ..models import AuditEntryRow, AuditList, AuditVerifyResponse

router = APIRouter(prefix="/api/audit", tags=["audit"])

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 500
_MAX_REPORTED_GAPS = 100


@router.get("", response_model=AuditList)
async def list_audit(
    conn: ConnDep,
    since_seq: int | None = Query(
        default=None,
        ge=0,
        description="Return only entries with seq > since_seq (keyset pagination).",
    ),
    kind: str | None = Query(
        default=None,
        max_length=64,
        description=(
            "Filter to a single audit_kind (proposal-approved, gate-run-completed, "
            "etc.). See SCHEMA.md § audit_kind for the enum."
        ),
    ),
    workflow_id: str | None = Query(
        default=None,
        max_length=64,
        description=(
            "Filter to entries whose related_id ties back to this workflow — "
            "via proposals.id (whose iteration belongs to the workflow), "
            "iterations.id, or failure_clusters.id. Also matches rows whose "
            "payload carries `workflow_id` directly (design-agent rows write "
            "`workflow_id` into payload but anchor to no related_id row)."
        ),
    ),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> AuditList:
    """Chronological audit entries, newest seq first.

    The API returns up to `limit` rows ordered seq DESC so the UI shows
    most-recent activity at the top. `total` is the unfiltered DB count
    so the UI can render "showing 200 of 412 entries" with a "load
    earlier" cue.
    """
    # TODO: export_audit_log fetches all rows before Python-side filtering.
    # At large log volumes this will OOM / timeout. Fix: push workflow_id
    # and payload->>'workflow_id' predicates into SQL with an expression
    # index on (payload->>'workflow_id'). Tracked for TODO-18 keyset
    # pagination work.
    try:
        entries = await export_audit_log(conn, since_seq=since_seq, kind=kind)
    except (ValueError, asyncpg.exceptions.InvalidTextRepresentationError) as exc:
        # asyncpg raises InvalidTextRepresentationError (22P02) for unknown
        # enum values — not ValueError. Catch both for defence in depth.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid audit_kind value: {kind!r}",
        ) from exc

    if workflow_id is not None:
        # Resolve the set of related_ids that anchor this workflow's
        # audit footprint. Audit entries are workspace-level today (D4
        # single-tenant) so this is the closest we get to "per-workflow
        # audit" without a workflow_id column.
        related_rows = await conn.fetch(
            """
            SELECT id FROM iterations WHERE workflow_id = $1
            UNION ALL
            SELECT p.id FROM proposals p
              JOIN iterations i ON i.id = p.iteration_id
              WHERE i.workflow_id = $1
            UNION ALL
            SELECT id FROM failure_clusters WHERE workflow_id = $1
            """,
            workflow_id,
        )
        wf_related_ids = {r["id"] for r in related_rows}
        entries = [
            e for e in entries
            if (e.related_id is not None and e.related_id in wf_related_ids)
            # design-agent-negotiation / design-agent-ambiguity rows write
            # the workflow_id into payload but have no related_id anchor —
            # match them by payload field so the per-workflow Audit tab
            # surfaces the conversation rows alongside iteration/proposal
            # entries. Future row kinds that follow the same convention
            # ride along for free.
            or (
                isinstance(e.payload, dict)
                and e.payload.get("workflow_id") == workflow_id
            )
        ]

    # `export_audit_log` returns ASC. Reverse + cap for the UI.
    sliced = list(reversed(entries))[:limit]

    items = [
        AuditEntryRow(
            id=e.id,
            seq=e.seq,
            kind=e.kind,
            actor=e.actor,
            related_id=e.related_id,
            payload=e.payload,
            created_at=e.created_at,
        )
        for e in sliced
    ]

    if workflow_id is not None:
        total = len(entries)
    else:
        total = await conn.fetchval("SELECT COUNT(*)::int FROM audit_entries") or 0
    # `truncated` reflects whether the [:limit] cap dropped rows from the
    # filtered result set — not whether there are more unfiltered entries.
    truncated = len(entries) > len(sliced)
    return AuditList(items=items, total=total, truncated=truncated)


@router.post("/verify", response_model=AuditVerifyResponse)
async def verify_chain(conn: ConnDep, _: DemoModeCheck) -> AuditVerifyResponse:
    """Verify audit chain structural integrity + SHA-256 hash chain.

    `valid` = seq contiguity, no duplicates.
    `hash_chain_valid` = every hashed entry's entry_hash recomputes correctly
    and each entry's parent_hash equals the previous hashed entry's entry_hash.
    Pre-epoch entries (NULL entry_hash) are skipped by hash verification.
    """
    entries = await export_audit_log(conn)
    canonical = to_canonical_json(entries)
    seqs = [e.seq for e in entries]
    seqs_set = set(seqs)

    counts = Counter(seqs)
    duplicates = sorted(s for s, n in counts.items() if n > 1)[:_MAX_REPORTED_GAPS]

    # Hash-chain verification over entries that carry hash data.
    hashed = [e for e in entries if e.entry_hash is not None]
    hash_chain_valid = True
    first_broken_seq: int | None = None

    # Verify genesis anchor: the first hashed entry must point to the
    # all-zeros sentinel, not an arbitrary value planted by a raw INSERT.
    if hashed and not hmac.compare_digest(hashed[0].parent_hash or "", _GENESIS_HASH):
        hash_chain_valid = False
        first_broken_seq = hashed[0].seq

    if hash_chain_valid:
        for i, entry in enumerate(hashed):
            kind_str = entry.kind.value if isinstance(entry.kind, AuditKind) else entry.kind
            expected_hash = compute_entry_hash(
                seq=entry.seq,
                kind=kind_str,
                payload=entry.payload,
                related_id=entry.related_id,
                actor=entry.actor,
                created_at=entry.created_at,
                parent_hash=entry.parent_hash if entry.parent_hash is not None else _GENESIS_HASH,
            )
            if not hmac.compare_digest(entry.entry_hash or "", expected_hash):
                hash_chain_valid = False
                first_broken_seq = entry.seq
                break
            if i > 0 and not hmac.compare_digest(
                entry.parent_hash or "", hashed[i - 1].entry_hash or ""
            ):
                hash_chain_valid = False
                first_broken_seq = entry.seq
                break

    if not seqs:
        return AuditVerifyResponse(
            valid=True,
            total_entries=0,
            min_seq=None,
            max_seq=None,
            missing_seqs=[],
            duplicate_seqs=[],
            canonical_export_bytes=len(canonical),
            checked_at=datetime.now(tz=UTC),
            hash_chain_valid=True,
            hash_chain_entries=0,
            first_broken_seq=None,
        )

    min_seq = min(seqs)
    max_seq = max(seqs)
    expected = set(range(min_seq, max_seq + 1))
    missing = sorted(expected - seqs_set)[:_MAX_REPORTED_GAPS]

    valid = not missing and not duplicates and len(seqs) == len(seqs_set)

    return AuditVerifyResponse(
        valid=valid,
        total_entries=len(entries),
        min_seq=min_seq,
        max_seq=max_seq,
        missing_seqs=missing,
        duplicate_seqs=duplicates,
        canonical_export_bytes=len(canonical),
        checked_at=datetime.now(tz=UTC),
        hash_chain_valid=hash_chain_valid,
        hash_chain_entries=len(hashed),
        first_broken_seq=first_broken_seq,
    )
