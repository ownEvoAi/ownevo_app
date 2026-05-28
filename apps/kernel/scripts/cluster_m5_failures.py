"""Cluster M5 failures end-to-end (B3.1 + B3.2 + B3.3).

What this does
--------------
1. Re-runs the in-process M5 LightGBM baseline (no sandbox required) so
   we have a fresh `M5RunArtifacts` with predictions / actuals / scales.
2. Runs `analyze_m5_failures` on the artifacts and prints the top-k
   worst-predicted series with structured context (B3.1).
3. Runs `cluster_failures` over the snapshots' `text_signature`s using a
   deterministic stub embedder by default (no model downloads, no LLM
   calls). `--real` flips to sentence-transformers + UMAP + HDBSCAN +
   Anthropic. The stub is enough to validate the pipeline end-to-end.
4. If `OWNEVO_DATABASE_URL` is set: persists clusters + promotes each
   cluster to `eval_cases` rows tagged `provenance=cluster-derived`.

Exit codes
----------
0  pipeline ran end-to-end (any signal)
2  M5 dataset missing / malformed
3  fold construction failed
4  DB connection failed when DB writes were requested

Usage
-----
  make m5-cluster-failures               # in-process baseline + stubs, DB if set
  make m5-cluster-failures CLUSTER_ARGS='--real --top-k 30'
  python scripts/cluster_m5_failures.py --no-db --top-k 20
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from baselines.m5_lightgbm import run_baseline  # noqa: E402
from ownevo_kernel.benchmark import (  # noqa: E402
    M5BenchmarkRunner,
    M5FailureSnapshot,
    analyze_m5_failures,
)
from ownevo_kernel.clustering import (  # noqa: E402
    EMBEDDING_DIM,
    Clusterer,
    ClusteringResult,
    ClusteringSignal,
    Embedder,
    Labeler,
    QualityThresholds,
    RawClusterAssignment,
    cluster_failures,
    persist_clustering_result,
)
from ownevo_kernel.datasets import (  # noqa: E402
    M5DatasetError,
    load_m5,
    make_held_out_fold,
)
from ownevo_kernel.eval_cases import promote_clusters_to_eval_cases  # noqa: E402
from ownevo_kernel.tenant_session import DEFAULT_WORKSPACE_ID, WorkspaceBindError, connect_workspace_conn  # noqa: E402

ENV_M5_DIR = "OWNEVO_M5_DIR"
ENV_DB_URL = "OWNEVO_DATABASE_URL"
DEFAULT_WORKFLOW_ID = "m5-demand-prediction"


# ---------------------------------------------------------------------------
# Stub Embedder / Clusterer / Labeler — deterministic, no network
# ---------------------------------------------------------------------------


class _HashEmbedder:
    """sha256 of `text_signature` → 384-d float32 normalized vector.

    Same text => same vector. Pure-stdlib + numpy. Used so a smoketest
    of the pipeline shape doesn't need to download `all-MiniLM-L6-v2`.
    """

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            h = hashlib.sha256(t.encode("utf-8")).digest()
            seed = int.from_bytes(h[:4], "big") & 0x7FFFFFFF
            rng = np.random.default_rng(seed)
            v = rng.normal(size=EMBEDDING_DIM).astype(np.float32)
            v /= np.linalg.norm(v) + 1e-9
            out[i] = v
        return out


class _IdentityReducer:
    def reduce(self, embeddings: np.ndarray) -> np.ndarray:
        return embeddings



def build_stub_clusterer(snapshots: list[M5FailureSnapshot]) -> Clusterer:
    """Return a clusterer that buckets `snapshots` by (cat_id, primary hint).

    The Clusterer Protocol receives the reduced embeddings (which we
    don't actually consume here) — the labels we return are by-position,
    matching the snapshots passed at pipeline time. The pipeline preserves
    snapshot ordering through embedder/reducer, so position alignment holds.
    """
    keys: list[tuple[str, str]] = []
    for s in snapshots:
        primary_hint = s.feature_gap_hints[0] if s.feature_gap_hints else "no-hint"
        keys.append((s.cat_id, primary_hint))

    # Deduplicate while preserving first-seen order so cluster ids are stable.
    seen: dict[tuple[str, str], int] = {}
    labels: list[int] = []
    for k in keys:
        if k not in seen:
            seen[k] = len(seen)
        labels.append(seen[k])

    arr = np.asarray(labels, dtype=np.int64)
    persistence = {lbl: 0.6 for lbl in seen.values()}

    class Pluggable:
        def cluster(self, reduced: np.ndarray) -> RawClusterAssignment:
            if reduced.shape[0] != arr.shape[0]:
                raise ValueError(
                    f"clusterer alignment lost: reduced={reduced.shape[0]} "
                    f"but pre-bound labels={arr.shape[0]}",
                )
            return RawClusterAssignment(labels=arr, persistence=persistence)

    return Pluggable()


class _StubLabeler:
    """Builds a label from the first sample signature's hierarchy + hint."""

    def label(self, sample_texts: list[str], cluster_index: int) -> str:
        if not sample_texts:
            return f"cluster-{cluster_index}"
        # Each text_signature looks like:
        #   "HOBBIES_1_001_CA_1_validation [HOBBIES/HOBBIES_1 @ CA/CA_1] rmsse=...
        #    peak +X.XX day N hints=[under-forecast,zero-inflated]"
        first = sample_texts[0]
        cat = "unknown"
        hint = "drift"
        with contextlib.suppress(IndexError):
            cat = first.split("[")[1].split("/")[0]
        if "hints=[" in first:
            after = first.split("hints=[")[1]
            tag = after.split(",")[0].split("]")[0]
            if tag and tag != "none":
                hint = tag
        return f"{cat} — {hint}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliArgs:
    m5_dir: Path
    val_days: int
    test_days: int
    workflow_id: str
    no_db: bool
    top_k: int
    real: bool
    max_cases_per_cluster: int
    min_reward_floor: float
    pretty: bool


def _parse_args(argv: list[str] | None = None) -> CliArgs:
    parser = argparse.ArgumentParser(
        description="Cluster M5 failures end-to-end (B3.1 + B3.2 + B3.3).",
    )
    parser.add_argument(
        "--m5-dir",
        type=Path,
        default=Path(os.environ.get(ENV_M5_DIR, "data/m5")),
        help=f"Path to M5 CSVs (default: ${ENV_M5_DIR} or ./data/m5).",
    )
    parser.add_argument("--val-days", type=int, default=28)
    parser.add_argument("--test-days", type=int, default=28)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Skip DB writes even if OWNEVO_DATABASE_URL is set.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=50,
        help="How many worst-predicted series to feed into clustering (default: 50).",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help=(
            "Use sentence-transformers + UMAP + HDBSCAN + Anthropic instead of stubs. "
            "Requires `pip install ownevo-kernel[clustering,agent]`."
        ),
    )
    parser.add_argument(
        "--max-cases-per-cluster",
        type=int,
        default=5,
        help="Cap on eval_cases promoted per cluster (default: 5).",
    )
    parser.add_argument(
        "--min-reward-floor",
        type=float,
        default=0.30,
        help="Per-case `expected_behavior.min_reward` (default: 0.30).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Emit indented JSON instead of the default summary lines.",
    )
    ns = parser.parse_args(argv)
    return CliArgs(
        m5_dir=ns.m5_dir,
        val_days=ns.val_days,
        test_days=ns.test_days,
        workflow_id=ns.workflow_id,
        no_db=ns.no_db,
        top_k=ns.top_k,
        real=ns.real,
        max_cases_per_cluster=ns.max_cases_per_cluster,
        min_reward_floor=ns.min_reward_floor,
        pretty=ns.pretty,
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def main_async(args: CliArgs) -> int:
    try:
        catalog = load_m5(args.m5_dir)
    except M5DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        fold = make_held_out_fold(
            catalog,
            val_days=args.val_days,
            test_days=args.test_days,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=run_baseline)
    await runner.run()
    arts = runner.last_artifacts
    if arts is None:
        raise RuntimeError("Baseline runner did not populate last_artifacts")

    snapshots = analyze_m5_failures(arts, fold=fold, k=args.top_k)
    print(
        f"analyzed {len(snapshots)} worst-predicted series of "
        f"{len(arts.series_ids)} total",
        file=sys.stderr,
    )

    embedder, reducer, clusterer, labeler = _build_stages(snapshots, real=args.real)
    result = cluster_failures(
        snapshots,
        embedder=embedder,
        reducer=reducer,
        clusterer=clusterer,
        labeler=labeler,
        thresholds=QualityThresholds(),
    )

    summary = _result_summary(result, top_k=args.top_k)

    db_url = os.environ.get(ENV_DB_URL)
    if not args.no_db and db_url and result.signal is ClusteringSignal.OK:
        import asyncpg

        try:
            async with (
                connect_workspace_conn(db_url, DEFAULT_WORKSPACE_ID) as conn,
                conn.transaction(),
            ):
                await _ensure_workflow_row(conn, args.workflow_id)
                persisted = await persist_clustering_result(
                    conn,
                    workflow_id=args.workflow_id,
                    result=result,
                )
                cases = await promote_clusters_to_eval_cases(
                    conn,
                    workflow_id=args.workflow_id,
                    clusters=persisted,
                    snapshots=snapshots,
                    max_cases_per_cluster=args.max_cases_per_cluster,
                    min_reward_floor=args.min_reward_floor,
                )
        except (WorkspaceBindError, asyncpg.PostgresError, OSError) as exc:
            print(f"error: could not connect to DB: {exc}", file=sys.stderr)
            return 4
        summary["persisted_clusters"] = len(persisted)
        summary["promoted_eval_cases"] = len(cases)
    elif not db_url and not args.no_db:
        print(
            f"\nnote: {ENV_DB_URL} not set — skipping DB persistence "
            "(clusters + eval cases not written).",
            file=sys.stderr,
        )

    print(json.dumps(summary, indent=2 if args.pretty else None))
    return 0


def _build_stages(
    snapshots: list[M5FailureSnapshot],
    *,
    real: bool,
) -> tuple[Embedder, object, Clusterer, Labeler]:
    if real:
        from ownevo_kernel.clustering.default_impl import (  # type: ignore[import-not-found]
            AnthropicLabeler,
            HDBSCANClusterer,
            SentenceTransformerEmbedder,
            UMAPReducer,
        )

        return (
            SentenceTransformerEmbedder(),
            UMAPReducer(),
            HDBSCANClusterer(),
            AnthropicLabeler(),
        )
    return (
        _HashEmbedder(),
        _IdentityReducer(),
        build_stub_clusterer(snapshots),
        _StubLabeler(),
    )


def _result_summary(result: ClusteringResult, *, top_k: int) -> dict[str, object]:
    return {
        "signal": result.signal.value,
        "n_inputs_clustered": result.n_inputs,
        "n_noise": result.n_noise,
        "n_clusters": len(result.clusters),
        "insufficient_data_reason": result.insufficient_data_reason,
        "top_k": top_k,
        "clusters": [
            {
                "label": c.label,
                "severity": c.severity,
                "size": len(c.member_indices),
                "quality_score": c.quality_score,
                "samples": list(c.sample_signatures[:3]),
            }
            for c in result.clusters
        ],
    }


async def _ensure_workflow_row(conn: object, workflow_id: str) -> None:
    """Idempotent upsert — `cluster_m5_failures` stands alone from
    `m5_baseline.py`, so we don't depend on it having seeded the row."""
    await conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, $2, '{}'::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        workflow_id,
        "M5 demand-prediction (cluster_m5_failures.py upsert)",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
