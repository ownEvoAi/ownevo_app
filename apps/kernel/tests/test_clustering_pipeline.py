"""Tests for the clustering pipeline (B3.2) — fully stubbed.

No DB, no LLM, no model downloads. Embedder/Reducer/Clusterer/Labeler
are tiny in-process stubs that produce deterministic outputs we can
assert against.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest
from ownevo_kernel.clustering import (
    EMBEDDING_DIM,
    ClusteringResult,
    ClusteringSignal,
    QualityThresholds,
    RawClusterAssignment,
    cluster_failures,
)
from ownevo_kernel.clustering.pipeline import _pick_samples, _validate_embeddings


@dataclass
class _Snap:
    text_signature: str
    rmsse: float


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class HashEmbedder:
    """Deterministic embedder: hash(text) → 384-d float32 unit vector.

    Identical texts produce identical embeddings (collisions tested).
    """

    def __init__(self, *, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = abs(hash(t)) % (2**31 - 1)
            rng = np.random.default_rng(seed)
            v = rng.normal(size=self.dim).astype(np.float32)
            v /= np.linalg.norm(v) + 1e-9
            out[i] = v
        return out


class IdentityReducer:
    def reduce(self, embeddings: np.ndarray) -> np.ndarray:
        return embeddings


class FixedClusterer:
    """Returns pre-set labels regardless of input."""

    def __init__(
        self,
        labels: list[int],
        *,
        persistence: dict[int, float] | None = None,
    ) -> None:
        self.labels = np.asarray(labels, dtype=np.int64)
        self.persistence = persistence or {}

    def cluster(self, reduced: np.ndarray) -> RawClusterAssignment:
        return RawClusterAssignment(
            labels=self.labels,
            persistence=self.persistence,
        )


class FixedLabeler:
    """Returns f'cluster-{idx}' unless overridden per-cluster."""

    def __init__(self, mapping: dict[int, str] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[tuple[int, list[str]]] = []

    def label(self, sample_texts: list[str], cluster_index: int) -> str:
        self.calls.append((cluster_index, list(sample_texts)))
        return self.mapping.get(cluster_index, f"cluster-{cluster_index}")


def _make_snapshots(n: int = 6) -> list[_Snap]:
    return [_Snap(text_signature=f"sig-{i}", rmsse=0.5 + i * 0.1) for i in range(n)]


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


def test_two_clusters_happy_path() -> None:
    snaps = _make_snapshots(6)
    # 3 points in cluster 0, 3 in cluster 1.
    clusterer = FixedClusterer(
        [0, 0, 0, 1, 1, 1],
        persistence={0: 0.7, 1: 0.55},
    )
    labeler = FixedLabeler({0: "winter footwear in CA", 1: "snack-aisle drift"})
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=labeler,
    )
    assert out.signal is ClusteringSignal.OK
    assert len(out.clusters) == 2
    labels = [c.label for c in out.clusters]
    assert labels == ["winter footwear in CA", "snack-aisle drift"]
    sizes = [len(c.member_indices) for c in out.clusters]
    assert sizes == [3, 3]
    assert out.n_inputs == 6
    assert out.n_noise == 0
    # Quality scores forwarded
    assert out.clusters[0].quality_score == pytest.approx(0.7)
    assert out.clusters[1].quality_score == pytest.approx(0.55)
    # Centroid shape pinned to schema dim
    assert out.clusters[0].centroid.shape == (EMBEDDING_DIM,)
    assert out.clusters[0].centroid.dtype == np.float32


def test_clusters_returned_in_label_ascending_order() -> None:
    snaps = _make_snapshots(6)
    # Mixed label order; pipeline must sort.
    clusterer = FixedClusterer([2, 0, 2, 0, 1, 1])
    labeler = FixedLabeler()
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=labeler,
    )
    # Cluster summaries should be in label order: 0, 1, 2.
    # Labeler is called with cluster_index 0, 1, 2 (sequential after sort).
    assert [c[0] for c in labeler.calls] == [0, 1, 2]
    assert len(out.clusters) == 3


def test_noise_points_counted_but_not_clustered() -> None:
    snaps = _make_snapshots(6)
    clusterer = FixedClusterer([0, 0, 0, -1, -1, 1])  # 2 noise; 1 singleton (label=1)
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
    )
    # Singleton cluster (label=1) drops; noise=2 reported.
    assert out.signal is ClusteringSignal.OK
    assert len(out.clusters) == 1
    assert out.clusters[0].member_indices == (0, 1, 2)
    assert out.n_noise == 2


def test_sample_signatures_capped_at_5_worst_first() -> None:
    snaps = [_Snap(text_signature=f"s{i}", rmsse=float(i)) for i in range(8)]
    clusterer = FixedClusterer([0] * 8)
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
    )
    assert len(out.clusters) == 1
    sigs = out.clusters[0].sample_signatures
    assert len(sigs) == 5
    # Worst RMSSE first → s7, s6, s5, s4, s3
    assert sigs == ("s7", "s6", "s5", "s4", "s3")


def test_label_falls_back_when_labeler_returns_empty() -> None:
    snaps = _make_snapshots(5)
    clusterer = FixedClusterer([0, 0, 0, 0, 0])

    class EmptyLabeler:
        def label(self, sample_texts: list[str], cluster_index: int) -> str:
            return "   "

    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=EmptyLabeler(),
    )
    assert out.clusters[0].label == "cluster-0"


# ---------------------------------------------------------------------------
# Quality-gate paths
# ---------------------------------------------------------------------------


def test_too_few_inputs_returns_insufficient_data() -> None:
    snaps = _make_snapshots(3)  # below min_inputs=5
    clusterer = FixedClusterer([0, 0, 0])
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
    )
    assert out.signal is ClusteringSignal.INSUFFICIENT_DATA
    assert out.insufficient_data_reason == "too-few-points"
    assert out.clusters == ()


def test_zero_inputs_returns_insufficient_data() -> None:
    out = cluster_failures(
        [],
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=FixedClusterer([]),
        labeler=FixedLabeler(),
    )
    assert out.signal is ClusteringSignal.INSUFFICIENT_DATA
    assert out.n_inputs == 0


def test_all_noise_returns_insufficient_data() -> None:
    snaps = _make_snapshots(6)
    clusterer = FixedClusterer([-1, -1, -1, -1, -1, -1])
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
    )
    assert out.signal is ClusteringSignal.INSUFFICIENT_DATA
    assert out.insufficient_data_reason == "all-noise"


def test_mega_cluster_returns_insufficient_data() -> None:
    # 10 points in cluster 0, 1 point in cluster 1 → 91% in one cluster.
    # min_cluster_size=2 means cluster 1 (size=1) drops. With cluster 1
    # dropped before mega-cluster check, we'd lose the signal — so
    # construct it with 10 + 1 + 1 (cluster 2 has 1 point; will also drop).
    # Better: 10 in cluster 0, 2 in cluster 1 — 10/12 = 83% (below 90%).
    # Use 19 + 2 = 21 → 19/21 = 90.5%.
    snaps = _make_snapshots(21)
    clusterer = FixedClusterer([0] * 19 + [1, 1])
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
    )
    assert out.signal is ClusteringSignal.INSUFFICIENT_DATA
    assert out.insufficient_data_reason == "mega-cluster"


def test_single_cluster_with_all_points_is_NOT_mega() -> None:
    """If HDBSCAN returns exactly one cluster (no others), surface it.

    Mega-cluster fires only when there are 2+ clusters and one swallows
    most of the points — i.e., the embedder failed to separate modes.
    A single cluster on its own is just 'one pattern found'.
    """
    snaps = _make_snapshots(10)
    clusterer = FixedClusterer([0] * 10)
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
    )
    assert out.signal is ClusteringSignal.OK
    assert len(out.clusters) == 1


def test_mega_cluster_threshold_can_be_loosened() -> None:
    """Caller can override thresholds for adversarial/test workflows."""
    snaps = _make_snapshots(21)
    clusterer = FixedClusterer([0] * 19 + [1, 1])
    thresholds = QualityThresholds(mega_cluster_threshold=0.99)
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
        thresholds=thresholds,
    )
    assert out.signal is ClusteringSignal.OK


# ---------------------------------------------------------------------------
# Severity assignment (round-trip through pipeline)
# ---------------------------------------------------------------------------


def test_severity_high_when_cluster_is_large() -> None:
    snaps = [_Snap(text_signature=f"s{i}", rmsse=0.5) for i in range(25)]
    clusterer = FixedClusterer([0] * 25)
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
    )
    assert out.clusters[0].severity == "high"


def test_severity_high_when_rmsse_severe() -> None:
    snaps = [_Snap(text_signature=f"s{i}", rmsse=2.0) for i in range(6)]
    clusterer = FixedClusterer([0] * 6)
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
    )
    assert out.clusters[0].severity == "high"


def test_severity_low_for_long_tail() -> None:
    # 4+ small clusters → tail clusters are low.
    snaps = [_Snap(text_signature=f"s{i}", rmsse=0.3) for i in range(12)]
    clusterer = FixedClusterer([0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3])
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=clusterer,
        labeler=FixedLabeler(),
    )
    severities = {c.severity for c in out.clusters}
    assert severities == {"low"}


# ---------------------------------------------------------------------------
# Validation guards
# ---------------------------------------------------------------------------


def test_embedder_must_return_correct_dim() -> None:
    class WrongDimEmbedder:
        def embed(self, texts: list[str]) -> np.ndarray:
            return np.zeros((len(texts), 100), dtype=np.float32)

    snaps = _make_snapshots(6)
    with pytest.raises(ValueError, match="dim=100"):
        cluster_failures(
            snaps,
            embedder=WrongDimEmbedder(),
            reducer=IdentityReducer(),
            clusterer=FixedClusterer([0, 0, 0, 1, 1, 1]),
            labeler=FixedLabeler(),
        )


def test_embedder_nan_rejected() -> None:
    class NaNEmbedder:
        def embed(self, texts: list[str]) -> np.ndarray:
            arr = np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
            arr[0, 0] = np.nan
            return arr

    snaps = _make_snapshots(6)
    with pytest.raises(ValueError, match="NaN"):
        cluster_failures(
            snaps,
            embedder=NaNEmbedder(),
            reducer=IdentityReducer(),
            clusterer=FixedClusterer([0, 0, 0, 1, 1, 1]),
            labeler=FixedLabeler(),
        )


def test_reducer_must_preserve_alignment() -> None:
    class TruncatingReducer:
        def reduce(self, embeddings: np.ndarray) -> np.ndarray:
            return embeddings[:-1]  # drops a row

    snaps = _make_snapshots(6)
    with pytest.raises(ValueError, match="alignment-preserving"):
        cluster_failures(
            snaps,
            embedder=HashEmbedder(),
            reducer=TruncatingReducer(),
            clusterer=FixedClusterer([0, 0, 0, 1, 1, 1]),
            labeler=FixedLabeler(),
        )


def test_pick_samples_returns_worst_first() -> None:
    snaps = [_Snap(text_signature=f"s{i}", rmsse=float(i)) for i in range(7)]
    out = _pick_samples(snaps, [0, 1, 2, 3, 4, 5, 6])
    assert out == [6, 5, 4, 3, 2]


def test_validate_embeddings_dim_mismatch_message() -> None:
    arr = np.zeros((3, 100), dtype=np.float32)
    with pytest.raises(ValueError, match="dim=100"):
        _validate_embeddings(arr, n=3)


def test_pipeline_returns_typed_result() -> None:
    snaps = _make_snapshots(6)
    out = cluster_failures(
        snaps,
        embedder=HashEmbedder(),
        reducer=IdentityReducer(),
        clusterer=FixedClusterer([0, 0, 0, 1, 1, 1]),
        labeler=FixedLabeler(),
    )
    assert isinstance(out, ClusteringResult)
