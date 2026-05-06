"""Clustering pipeline orchestrator (B3.2).

`cluster_failures(snapshots, embedder, reducer, clusterer, labeler)`
runs the 4-stage pipeline and returns a `ClusteringResult`. Inputs are
`M5FailureSnapshot`s (B3.1) but only the `text_signature` and `rmsse`
fields are read — any object exposing the same attributes works (a
duck-typed `FailureLike` Protocol is exposed for the NL-gen reuse path
in W5.3).

Pipeline:
  1. Embed each snapshot's `text_signature` -> (n, 384)
  2. Reduce -> (n, k) for HDBSCAN to chew on
  3. Cluster (HDBSCAN-style; -1 = noise)
  4. Quality gate (`quality.gate_assignment`)
  5. For each surviving cluster, ask the labeler for a one-line label,
     compute centroid (mean of original embeddings, NOT reduced), pick
     up to 5 representative sample signatures.

The pipeline never touches the DB or the LLM directly — `Labeler` is a
Protocol the caller wires (stub for tests, Anthropic in prod).
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

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
    Reducer,
)

_MAX_SAMPLE_SIGNATURES = 5


class FailureLike(Protocol):
    """The duck-typed shape `cluster_failures` needs from each snapshot.

    `M5FailureSnapshot` satisfies this. The NL-gen reuse path (W5.3)
    will define its own snapshot type that does too, so the clustering
    pipeline doesn't take an M5 dependency.
    """

    text_signature: str
    rmsse: float


def cluster_failures(
    snapshots: list[FailureLike],
    *,
    embedder: Embedder,
    reducer: Reducer,
    clusterer: Clusterer,
    labeler: Labeler,
    thresholds: QualityThresholds | None = None,
) -> ClusteringResult:
    """Run embed → reduce → cluster → label → summarize.

    On `ClusteringInsufficientData`, returns a result with
    `signal=INSUFFICIENT_DATA` and an empty `clusters` tuple instead of
    raising — the caller usually wants to log the reason and continue
    (the UI surfaces a "more iterations needed" state).
    """
    thresholds = thresholds or QualityThresholds()
    n = len(snapshots)

    if n == 0:
        return ClusteringResult(
            signal=ClusteringSignal.INSUFFICIENT_DATA,
            clusters=(),
            n_inputs=0,
            n_noise=0,
            insufficient_data_reason="too-few-points",
        )

    texts = [s.text_signature for s in snapshots]
    embeddings = embedder.embed(texts)
    _validate_embeddings(embeddings, n=n)

    reduced = reducer.reduce(embeddings)
    if reduced.shape[0] != n:
        raise ValueError(
            f"reducer returned {reduced.shape[0]} rows for {n} inputs; "
            "the reducer must be alignment-preserving.",
        )

    assignment = clusterer.cluster(reduced)

    try:
        survivors = gate_assignment(assignment, n_inputs=n, thresholds=thresholds)
    except ClusteringInsufficientData as exc:
        return ClusteringResult(
            signal=ClusteringSignal.INSUFFICIENT_DATA,
            clusters=(),
            n_inputs=n,
            n_noise=exc.noise_count,
            insufficient_data_reason=exc.reason,
        )

    # Stable cluster ordering by label so callers' sample_trace_ids /
    # eval_case lists are deterministic across runs.
    ordered_labels = sorted(survivors.keys())
    n_clusters = len(ordered_labels)
    n_noise = int(np.sum(assignment.labels == -1))

    summaries: list[ClusterSummary] = []
    for cluster_idx, lbl in enumerate(ordered_labels):
        member_indices = survivors[lbl]
        member_embeddings = embeddings[member_indices]
        centroid = member_embeddings.mean(axis=0).astype(np.float32, copy=False)

        sample_indices = _pick_samples(snapshots, member_indices.tolist())
        sample_signatures = tuple(snapshots[i].text_signature for i in sample_indices)

        label_text = labeler.label(list(sample_signatures), cluster_idx).strip()
        if not label_text:
            label_text = f"cluster-{cluster_idx}"

        rmsses = [snapshots[i].rmsse for i in member_indices.tolist()]
        mean_rmsse = float(np.mean(rmsses)) if rmsses else None

        severity = assign_severity(
            cluster_size=int(len(member_indices)),
            mean_rmsse=mean_rmsse,
            total_clusters=n_clusters,
        )
        quality_score = assignment.persistence.get(int(lbl))

        summaries.append(
            ClusterSummary(
                label=label_text,
                severity=severity,
                member_indices=tuple(int(i) for i in member_indices.tolist()),
                centroid=centroid,
                quality_score=float(quality_score) if quality_score is not None else None,
                sample_signatures=sample_signatures,
            )
        )

    return ClusteringResult(
        signal=ClusteringSignal.OK,
        clusters=tuple(summaries),
        n_inputs=n,
        n_noise=n_noise,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _validate_embeddings(embeddings: np.ndarray, *, n: int) -> None:
    if embeddings.ndim != 2:
        raise ValueError(
            f"embedder returned {embeddings.ndim}D array; expected 2D.",
        )
    if embeddings.shape[0] != n:
        raise ValueError(
            f"embedder returned {embeddings.shape[0]} rows for {n} inputs.",
        )
    if embeddings.shape[1] != EMBEDDING_DIM:
        raise ValueError(
            f"embedder returned dim={embeddings.shape[1]}; "
            f"failure_clusters.centroid is `vector({EMBEDDING_DIM})` and "
            "refuses any other size.",
        )
    if not np.all(np.isfinite(embeddings)):
        raise ValueError("embedder returned NaN/inf — refuses to persist.")


def _pick_samples(
    snapshots: list[FailureLike],
    member_indices: list[int],
) -> list[int]:
    """Choose up to 5 representative samples — worst RMSSE first.

    'Worst first' surfaces the most informative cases to the LLM
    labeler and to the human reviewer in the cluster card.
    """
    ordered = sorted(member_indices, key=lambda i: -snapshots[i].rmsse)
    return ordered[:_MAX_SAMPLE_SIGNATURES]
