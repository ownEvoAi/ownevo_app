"""DB-backed integration tests for clustering persistence (B3.2).

Skipped when `OWNEVO_DATABASE_URL` is unset so unit-only CI stays green.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import numpy as np
import pytest
from ownevo_kernel.clustering import (
    EMBEDDING_DIM,
    ClusteringResult,
    ClusteringSignal,
    ClusterSummary,
    fetch_failure_cluster,
    persist_clustering_result,
)
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


def _make_summary(
    *,
    label: str = "winter footwear in CA",
    severity: str = "high",
    member_indices: tuple[int, ...] = (0, 1, 2),
    centroid_value: float = 0.1,
    quality_score: float | None = 0.72,
) -> ClusterSummary:
    centroid = np.full(EMBEDDING_DIM, centroid_value, dtype=np.float32)
    return ClusterSummary(
        label=label,
        severity=severity,
        member_indices=member_indices,
        centroid=centroid,
        quality_score=quality_score,
        sample_signatures=("a", "b", "c"),
    )


async def _seed_workflow(conn: asyncpg.Connection, workflow_id: str) -> None:
    await conn.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, $2, '{}'::jsonb)",
        workflow_id,
        "test workflow",
    )


async def test_persist_one_cluster_roundtrip(db: asyncpg.Connection) -> None:
    workflow_id = "wf-cluster-1"
    await _seed_workflow(db, workflow_id)
    trace_id = uuid.uuid4()

    result = ClusteringResult(
        signal=ClusteringSignal.OK,
        clusters=(_make_summary(),),
        n_inputs=10,
        n_noise=2,
    )
    persisted = await persist_clustering_result(
        db,
        workflow_id=workflow_id,
        result=result,
        source_trace_ids=[trace_id],
    )
    assert len(persisted) == 1
    pc = persisted[0]
    assert pc.summary.label == "winter footwear in CA"

    fetched = await fetch_failure_cluster(db, pc.id)
    assert fetched is not None
    assert fetched.label == "winter footwear in CA"
    assert fetched.severity == "high"
    assert fetched.cluster_size == 3
    assert fetched.workflow_id == workflow_id
    assert fetched.sample_trace_ids == [trace_id]
    assert fetched.quality_score == pytest.approx(0.72)
    # Centroid round-trip
    assert fetched.centroid is not None
    assert len(fetched.centroid) == EMBEDDING_DIM
    assert all(abs(v - 0.1) < 1e-5 for v in fetched.centroid[:5])


async def test_insufficient_data_persists_nothing(db: asyncpg.Connection) -> None:
    workflow_id = "wf-cluster-empty"
    await _seed_workflow(db, workflow_id)
    result = ClusteringResult(
        signal=ClusteringSignal.INSUFFICIENT_DATA,
        clusters=(),
        n_inputs=2,
        n_noise=2,
        insufficient_data_reason="too-few-points",
    )
    persisted = await persist_clustering_result(
        db,
        workflow_id=workflow_id,
        result=result,
    )
    assert persisted == []
    rows = await db.fetch(
        "SELECT id FROM failure_clusters WHERE workflow_id = $1",
        workflow_id,
    )
    assert rows == []


async def test_multiple_clusters_persist_in_one_transaction(
    db: asyncpg.Connection,
) -> None:
    workflow_id = "wf-cluster-multi"
    await _seed_workflow(db, workflow_id)
    summaries = (
        _make_summary(label="A", member_indices=(0, 1, 2), centroid_value=0.1),
        _make_summary(
            label="B",
            severity="medium",
            member_indices=(3, 4, 5, 6),
            centroid_value=0.2,
        ),
        _make_summary(
            label="C",
            severity="low",
            member_indices=(7, 8),
            centroid_value=0.3,
            quality_score=None,
        ),
    )
    result = ClusteringResult(
        signal=ClusteringSignal.OK,
        clusters=summaries,
        n_inputs=9,
        n_noise=0,
    )
    persisted = await persist_clustering_result(
        db,
        workflow_id=workflow_id,
        result=result,
    )
    assert [p.summary.label for p in persisted] == ["A", "B", "C"]

    rows = await db.fetch(
        "SELECT label, severity, cluster_size, quality_score FROM failure_clusters "
        "WHERE workflow_id = $1 ORDER BY label",
        workflow_id,
    )
    assert [(r["label"], r["severity"], r["cluster_size"]) for r in rows] == [
        ("A", "high", 3),
        ("B", "medium", 4),
        ("C", "low", 2),
    ]
    # quality_score=None on cluster C should round-trip as NULL.
    c_quality = next(r["quality_score"] for r in rows if r["label"] == "C")
    assert c_quality is None


async def test_centroid_wrong_size_rejected_by_schema(
    db: asyncpg.Connection,
) -> None:
    """If a caller hand-builds a summary with the wrong centroid size,
    the schema's `vector(384)` constraint should block the INSERT."""
    workflow_id = "wf-cluster-bad-dim"
    await _seed_workflow(db, workflow_id)
    bad_summary = ClusterSummary(
        label="bad",
        severity="low",
        member_indices=(0, 1),
        centroid=np.zeros(100, dtype=np.float32),  # wrong dim
        quality_score=None,
        sample_signatures=(),
    )
    result = ClusteringResult(
        signal=ClusteringSignal.OK,
        clusters=(bad_summary,),
        n_inputs=2,
    )
    with pytest.raises(asyncpg.exceptions.DataError):
        await persist_clustering_result(
            db,
            workflow_id=workflow_id,
            result=result,
        )
    # Transaction rolled back — no row written.
    rows = await db.fetch(
        "SELECT id FROM failure_clusters WHERE workflow_id = $1",
        workflow_id,
    )
    assert rows == []


async def test_fetch_unknown_id_returns_none(db: asyncpg.Connection) -> None:
    assert await fetch_failure_cluster(db, uuid.uuid4()) is None
