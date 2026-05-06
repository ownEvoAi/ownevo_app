"""Cluster → eval-case promotion (B3.3).

Closes the loop from `failure_clusters` rows to `eval_cases` rows so the
gate's regression-suite step has cluster-derived cases to enforce on the
next iteration. One INSERT per cluster member, all under one transaction
so a partial promotion can't leave dangling cases.

Shape contract — eval-case payloads:
  input = {
      "task_id": series_id,                # what the gate scores against
      "series_id": series_id,
      "fold": "test",                      # cluster-derived cases live in
                                           # the held-out fold by default
                                           # (the gate's `include_test_fold`
                                           # opt-in is gate-runner-only)
      "feature_gap_hints": [...],          # carried forward from B3.1 for
                                           # the agent's reasoning
  }
  expected_behavior = {
      "min_reward": <float>,               # the gate's per-case threshold
      "rmsse_at_promotion": <float>,       # frozen baseline for human review
      "reward_at_promotion": <float>,
      "rationale": <cluster.label>,        # human-readable "why is this
                                           # here?"
      "cluster_severity": <str>,
  }

`min_reward` defaults to a lenient threshold (0.30) for cluster-derived
cases. The cases describe series the agent currently FAILS — promoting
them with `min_reward = current_reward + epsilon` would block every
future iteration until the agent fixes them. A lenient floor instead
encodes "don't make it worse than this" while letting iteration
continue. Tighten via `min_reward_floor=` once the cluster's series are
demonstrably under control.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import asyncpg

from ..benchmark.m5_failure_analyzer import M5FailureSnapshot
from ..clustering import PersistedCluster
from ..types import EvalCase, ProvenanceKind
from .registry import add_eval_case

_DEFAULT_MAX_CASES_PER_CLUSTER = 5
_DEFAULT_MIN_REWARD_FLOOR = 0.30
_DEFAULT_REGRESSION_TOLERANCE = 0.05


class ClusterPromotionError(ValueError):
    """Raised when caller-provided inputs are inconsistent.

    Examples:
      - A `PersistedCluster.summary.member_indices` references a row not
        present in `snapshots` (off-by-one in the caller's slicing).
      - `min_reward_floor` is outside [0, 1].
    """


@dataclass(frozen=True)
class PromotionPlan:
    """Returned by `plan_cluster_promotion` so callers can preview which
    series will be promoted before writing rows.

    Useful for the smoke-test CLI ("show me what would land in the eval
    suite if I run with these settings") without paying for INSERTs.
    """

    cluster_id: str
    cluster_label: str
    severity: str
    series_ids: tuple[str, ...]
    rmsse_per_series: tuple[float, ...]


def plan_cluster_promotion(
    *,
    cluster: PersistedCluster,
    snapshots: list[M5FailureSnapshot],
    max_cases_per_cluster: int = _DEFAULT_MAX_CASES_PER_CLUSTER,
) -> PromotionPlan:
    """Compute which `snapshots` rows the cluster would promote.

    Selection rule: worst RMSSE first, capped at `max_cases_per_cluster`.
    The same rule the pipeline used when picking `sample_signatures`.
    """
    members = _resolve_members(cluster, snapshots)
    members.sort(key=lambda s: -s.rmsse)
    chosen = members[:max_cases_per_cluster]
    return PromotionPlan(
        cluster_id=str(cluster.id),
        cluster_label=cluster.summary.label,
        severity=cluster.summary.severity,
        series_ids=tuple(s.series_id for s in chosen),
        rmsse_per_series=tuple(s.rmsse for s in chosen),
    )


async def promote_cluster_to_eval_cases(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    cluster: PersistedCluster,
    snapshots: list[M5FailureSnapshot],
    max_cases_per_cluster: int = _DEFAULT_MAX_CASES_PER_CLUSTER,
    min_reward_floor: float = _DEFAULT_MIN_REWARD_FLOOR,
    regression_tolerance: float | None = _DEFAULT_REGRESSION_TOLERANCE,
    is_test_fold: bool = False,
) -> list[EvalCase]:
    """Insert one eval_case per promoted member of `cluster`.

    Single transaction — partial promotion never leaves the suite in a
    half-built state.

    `min_reward_floor` is the per-case `expected_behavior.min_reward`.
    `regression_tolerance` is the per-case slip the gate tolerates;
    None = use the gate's workflow default.
    """
    if not 0.0 <= min_reward_floor <= 1.0:
        raise ClusterPromotionError(
            f"min_reward_floor must be in [0,1]; got {min_reward_floor}",
        )
    plan = plan_cluster_promotion(
        cluster=cluster,
        snapshots=snapshots,
        max_cases_per_cluster=max_cases_per_cluster,
    )
    members_by_sid = {
        s.series_id: s for s in _resolve_members(cluster, snapshots)
    }

    written: list[EvalCase] = []
    async with conn.transaction():
        for sid in plan.series_ids:
            snap = members_by_sid[sid]
            input_payload = {
                "task_id": sid,
                "series_id": sid,
                "fold": "test",
                "feature_gap_hints": list(snap.feature_gap_hints),
            }
            expected = {
                "min_reward": min_reward_floor,
                "rmsse_at_promotion": snap.rmsse,
                "reward_at_promotion": snap.reward,
                "rationale": cluster.summary.label,
                "cluster_severity": cluster.summary.severity,
                "peak_error_value": snap.peak_error_value,
                "peak_error_day_offset": snap.peak_error_day_offset,
            }
            case = await add_eval_case(
                conn,
                workflow_id=workflow_id,
                provenance=ProvenanceKind.CLUSTER_DERIVED,
                cluster_id=cluster.id,
                input=input_payload,
                expected_behavior=expected,
                regression_tolerance=regression_tolerance,
                is_test_fold=is_test_fold,
            )
            written.append(case)
    return written


async def promote_clusters_to_eval_cases(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    clusters: Iterable[PersistedCluster],
    snapshots: list[M5FailureSnapshot],
    max_cases_per_cluster: int = _DEFAULT_MAX_CASES_PER_CLUSTER,
    min_reward_floor: float = _DEFAULT_MIN_REWARD_FLOOR,
    regression_tolerance: float | None = _DEFAULT_REGRESSION_TOLERANCE,
    is_test_fold: bool = False,
) -> list[EvalCase]:
    """Promote a batch of clusters in one transaction.

    Same shape as `promote_cluster_to_eval_cases` but iterates over
    every cluster in the batch. Returns a flat list across all of them.
    """
    written: list[EvalCase] = []
    async with conn.transaction():
        for cluster in clusters:
            written.extend(
                await promote_cluster_to_eval_cases(
                    conn,
                    workflow_id=workflow_id,
                    cluster=cluster,
                    snapshots=snapshots,
                    max_cases_per_cluster=max_cases_per_cluster,
                    min_reward_floor=min_reward_floor,
                    regression_tolerance=regression_tolerance,
                    is_test_fold=is_test_fold,
                )
            )
    return written


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_members(
    cluster: PersistedCluster,
    snapshots: list[M5FailureSnapshot],
) -> list[M5FailureSnapshot]:
    out: list[M5FailureSnapshot] = []
    n = len(snapshots)
    for idx in cluster.summary.member_indices:
        if idx < 0 or idx >= n:
            raise ClusterPromotionError(
                f"cluster {cluster.id} member_index {idx} is out of range "
                f"for snapshots (n={n}); caller passed mismatched lists.",
            )
        snap = snapshots[idx]
        if not math.isfinite(snap.rmsse):
            raise ClusterPromotionError(
                f"snapshot at index {idx} has non-finite rmsse={snap.rmsse}",
            )
        out.append(snap)
    return out
