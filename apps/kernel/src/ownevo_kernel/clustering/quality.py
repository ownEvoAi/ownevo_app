"""Quality enforcement + severity assignment for the clustering pipeline.

`gate_assignment` runs BEFORE the LLM labeler so we don't pay for tokens
on clusterings that won't be persisted. Three failure modes are guarded
explicitly because each has been observed in the auto-harness traces:

  too-few-points  — fewer than `min_inputs` snapshots; HDBSCAN would
                    report "all noise" and the result would be useless
                    even if produced.
  all-noise       — HDBSCAN labelled every point -1. Happens when
                    embeddings are too uniform (e.g., one repeated
                    failure mode, no structure).
  mega-cluster    — One cluster contains > `mega_cluster_threshold`
                    fraction of the non-noise points. Real M5 failure
                    distributions split across 3-6 clusters; one giant
                    cluster means the embedder collapsed structure or
                    HDBSCAN's `min_cluster_size` was set too low.

`assign_severity` derives the schema's `severity ∈ {high, medium, low}`
from per-cluster signal. The thresholds are tuned to surface "the agent
should look here first" without being so loud that 80% of clusters get
flagged `high`. Inputs:
  - `cluster_size` (members in this cluster)
  - `mean_rmsse` across members (when available) — bigger misses are
    more urgent than just "more rows".
  - Total-cluster count — when a workflow has 1-2 clusters every cluster
    should at least be `medium` so the UI shows something actionable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .types import (
    ClusteringInsufficientData,
    RawClusterAssignment,
)


@dataclass(frozen=True)
class QualityThresholds:
    """All thresholds the quality gate enforces. Frozen so tests can
    construct deviations without leaking state."""

    min_inputs: int = 5
    """Below this many embedded points, HDBSCAN can't form a meaningful
    cluster — fail fast with `too-few-points`."""

    mega_cluster_threshold: float = 0.9
    """If any one cluster owns > this fraction of NON-noise points, the
    run is rejected as `mega-cluster`. 0.9 = 'one cluster has > 90% of
    the non-noise points; the embedder didn't separate the failure
    modes'."""

    min_cluster_size: int = 2
    """A cluster must have at least this many members to survive into
    the persisted result. HDBSCAN can produce singleton clusters when
    `min_cluster_size=1`; we don't promote them — a single point is not
    a pattern."""


def gate_assignment(
    assignment: RawClusterAssignment,
    *,
    n_inputs: int,
    thresholds: QualityThresholds,
) -> dict[int, np.ndarray]:
    """Apply quality gates and return surviving clusters.

    Returns a dict mapping `cluster_label -> indices array` for the
    clusters that pass. Raises `ClusteringInsufficientData` if the run
    as a whole should be rejected.

    Singleton-or-smaller clusters are dropped silently (their points
    contribute to the noise count); the run is rejected only when the
    GLOBAL signal is bad (too few inputs, all-noise, mega-cluster).
    """
    if n_inputs < thresholds.min_inputs:
        raise ClusteringInsufficientData(
            reason="too-few-points",
            n_inputs=n_inputs,
        )

    labels = assignment.labels
    if labels.shape[0] != n_inputs:
        raise ValueError(
            f"assignment.labels has {labels.shape[0]} entries but "
            f"n_inputs={n_inputs}; clusterer/embedder lengths disagree.",
        )

    # Bucket points by label. `-1` is HDBSCAN noise.
    by_cluster: dict[int, list[int]] = {}
    noise_count = 0
    for i, lbl in enumerate(labels.tolist()):
        lbl_int = int(lbl)
        if lbl_int == -1:
            noise_count += 1
            continue
        by_cluster.setdefault(lbl_int, []).append(i)

    if not by_cluster:
        # Every point was noise.
        raise ClusteringInsufficientData(
            reason="all-noise",
            n_inputs=n_inputs,
            noise_count=noise_count,
        )

    sizes = tuple(len(v) for v in by_cluster.values())
    non_noise = sum(sizes)
    biggest = max(sizes)
    # Mega-cluster only meaningful when there's >1 cluster — a single
    # cluster trivially owns 100%, but if HDBSCAN returned exactly one
    # cluster we still want to surface it as "found a single pattern"
    # rather than reject. So: only fire when there are 2+ clusters AND
    # one swallows most of the points.
    if (
        len(by_cluster) >= 2
        and non_noise > 0
        and (biggest / non_noise) > thresholds.mega_cluster_threshold
    ):
        raise ClusteringInsufficientData(
            reason="mega-cluster",
            n_inputs=n_inputs,
            cluster_sizes=sizes,
            noise_count=noise_count,
        )

    # Filter out singletons-or-smaller. They're not useful and bloat the
    # eval suite with one-off cases.
    survivors: dict[int, np.ndarray] = {}
    for lbl, members in by_cluster.items():
        if len(members) < thresholds.min_cluster_size:
            continue
        survivors[lbl] = np.asarray(members, dtype=np.int64)

    if not survivors:
        # All clusters were singletons; treat as all-noise for the
        # caller — there's nothing to promote.
        raise ClusteringInsufficientData(
            reason="all-noise",
            n_inputs=n_inputs,
            cluster_sizes=sizes,
            noise_count=noise_count + non_noise,
        )

    return survivors


def assign_severity(
    *,
    cluster_size: int,
    mean_rmsse: float | None,
    total_clusters: int,
) -> str:
    """Map per-cluster signal to `{high, medium, low}` per the schema check.

    - `high`   — large cluster (>= 20) OR severe miss (rmsse >= 1.5)
    - `medium` — moderate signal OR low total cluster count
    - `low`    — everything else

    Tuning intent: in a workflow with 1-2 clusters, every cluster should
    be at least medium so the UI surfaces an actionable card. In a
    workflow with 5+ clusters, low is acceptable for the long-tail ones.
    """
    if cluster_size >= 20:
        return "high"
    if mean_rmsse is not None and mean_rmsse >= 1.5:
        return "high"
    if cluster_size >= 5:
        return "medium"
    if mean_rmsse is not None and mean_rmsse >= 0.7:
        return "medium"
    if total_clusters <= 2:
        return "medium"
    return "low"
