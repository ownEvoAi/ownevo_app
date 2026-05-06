"""Failure clustering pipeline (B3.2 / B3.3).

Entry points:
  cluster_failures(snapshots, embedder, reducer, clusterer, labeler) -> ClusteringResult
  persist_clustering_result(conn, *, workflow_id, result, source_trace_ids=)
      -> list[PersistedCluster]
  fetch_failure_cluster(conn, cluster_id) -> FailureCluster | None

The 4 stages are Protocols so unit tests can stub them. Production
implementations (sentence-transformers + UMAP + HDBSCAN + Anthropic
labeler) live in `default_impl.py`, gated on the `clustering` extra.

Quality enforcement (`ClusteringInsufficientData`) lives in `quality.py`
and runs BEFORE the LLM labeler so we don't pay tokens on rejected runs.
"""

from .persistence import (
    fetch_failure_cluster,
    persist_clustering_result,
)
from .pipeline import FailureLike, cluster_failures
from .quality import QualityThresholds, assign_severity, gate_assignment
from .types import (
    EMBEDDING_DIM,
    Clusterer,
    ClusteringInsufficientData,
    ClusteringResult,
    ClusteringSignal,
    ClusterSummary,
    Embedder,
    Labeler,
    PersistedCluster,
    RawClusterAssignment,
    Reducer,
)

__all__ = [
    "EMBEDDING_DIM",
    "ClusterSummary",
    "Clusterer",
    "ClusteringInsufficientData",
    "ClusteringResult",
    "ClusteringSignal",
    "Embedder",
    "FailureLike",
    "Labeler",
    "PersistedCluster",
    "QualityThresholds",
    "RawClusterAssignment",
    "Reducer",
    "assign_severity",
    "cluster_failures",
    "fetch_failure_cluster",
    "gate_assignment",
    "persist_clustering_result",
]
