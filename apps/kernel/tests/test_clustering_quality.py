"""Direct tests for `clustering.quality` (gate + severity)."""

from __future__ import annotations

import numpy as np
import pytest
from ownevo_kernel.clustering import (
    ClusteringInsufficientData,
    QualityThresholds,
    RawClusterAssignment,
    assign_severity,
    gate_assignment,
)


def _assignment(labels: list[int]) -> RawClusterAssignment:
    return RawClusterAssignment(labels=np.asarray(labels, dtype=np.int64))


# ---------------------------------------------------------------------------
# gate_assignment
# ---------------------------------------------------------------------------


def test_gate_too_few_points() -> None:
    with pytest.raises(ClusteringInsufficientData) as exc:
        gate_assignment(
            _assignment([0, 0, 0]),
            n_inputs=3,
            thresholds=QualityThresholds(),
        )
    assert exc.value.reason == "too-few-points"
    assert exc.value.n_inputs == 3


def test_gate_all_noise() -> None:
    with pytest.raises(ClusteringInsufficientData) as exc:
        gate_assignment(
            _assignment([-1, -1, -1, -1, -1, -1]),
            n_inputs=6,
            thresholds=QualityThresholds(),
        )
    assert exc.value.reason == "all-noise"
    assert exc.value.noise_count == 6


def test_gate_mega_cluster() -> None:
    # 20 in cluster 0, 2 in cluster 1 → 90.9% > 90%
    labels = [0] * 20 + [1, 1]
    with pytest.raises(ClusteringInsufficientData) as exc:
        gate_assignment(
            _assignment(labels),
            n_inputs=22,
            thresholds=QualityThresholds(),
        )
    assert exc.value.reason == "mega-cluster"
    assert exc.value.cluster_sizes == (20, 2)


def test_gate_single_cluster_passes() -> None:
    labels = [0] * 7
    survivors = gate_assignment(
        _assignment(labels),
        n_inputs=7,
        thresholds=QualityThresholds(),
    )
    assert list(survivors.keys()) == [0]
    assert len(survivors[0]) == 7


def test_gate_drops_singletons() -> None:
    labels = [0, 0, 0, 1]  # cluster 1 is a singleton
    survivors = gate_assignment(
        _assignment(labels),
        n_inputs=4,
        thresholds=QualityThresholds(min_inputs=4),
    )
    assert list(survivors.keys()) == [0]


def test_gate_label_count_mismatch_raises_value_error() -> None:
    with pytest.raises(ValueError, match="entries"):
        gate_assignment(
            _assignment([0, 0]),
            n_inputs=5,
            thresholds=QualityThresholds(min_inputs=2),
        )


def test_gate_all_singletons_treated_as_all_noise() -> None:
    # 5 inputs, each in its own cluster → all drop as singletons.
    labels = [0, 1, 2, 3, 4]
    with pytest.raises(ClusteringInsufficientData) as exc:
        gate_assignment(
            _assignment(labels),
            n_inputs=5,
            thresholds=QualityThresholds(),
        )
    assert exc.value.reason == "all-noise"


# ---------------------------------------------------------------------------
# assign_severity
# ---------------------------------------------------------------------------


def test_severity_high_for_large_cluster() -> None:
    assert assign_severity(cluster_size=20, mean_rmsse=0.3, total_clusters=4) == "high"
    assert assign_severity(cluster_size=50, mean_rmsse=None, total_clusters=4) == "high"


def test_severity_high_for_severe_rmsse() -> None:
    assert assign_severity(cluster_size=3, mean_rmsse=1.5, total_clusters=4) == "high"
    assert assign_severity(cluster_size=3, mean_rmsse=2.5, total_clusters=4) == "high"


def test_severity_medium_for_moderate_size() -> None:
    assert assign_severity(cluster_size=10, mean_rmsse=0.3, total_clusters=4) == "medium"


def test_severity_medium_for_moderate_rmsse() -> None:
    assert assign_severity(cluster_size=3, mean_rmsse=0.7, total_clusters=4) == "medium"


def test_severity_medium_when_few_total_clusters() -> None:
    assert assign_severity(cluster_size=3, mean_rmsse=0.2, total_clusters=2) == "medium"
    assert assign_severity(cluster_size=2, mean_rmsse=None, total_clusters=1) == "medium"


def test_severity_low_for_long_tail() -> None:
    assert assign_severity(cluster_size=3, mean_rmsse=0.2, total_clusters=8) == "low"
    assert assign_severity(cluster_size=2, mean_rmsse=None, total_clusters=5) == "low"
