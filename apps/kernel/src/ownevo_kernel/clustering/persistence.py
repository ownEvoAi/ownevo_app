"""Write `ClusteringResult` rows into `failure_clusters` (B3.2 / B3.3 seam).

Single transaction per call; one row per cluster. The centroid is
serialized as a pgvector literal (`'[v1,v2,...]'::vector`) — asyncpg
doesn't ship a native pgvector codec by default and the kernel doesn't
take a `pgvector` dep just to write 384 floats once per iteration.

`sample_trace_ids` defaults to `[source_trace_id]` when supplied — for
M5 there's typically one trace per iteration that produced the
predictions being clustered. If the caller has multiple sources
(re-clustering across N iterations), pass them via `source_trace_ids`.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

import asyncpg

from ..types import FailureCluster
from .types import ClusteringResult, ClusteringSignal, ClusterSummary, PersistedCluster


async def persist_clustering_result(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    result: ClusteringResult,
    source_trace_ids: list[UUID] | None = None,
) -> list[PersistedCluster]:
    """Insert one row per cluster in `result.clusters`.

    Returns the persisted rows paired with their in-memory summaries so
    the caller can build the eval-case batch (B3.3) without re-querying.

    No-ops on `INSUFFICIENT_DATA` results — returns an empty list.
    Caller should log `result.insufficient_data_reason` themselves.
    """
    if result.signal is not ClusteringSignal.OK:
        return []
    if not result.clusters:
        return []

    sample_ids = list(source_trace_ids) if source_trace_ids else []
    persisted: list[PersistedCluster] = []
    async with conn.transaction():
        for summary in result.clusters:
            persisted.append(
                await insert_cluster(
                    conn,
                    workflow_id=workflow_id,
                    summary=summary,
                    sample_trace_ids=sample_ids,
                )
            )
    return persisted


async def fetch_failure_cluster(
    conn: asyncpg.Connection,
    cluster_id: UUID,
) -> FailureCluster | None:
    """Read a `failure_clusters` row back as the typed model."""
    row = await conn.fetchrow(
        """
        SELECT id, workflow_id, label, label_eval_score, severity,
               centroid::text AS centroid, sample_trace_ids,
               cluster_size, quality_score, created_at
        FROM failure_clusters
        WHERE id = $1
        """,
        cluster_id,
    )
    if row is None:
        return None
    return FailureCluster(
        id=row["id"],
        workflow_id=row["workflow_id"],
        label=row["label"],
        label_eval_score=_to_float(row["label_eval_score"]),
        severity=row["severity"],
        centroid=_parse_pgvector(row["centroid"]),
        sample_trace_ids=list(row["sample_trace_ids"] or []),
        cluster_size=row["cluster_size"],
        quality_score=_to_float(row["quality_score"]),
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def insert_cluster(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    summary: ClusterSummary,
    sample_trace_ids: list[UUID],
) -> PersistedCluster:
    """Insert one failure_clusters row. Exposed (vs `_insert_cluster`) so
    the iteration runner can drive per-cluster `sample_trace_ids` without
    rewriting the pgvector + fingerprint plumbing."""
    centroid_literal = _to_pgvector_literal(summary.centroid.tolist())
    quality = (
        round(float(summary.quality_score), 2) if summary.quality_score is not None else None
    )
    fingerprint = _fingerprint(workflow_id, summary.label, len(summary.member_indices))
    row = await conn.fetchrow(
        """
        INSERT INTO failure_clusters (
            workflow_id, label, severity, centroid,
            sample_trace_ids, cluster_size, quality_score, fingerprint
        )
        VALUES ($1, $2, $3, $4::vector, $5::uuid[], $6, $7, $8)
        ON CONFLICT (fingerprint) DO NOTHING
        RETURNING id
        """,
        workflow_id,
        summary.label,
        summary.severity,
        centroid_literal,
        sample_trace_ids,
        len(summary.member_indices),
        quality,
        fingerprint,
    )
    if row is None:
        # Conflict: a cluster with this fingerprint already exists (re-run).
        existing = await conn.fetchrow(
            "SELECT id FROM failure_clusters WHERE fingerprint = $1",
            fingerprint,
        )
        if existing is None:
            raise RuntimeError(
                f"ON CONFLICT lost the row for fingerprint {fingerprint!r}",
            )
        cluster_id = existing["id"]
    else:
        cluster_id = row["id"]
    return PersistedCluster(id=cluster_id, summary=summary)


def _fingerprint(workflow_id: str, label: str, cluster_size: int) -> str:
    raw = f"{workflow_id}|{label}|{cluster_size}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _to_pgvector_literal(values: list[float]) -> str:
    """Serialize a Python list into pgvector's input format `[v1,v2,...]`.

    Uses `repr` rather than `str` so we don't lose precision in the
    last bit — pgvector accepts up to 8 decimal digits for `vector`,
    and `repr(float)` round-trips. Avoids `numpy.array2string` (which
    can wrap and emit `array(...)`).
    """
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _parse_pgvector(text: str | None) -> list[float] | None:
    if text is None:
        return None
    inner = text.strip().lstrip("[").rstrip("]")
    if not inner:
        return []
    return [float(part) for part in inner.split(",")]


def _to_float(v: object) -> float | None:
    if v is None:
        return None
    return float(v)  # type: ignore[arg-type]
