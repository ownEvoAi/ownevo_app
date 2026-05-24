"""Cluster production traces by their tool failures.

The clustering pipeline (`cluster_failures`) was built for eval-case and
benchmark results: each input is a graded case the harness produced. But
traces ingested over the OTLP receiver (or any production trace, eval or
not) carry their own failure signal — a `tool_call_result` event with
`status="error"`. Until now nothing turned those into clustering input,
so an ingested LangChain/OpenLLMetry agent's failures landed in `traces`
and stopped there.

This module closes that gap. It reads a workflow's production traces,
extracts one failure snapshot per trace that contains a tool error,
runs the standard embed → reduce → cluster → label pipeline, and
persists the surviving clusters into `failure_clusters` with the
contributing trace_ids attached per cluster.

What counts as a "production" trace
-----------------------------------
A trace with `iteration_id IS NULL` — i.e. not produced by a gate
iteration / replay. That covers both OTLP-ingested traces
(`ingest_source='otlp'`) and any other non-iteration trace. Eval traces
already flow through the eval-case clustering path and would be
double-counted here.

When this runs
--------------
On demand, via `POST /api/workflows/{id}/cluster-production-failures`,
not automatically on every ingest — clustering is CPU-heavy
(embeddings + UMAP + HDBSCAN) and batching many traces into one run is
both cheaper and produces better clusters than re-clustering on each
single-trace flush. A scheduled job can call the same entry point.

The pipeline dependencies (Embedder / Reducer / Clusterer / Labeler)
are injected, exactly as the eval-case path injects them — so the
heavy sentence-transformers / UMAP / HDBSCAN stack stays optional and
unit tests can substitute deterministic stubs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from .persistence import insert_cluster
from .pipeline import cluster_failures

if TYPE_CHECKING:
    import asyncpg

    from .persistence import PersistedCluster
    from .quality import QualityThresholds
    from .types import Clusterer, Embedder, Labeler, Reducer

# Severity floor/boosts for the rmsse-analog the FailureLike Protocol
# reads. Range stays in [0.5, 1.0] to match the eval-case snapshot path.
_SEV_LOGICAL = 0.6  # tool raised a logical error inside the tool
_SEV_SANDBOX = 0.9  # sandbox runtime killed the call (Timeout/OOM/Crash)

_ERROR_MSG_TRUNCATE = 80


@dataclass(frozen=True)
class ProductionFailureSnapshot:
    """A FailureLike snapshot built from one trace's tool error.

    `text_signature` is the clustering input — a concrete one-liner
    naming the failure mode so the labeler and the embeddings group
    like with like. `severity_score` is exposed as `rmsse` for protocol
    parity with the M5 / NL-gen snapshots (pure naming inertia; the
    pipeline reads the attribute under that name).
    """

    text_signature: str
    severity_score: float

    @property
    def rmsse(self) -> float:
        return self.severity_score


def _first_tool_error(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first `tool_call_result` event with status='error'.

    One snapshot per trace keeps the clustering input one-failure-per-row
    (the same shape the eval-case path uses). The first error is the one
    that usually derails the run; later errors are often cascades.
    """
    for ev in events:
        if ev.get("type") == "tool_call_result" and ev.get("status") == "error":
            return ev
    return None


def _build_signature(error_event: dict[str, Any]) -> str:
    name = error_event.get("name") or "unknown-tool"
    error_class = error_event.get("error_class")
    error_msg = (error_event.get("error") or "").strip().replace("\n", " ")
    mode = error_class or "logical-error"
    truncated = error_msg[:_ERROR_MSG_TRUNCATE]
    return f"{mode} | tool={name} | {truncated}"


def _severity(error_event: dict[str, Any]) -> float:
    # A sandbox-runtime failure (error_class set: Timeout / OOM / Crash)
    # is more severe than a logical error returned from inside the tool.
    return _SEV_SANDBOX if error_event.get("error_class") else _SEV_LOGICAL


def extract_failure_snapshots(
    trace_rows: list[tuple[UUID, list[dict[str, Any]]]],
) -> list[tuple[UUID, ProductionFailureSnapshot]]:
    """Build one snapshot per trace that carries a tool error.

    `trace_rows` is `(trace_id, events)` pairs; `events` is the decoded
    JSONB array. Traces with no tool error are dropped — clustering only
    runs on failures, same as every other snapshot path. Order is
    preserved so the caller can map cluster member indices back to
    trace_ids.
    """
    out: list[tuple[UUID, ProductionFailureSnapshot]] = []
    for trace_id, events in trace_rows:
        error_event = _first_tool_error(events)
        if error_event is None:
            continue
        out.append(
            (
                trace_id,
                ProductionFailureSnapshot(
                    text_signature=_build_signature(error_event),
                    severity_score=_severity(error_event),
                ),
            )
        )
    return out


async def _fetch_production_traces(
    conn: asyncpg.Connection,
    workflow_id: str,
) -> list[tuple[UUID, list[dict[str, Any]]]]:
    """Read a workflow's production traces (iteration_id IS NULL).

    Eval traces (iteration_id set) are excluded — they cluster through
    the eval-case path and would double-count here.
    """
    rows = await conn.fetch(
        """
        SELECT id, events
        FROM traces
        WHERE workflow_id = $1 AND iteration_id IS NULL
        ORDER BY started_at
        """,
        workflow_id,
    )
    result: list[tuple[UUID, list[dict[str, Any]]]] = []
    for row in rows:
        events = row["events"]
        if isinstance(events, str):
            events = json.loads(events)
        result.append((row["id"], list(events or [])))
    return result


async def cluster_production_failures(
    conn: asyncpg.Connection,
    workflow_id: str,
    *,
    embedder: Embedder,
    reducer: Reducer,
    clusterer: Clusterer,
    labeler: Labeler,
    thresholds: QualityThresholds | None = None,
) -> list[PersistedCluster]:
    """Cluster a workflow's production-trace tool failures and persist.

    Returns the persisted clusters (empty when there are too few failing
    traces to cluster, or none). Each persisted cluster carries the
    trace_ids of its members in `sample_trace_ids`, so the Failures tab
    can deep-link from a cluster back to the traces that formed it.
    """
    trace_rows = await _fetch_production_traces(conn, workflow_id)
    snapshots_with_ids = extract_failure_snapshots(trace_rows)
    if not snapshots_with_ids:
        return []

    trace_ids = [tid for tid, _ in snapshots_with_ids]
    snapshots = [snap for _, snap in snapshots_with_ids]

    result = cluster_failures(
        snapshots,
        embedder=embedder,
        reducer=reducer,
        clusterer=clusterer,
        labeler=labeler,
        thresholds=thresholds,
    )

    persisted: list[PersistedCluster] = []
    async with conn.transaction():
        for summary in result.clusters:
            member_trace_ids = [trace_ids[i] for i in summary.member_indices]
            persisted.append(
                await insert_cluster(
                    conn,
                    workflow_id=workflow_id,
                    summary=summary,
                    sample_trace_ids=member_trace_ids,
                )
            )
    return persisted
