"""Unit tests for plan_cluster_promotion — no DB required.

These run in unit-only CI (no OWNEVO_DATABASE_URL needed).
DB-backed promote_* tests live in test_eval_cases_from_cluster.py.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest
from ownevo_kernel.benchmark.m5_failure_analyzer import M5FailureSnapshot
from ownevo_kernel.clustering import (
    EMBEDDING_DIM,
    ClusterSummary,
    PersistedCluster,
)
from ownevo_kernel.eval_cases import (
    ClusterPromotionError,
    plan_cluster_promotion,
)


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


def test_plan_orders_by_rmsse_descending() -> None:
    snaps = [
        _snap("HOBBIES_1_001_CA_1_validation", rmsse=0.5),
        _snap("HOBBIES_1_002_CA_1_validation", rmsse=2.0),
        _snap("HOBBIES_1_003_CA_1_validation", rmsse=1.0),
    ]
    cluster = _cluster((0, 1, 2))
    plan = plan_cluster_promotion(cluster=cluster, snapshots=snaps)
    assert plan.series_ids == (
        "HOBBIES_1_002_CA_1_validation",
        "HOBBIES_1_003_CA_1_validation",
        "HOBBIES_1_001_CA_1_validation",
    )
    assert plan.rmsse_per_series == (2.0, 1.0, 0.5)
    assert plan.cluster_label == "winter footwear in Pacific NW Q4"
    assert plan.severity == "high"


def test_plan_caps_at_max_cases_per_cluster() -> None:
    snaps = [_snap(f"HOBBIES_1_{i:03d}_CA_1_validation", rmsse=float(i)) for i in range(10)]
    cluster = _cluster(tuple(range(10)))
    plan = plan_cluster_promotion(cluster=cluster, snapshots=snaps, max_cases_per_cluster=3)
    assert len(plan.series_ids) == 3


def test_plan_rejects_out_of_range_member_index() -> None:
    snaps = [_snap("HOBBIES_1_001_CA_1_validation", rmsse=1.0)]
    cluster = _cluster((0, 5))  # 5 doesn't exist
    with pytest.raises(ClusterPromotionError, match="out of range"):
        plan_cluster_promotion(cluster=cluster, snapshots=snaps)


def test_plan_rejects_non_finite_rmsse() -> None:
    snaps = [_snap("HOBBIES_1_001_CA_1_validation", rmsse=float("nan"))]
    cluster = _cluster((0,))
    with pytest.raises(ClusterPromotionError, match="non-finite"):
        plan_cluster_promotion(cluster=cluster, snapshots=snaps)
