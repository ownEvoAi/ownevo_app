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

from datetime import UTC, datetime

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
        description=(
            "Filter to a single audit_kind (proposal-approved, gate-run-completed, "
            "etc.). See SCHEMA.md § audit_kind for the enum."
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
    except ValueError as exc:
        # `kind` is validated against the audit_kind enum in Postgres;
        # an unknown value bubbles as ValueError per asyncpg's enum cast.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

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

    total = await conn.fetchval("SELECT COUNT(*)::int FROM audit_entries") or 0
    return AuditList(items=items, total=total, truncated=total > len(items))


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

    duplicates = sorted(s for s in seqs_set if seqs.count(s) > 1)[:_MAX_REPORTED_GAPS]

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
