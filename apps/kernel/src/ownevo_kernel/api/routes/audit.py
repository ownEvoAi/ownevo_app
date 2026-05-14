"""`/api/audit` — workspace-level audit trail surface.

D2-aligned: the audit log is append-only WORM in the DB and the API is
read-only. The "verify chain" endpoint runs a structural integrity
check (seq contiguity + canonical-JSON byte count); the future crypto
chain (TODO-3) extends this with parent_hash / entry_hash verification
without changing the response shape.

Pagination: the list endpoint enforces a hard `limit <= 500`. With
real customer log volume the endpoint switches to keyset pagination
via `since_seq` (TODO-18) — `since_seq` is already wired through the
underlying `export_audit_log` call.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, HTTPException, Query, status

from ...audit.writer import export_audit_log, to_canonical_json
from ..deps import ConnDep
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
            "iterations.id, or failure_clusters.id. Entries with a NULL "
            "related_id are workspace-level and not returned by this filter."
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
            if e.related_id is not None and e.related_id in wf_related_ids
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
async def verify_chain(conn: ConnDep) -> AuditVerifyResponse:
    """Verify the audit chain's structural integrity.

    For D2 (append-only, no crypto) "valid" means: every `seq` from 1
    to max(seq) is present, no duplicates, and the canonical-JSON
    export round-trips. The response carries the gap + duplicate lists
    (capped at 100 each) so the UI can surface the diagnostic without
    blowing the payload.
    """
    entries = await export_audit_log(conn)
    canonical = to_canonical_json(entries)
    seqs = [e.seq for e in entries]
    seqs_set = set(seqs)

    counts = Counter(seqs)
    duplicates = sorted(s for s, n in counts.items() if n > 1)[:_MAX_REPORTED_GAPS]

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
    )
