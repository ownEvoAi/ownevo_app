"""Clustering pipeline types — Protocols + dataclasses (B3.2).

The pipeline is structured around 4 swappable stages so unit tests can
stub them deterministically and the production wiring (sentence-
transformers + UMAP + HDBSCAN + Anthropic) lives behind an optional
`clustering` extra:

  Embedder    text   -> ndarray (n, dim)
  Reducer     ndarray (n, dim) -> ndarray (n, reduced_dim)
  Clusterer   ndarray (n, reduced_dim) -> labels + persistence
  Labeler     cluster sample texts -> human-readable label

Quality enforcement (insufficient data / mega-cluster / all-noise) lives
in `clustering.quality` and operates on `RawClusterAssignment` before
labels are paid for from the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID

import numpy as np

# Sentence-transformers all-MiniLM-L6-v2 dim. Pinned in the schema
# (`failure_clusters.centroid` is `vector(384)`); the kernel refuses to
# persist any other size.
EMBEDDING_DIM: int = 384


class ClusteringSignal(StrEnum):
    """Top-level outcome of a clustering pass.

    OK                    — at least one valid cluster surfaced; safe to persist.
    INSUFFICIENT_DATA     — too few inputs, all-noise, or mega-cluster.
                            Caller should NOT promote eval cases this round.
    """

    OK = "ok"
    INSUFFICIENT_DATA = "insufficient-data"


class ClusteringInsufficientData(RuntimeError):
    """Raised by the pipeline when quality thresholds reject the run.

    The `reason` is one of: `too-few-points`, `all-noise`, `mega-cluster`.
    Carries the input count + observed cluster sizes so the caller can
    surface a meaningful UI state ("more iterations needed; got 3 traces,
    need ≥ 5").
    """

    def __init__(
        self,
        *,
        reason: str,
        n_inputs: int,
        cluster_sizes: tuple[int, ...] = (),
        noise_count: int = 0,
    ) -> None:
        self.reason = reason
        self.n_inputs = n_inputs
        self.cluster_sizes = cluster_sizes
        self.noise_count = noise_count
        super().__init__(
            f"clustering rejected: reason={reason} n_inputs={n_inputs} "
            f"cluster_sizes={cluster_sizes} noise={noise_count}",
        )


@dataclass(frozen=True)
class RawClusterAssignment:
    """Per-point label + per-cluster persistence from the clusterer.

    `labels[i]` is the cluster id for point `i`, or -1 for noise (HDBSCAN
    convention). `persistence[c]` is HDBSCAN's per-cluster persistence in
    [0, 1] — used as the cluster's `quality_score`. Reducers / clusterers
    that don't expose persistence return an empty dict; downstream defaults
    to `None`.
    """

    labels: np.ndarray  # (n,), int
    persistence: dict[int, float] = field(default_factory=dict)


class Embedder(Protocol):
    """Maps a list of texts to a (n, EMBEDDING_DIM) float array."""

    def embed(self, texts: list[str]) -> np.ndarray: ...


class Reducer(Protocol):
    """Maps embeddings to a lower-dim space for HDBSCAN."""

    def reduce(self, embeddings: np.ndarray) -> np.ndarray: ...


class Clusterer(Protocol):
    """Cluster reduced embeddings; emits HDBSCAN-style assignments."""

    def cluster(self, reduced: np.ndarray) -> RawClusterAssignment: ...


class Labeler(Protocol):
    """Map a cluster's sample texts → one-line human-readable label."""

    def label(self, sample_texts: list[str], cluster_index: int) -> str: ...


@dataclass(frozen=True)
class ClusterSummary:
    """One labelled cluster, ready to persist into `failure_clusters`.

    `member_indices` are positions into the original snapshot list the
    pipeline was called with — the persistence layer joins them back to
    series_ids when building eval cases.
    """

    label: str
    severity: str  # "high" | "medium" | "low" — see clustering.quality
    member_indices: tuple[int, ...]
    centroid: np.ndarray  # (EMBEDDING_DIM,) float32
    quality_score: float | None
    sample_signatures: tuple[str, ...]
    """Up to 5 representative `text_signature`s for human / LLM review."""


@dataclass(frozen=True)
class ClusteringResult:
    """Result of one clustering pass.

    `signal` is OK only when at least one `clusters` entry was produced
    AND the quality gate passed. INSUFFICIENT_DATA results have an empty
    `clusters` list and an `insufficient_data_reason` explaining why.
    """

    signal: ClusteringSignal
    clusters: tuple[ClusterSummary, ...] = ()
    n_inputs: int = 0
    n_noise: int = 0
    insufficient_data_reason: str | None = None


@dataclass(frozen=True)
class PersistedCluster:
    """The DB row id paired with the in-memory summary that produced it.

    `eval_cases.cluster_id` referencing this row is what closes the
    cluster → eval-case loop in B3.3.
    """

    id: UUID
    summary: ClusterSummary
