"""B3.3 — cluster → eval-case promotion (DB-backed integration tests).

Skipped when `OWNEVO_DATABASE_URL` is unset.
Pure-Python plan_cluster_promotion tests live in test_plan_cluster_promotion.py.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import numpy as np
import pytest
from ownevo_kernel.benchmark.m5_failure_analyzer import M5FailureSnapshot
from ownevo_kernel.clustering import (
    EMBEDDING_DIM,
    ClusterSummary,
    PersistedCluster,
)
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.eval_cases import (
    ClusterPromotionError,
    list_eval_cases,
    promote_cluster_to_eval_cases,
    promote_clusters_to_eval_cases,
)
from ownevo_kernel.types import ProvenanceKind


def _snap(
    series_id: str,
    *,
    rmsse: float,
    reward: float = 0.5,
    hints: tuple[str, ...] = ("under-forecast",),
) -> M5FailureSnapshot:
    return M5FailureSnapshot(
        series_id=series_id,
        item_id=series_id.split("_CA")[0] if "CA" in series_id else "X",
        dept_id="HOBBIES_1",
        cat_id="HOBBIES",
        store_id="CA_1",
        state_id="CA",
        rmsse=rmsse,
        reward=reward,
        mean_actual=2.0,
        mean_predicted=1.5,
        peak_error_day_offset=3,
        peak_error_day_label="d_1900",
        peak_error_value=-2.0,
        feature_gap_hints=hints,
        text_signature=f"{series_id} sig",
    )


def _cluster(
    member_indices: tuple[int, ...],
    *,
    label: str = "winter footwear in Pacific NW Q4",
    severity: str = "high",
    cluster_id: uuid.UUID | None = None,
) -> PersistedCluster:
    summary = ClusterSummary(
        label=label,
        severity=severity,
        member_indices=member_indices,
        centroid=np.zeros(EMBEDDING_DIM, dtype=np.float32),
        quality_score=0.7,
        sample_signatures=("a", "b"),
    )
    return PersistedCluster(id=cluster_id or uuid.uuid4(), summary=summary)


# ---------------------------------------------------------------------------
# promote_* — DB-backed
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


async def _seed_workflow_and_cluster(
    db: asyncpg.Connection,
    *,
    workflow_id: str,
    cluster_id: uuid.UUID,
    label: str = "winter footwear in CA",
    severity: str = "high",
) -> None:
    await db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, 'wf', '{}'::jsonb)",
        workflow_id,
    )
    centroid = "[" + ",".join("0.0" for _ in range(EMBEDDING_DIM)) + "]"
    await db.execute(
        """
        INSERT INTO failure_clusters
            (id, workflow_id, label, severity, centroid, cluster_size)
        VALUES ($1, $2, $3, $4, $5::vector, $6)
        """,
        cluster_id,
        workflow_id,
        label,
        severity,
        centroid,
        3,
    )


async def test_promote_writes_one_case_per_member(db: asyncpg.Connection) -> None:
    workflow_id = "wf-promote-1"
    cluster_id = uuid.uuid4()
    await _seed_workflow_and_cluster(db, workflow_id=workflow_id, cluster_id=cluster_id)

    snaps = [
        _snap("HOBBIES_1_001_CA_1_validation", rmsse=2.0),
        _snap("HOBBIES_1_002_CA_1_validation", rmsse=1.5),
        _snap("HOBBIES_1_003_CA_1_validation", rmsse=1.0),
    ]
    cluster = _cluster((0, 1, 2), cluster_id=cluster_id)
    cases = await promote_cluster_to_eval_cases(
        db,
        workflow_id=workflow_id,
        cluster=cluster,
        snapshots=snaps,
    )
    assert len(cases) == 3
    assert {c.provenance for c in cases} == {ProvenanceKind.CLUSTER_DERIVED}
    assert all(c.cluster_id == cluster_id for c in cases)
    assert all(c.workflow_id == workflow_id for c in cases)

    # Worst RMSSE first in the returned list — the order the gate
    # will see when consuming `prior_eval_task_ids`.
    series_ids_in_order = [c.input["task_id"] for c in cases]
    assert series_ids_in_order == [
        "HOBBIES_1_001_CA_1_validation",
        "HOBBIES_1_002_CA_1_validation",
        "HOBBIES_1_003_CA_1_validation",
    ]
    # All 3 rows landed in DB.
    listed = await list_eval_cases(db, workflow_id=workflow_id)
    assert {c.input["task_id"] for c in listed} == set(series_ids_in_order)


async def test_promote_carries_expected_behavior_payload(db: asyncpg.Connection) -> None:
    workflow_id = "wf-promote-shape"
    cluster_id = uuid.uuid4()
    await _seed_workflow_and_cluster(
        db,
        workflow_id=workflow_id,
        cluster_id=cluster_id,
        label="winter footwear in Pacific NW Q4",
        severity="high",
    )
    snaps = [_snap("HOBBIES_1_001_CA_1_validation", rmsse=1.5, reward=0.22)]
    cluster = _cluster((0,), cluster_id=cluster_id, label="winter footwear in Pacific NW Q4")
    [case] = await promote_cluster_to_eval_cases(
        db,
        workflow_id=workflow_id,
        cluster=cluster,
        snapshots=snaps,
        min_reward_floor=0.40,
    )
    assert case.input["task_id"] == "HOBBIES_1_001_CA_1_validation"
    assert case.input["fold"] == "test"
    assert case.input["feature_gap_hints"] == ["under-forecast"]
    assert case.expected_behavior["min_reward"] == pytest.approx(0.40)
    assert case.expected_behavior["rmsse_at_promotion"] == pytest.approx(1.5)
    assert case.expected_behavior["reward_at_promotion"] == pytest.approx(0.22)
    assert case.expected_behavior["rationale"] == "winter footwear in Pacific NW Q4"
    assert case.expected_behavior["cluster_severity"] == "high"


async def test_promote_caps_at_max_cases_per_cluster(db: asyncpg.Connection) -> None:
    workflow_id = "wf-promote-cap"
    cluster_id = uuid.uuid4()
    await _seed_workflow_and_cluster(db, workflow_id=workflow_id, cluster_id=cluster_id)
    snaps = [_snap(f"HOBBIES_1_{i:03d}_CA_1_validation", rmsse=float(i + 1)) for i in range(8)]
    cluster = _cluster(tuple(range(8)), cluster_id=cluster_id)
    cases = await promote_cluster_to_eval_cases(
        db,
        workflow_id=workflow_id,
        cluster=cluster,
        snapshots=snaps,
        max_cases_per_cluster=3,
    )
    assert len(cases) == 3


async def test_promote_min_reward_floor_validated(db: asyncpg.Connection) -> None:
    workflow_id = "wf-promote-floor"
    cluster_id = uuid.uuid4()
    await _seed_workflow_and_cluster(db, workflow_id=workflow_id, cluster_id=cluster_id)
    snaps = [_snap("HOBBIES_1_001_CA_1_validation", rmsse=1.0)]
    cluster = _cluster((0,), cluster_id=cluster_id)
    with pytest.raises(ClusterPromotionError, match="min_reward_floor"):
        await promote_cluster_to_eval_cases(
            db,
            workflow_id=workflow_id,
            cluster=cluster,
            snapshots=snaps,
            min_reward_floor=1.5,
        )


async def test_promote_batch_writes_across_clusters(db: asyncpg.Connection) -> None:
    workflow_id = "wf-promote-batch"
    cluster_a = uuid.uuid4()
    cluster_b = uuid.uuid4()
    await db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, 'wf', '{}'::jsonb)",
        workflow_id,
    )
    centroid = "[" + ",".join("0.0" for _ in range(EMBEDDING_DIM)) + "]"
    for cid, label, sev in [(cluster_a, "A", "high"), (cluster_b, "B", "medium")]:
        await db.execute(
            """
            INSERT INTO failure_clusters
                (id, workflow_id, label, severity, centroid, cluster_size)
            VALUES ($1, $2, $3, $4, $5::vector, $6)
            """,
            cid, workflow_id, label, sev, centroid, 2,
        )
    snaps = [
        _snap("HOBBIES_1_001_CA_1_validation", rmsse=2.0),
        _snap("HOBBIES_1_002_CA_1_validation", rmsse=1.5),
        _snap("HOBBIES_1_003_CA_1_validation", rmsse=1.0),
        _snap("HOBBIES_1_004_CA_1_validation", rmsse=0.8),
    ]
    clusters = [
        _cluster((0, 1), cluster_id=cluster_a, label="A"),
        _cluster((2, 3), cluster_id=cluster_b, label="B", severity="medium"),
    ]
    cases = await promote_clusters_to_eval_cases(
        db,
        workflow_id=workflow_id,
        clusters=clusters,
        snapshots=snaps,
    )
    assert len(cases) == 4
    by_cluster: dict[uuid.UUID, list[str]] = {}
    for c in cases:
        by_cluster.setdefault(c.cluster_id, []).append(c.input["task_id"])
    assert by_cluster[cluster_a] == [
        "HOBBIES_1_001_CA_1_validation",
        "HOBBIES_1_002_CA_1_validation",
    ]
    assert by_cluster[cluster_b] == [
        "HOBBIES_1_003_CA_1_validation",
        "HOBBIES_1_004_CA_1_validation",
    ]


async def test_promote_rolls_back_on_failure(db: asyncpg.Connection) -> None:
    """If one INSERT fails, the whole batch rolls back."""
    workflow_id = "wf-promote-rollback"
    cluster_id = uuid.uuid4()
    await _seed_workflow_and_cluster(db, workflow_id=workflow_id, cluster_id=cluster_id)
    snaps = [
        _snap("HOBBIES_1_001_CA_1_validation", rmsse=2.0),
        _snap("HOBBIES_1_002_CA_1_validation", rmsse=1.5),
    ]
    cluster = _cluster((0, 1), cluster_id=cluster_id)
    # Trigger a FK violation by wiping the cluster row mid-flight is hard;
    # instead, point cluster_id to a non-existent UUID so add_eval_case's
    # FK to failure_clusters fails.
    bad_cluster = PersistedCluster(id=uuid.uuid4(), summary=cluster.summary)
    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
        await promote_cluster_to_eval_cases(
            db,
            workflow_id=workflow_id,
            cluster=bad_cluster,
            snapshots=snaps,
        )
    listed = await list_eval_cases(db, workflow_id=workflow_id)
    assert listed == []


async def test_promote_supports_test_fold_flag(db: asyncpg.Connection) -> None:
    workflow_id = "wf-promote-testfold"
    cluster_id = uuid.uuid4()
    await _seed_workflow_and_cluster(db, workflow_id=workflow_id, cluster_id=cluster_id)
    snaps = [_snap("HOBBIES_1_001_CA_1_validation", rmsse=1.0)]
    cluster = _cluster((0,), cluster_id=cluster_id)
    [case] = await promote_cluster_to_eval_cases(
        db,
        workflow_id=workflow_id,
        cluster=cluster,
        snapshots=snaps,
        is_test_fold=True,
    )
    assert case.is_test_fold is True
