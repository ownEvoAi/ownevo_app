"""Cluster NL-gen failures end-to-end (W5.3).

What this does
--------------
1. Loads the 3 hand-authored NL-gen fixtures (demand-prediction, credit-risk,
   contract-review) — `WorkflowSpec` + `SimulationPlan` + `EvalCaseSet` per
   workflow, ~12 cases each.
2. Drives a deliberately-buggy stub agent over every case (`--strategy`
   selects which failure pattern; default `miss-derived-and-train-fps`
   produces the failure-mode mix the W3 clustering pipeline needs to
   form ≥3 clusters). `--real` flips to a real Anthropic agent via
   `solve_with_agent` — costly, requires `ANTHROPIC_API_KEY`.
3. Runs `analyze_nl_gen_failures` per workflow to produce the W3-shaped
   `NLGenFailureSnapshot`s, then concatenates across workflows so
   clustering sees the full failure population.
4. Runs `cluster_failures` over the snapshots' `text_signature`s. Stub
   embedder/clusterer/labeler by default (no model downloads, no LLM
   calls); `--real` flips to sentence-transformers + UMAP + HDBSCAN +
   Anthropic.
5. If `OWNEVO_DATABASE_URL` is set: persists clusters via
   `persist_clustering_result` under one transaction. **Cluster→eval-
   case promotion is deferred** — the existing `from_cluster.py` is
   M5-typed; the NL-gen-shaped promotion helper lands in a follow-up.

Exit codes
----------
0  pipeline ran end-to-end (any signal)
2  fixture loading failed
3  stub strategy unrecognized
4  DB connection failed when DB writes were requested
5  agreement gate not met (only when `--require-clusters N` is set)

Usage
-----
  make nl-gen-cluster-failures
  python scripts/cluster_nl_gen_failures.py --strategy always-false
  python scripts/cluster_nl_gen_failures.py --real --pretty
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
from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet, GeneratedEvalCase  # noqa: E402
from ownevo_kernel.nl_gen.failure_clustering import (  # noqa: E402
    NLGenFailureSnapshot,
    analyze_nl_gen_failures,
)
from ownevo_kernel.nl_gen.fixtures import (  # noqa: E402
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
)
from ownevo_kernel.nl_gen.spec import WorkflowSpec  # noqa: E402
from ownevo_kernel.tenant_session import DEFAULT_WORKSPACE_ID, connect_workspace_conn  # noqa: E402

ENV_DB_URL = "OWNEVO_DATABASE_URL"
DEFAULT_WORKFLOW_ID = "nl-gen-failure-clusters"

# ---------------------------------------------------------------------------
# Stub agent strategies — deterministic, no network
# ---------------------------------------------------------------------------


def _strategy_always_false(case: GeneratedEvalCase) -> bool:
    return False


def _strategy_always_true(case: GeneratedEvalCase) -> bool:
    return True


def _strategy_miss_derived(case: GeneratedEvalCase) -> bool:
    """Fails on every `derived` case (verbatim past-miss phrases).

    Predicts the OPPOSITE of expected for derived cases; ground-truth on
    inferred. Surfaces the "the agent ignores user-flagged misses"
    failure mode as one tight cluster.
    """
    if case.provenance.kind == "derived":
        return not case.expected_value
    return case.expected_value


def _strategy_miss_derived_and_train_fps(case: GeneratedEvalCase) -> bool:
    """Default smoke strategy — guarantees ≥3 distinct failure shapes.

    Failure mix:
      * Derived cases (any fold): predicts opposite → false-negatives /
        false-positives on `derived-miss` hint.
      * Train-fold inferred cases with `expected_value=False`: predicts
        True → false-positive + inferred-miss + train-fold cluster.
      * All else: predicts ground-truth (passes).
    """
    if case.provenance.kind == "derived":
        return not case.expected_value
    if (
        case.provenance.kind == "inferred"
        and case.expected_value is False
        and not case.is_test_fold
    ):
        return True
    return case.expected_value


_STRATEGIES = {
    "always-false": _strategy_always_false,
    "always-true": _strategy_always_true,
    "miss-derived": _strategy_miss_derived,
    "miss-derived-and-train-fps": _strategy_miss_derived_and_train_fps,
}


# ---------------------------------------------------------------------------
# Stub Embedder / Clusterer / Labeler — deterministic, no network
# ---------------------------------------------------------------------------


class _HashEmbedder:
    """sha256 of `text_signature` → 384-d float32 normalized vector.

    Identical pattern to `cluster_m5_failures.py`. Stays consistent with
    the M5 smoke story so reviewers can compare end-to-end runs side by
    side without two embedder implementations to reason about.
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


def _build_stub_clusterer(snapshots: list[NLGenFailureSnapshot]) -> Clusterer:
    """Bucket snapshots by (workflow_spec_id, primary failure-direction hint).

    The first hint in `feature_gap_hints` is the direction tag
    (`false-negative` / `false-positive`); pairing it with the workflow
    id gives a natural per-(workflow, direction) cluster.
    """
    keys: list[tuple[str, str]] = []
    for s in snapshots:
        primary_hint = s.feature_gap_hints[0] if s.feature_gap_hints else "no-hint"
        keys.append((s.workflow_spec_id, primary_hint))

    seen: dict[tuple[str, str], int] = {}
    labels: list[int] = []
    for k in keys:
        if k not in seen:
            seen[k] = len(seen)
        labels.append(seen[k])

    arr = np.asarray(labels, dtype=np.int64)
    persistence = {lbl: 0.6 for lbl in seen.values()}

    class _Pluggable:
        def cluster(self, reduced: np.ndarray) -> RawClusterAssignment:
            if reduced.shape[0] != arr.shape[0]:
                raise ValueError(
                    f"clusterer alignment lost: reduced={reduced.shape[0]} "
                    f"but pre-bound labels={arr.shape[0]}",
                )
            return RawClusterAssignment(labels=arr, persistence=persistence)

    return _Pluggable()


class _StubLabeler:
    """Builds a label from the first sample signature's workflow + hint.

    Sample text shape (built by `failure_clustering._text_signature`):
      "<case_id> [<workflow_spec_id>/<label_field>] expected=… actual=…
       provenance=…:… hints=[<hint>,<hint>]"
    """

    def label(self, sample_texts: list[str], cluster_index: int) -> str:
        if not sample_texts:
            return f"cluster-{cluster_index}"
        first = sample_texts[0]
        workflow = "unknown"
        hint = "miss"
        with contextlib.suppress(IndexError):
            workflow = first.split("[")[1].split("/")[0]
        if "hints=[" in first:
            after = first.split("hints=[")[1]
            tag = after.split(",")[0].split("]")[0]
            if tag and tag != "none":
                hint = tag
        return f"{workflow} — {hint}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliArgs:
    workflow_id: str
    no_db: bool
    strategy: str
    real: bool
    require_clusters: int | None
    pretty: bool


def _parse_args(argv: list[str] | None = None) -> CliArgs:
    parser = argparse.ArgumentParser(
        description="Cluster NL-gen failures end-to-end (W5.3).",
    )
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Skip DB writes even if OWNEVO_DATABASE_URL is set.",
    )
    parser.add_argument(
        "--strategy",
        default="miss-derived-and-train-fps",
        choices=sorted(_STRATEGIES),
        help="Stub agent failure pattern (default produces ≥3 clusters).",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help=(
            "Run a real Anthropic agent over each case set instead of the stub "
            "and pair the result with sentence-transformers + UMAP + HDBSCAN + "
            "Anthropic clustering. Requires ANTHROPIC_API_KEY + "
            "`pip install ownevo-kernel[clustering,agent]`."
        ),
    )
    parser.add_argument(
        "--require-clusters",
        type=_positive_int,
        default=None,
        help=(
            "Exit 5 if fewer than N clusters appear (W5.3 spec gate "
            "expects ≥3). Default: don't gate."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Emit indented JSON instead of single-line.",
    )
    ns = parser.parse_args(argv)
    return CliArgs(
        workflow_id=ns.workflow_id,
        no_db=ns.no_db,
        strategy=ns.strategy,
        real=ns.real,
        require_clusters=ns.require_clusters,
        pretty=ns.pretty,
    )


def _positive_int(s: str) -> int:
    try:
        v = int(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected integer, got {s!r}") from exc
    if v <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {v}")
    return v


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def main_async(args: CliArgs) -> int:
    try:
        per_workflow = _load_workflow_bundles()
    except (KeyError, AttributeError) as exc:
        print(f"error: failed to load fixtures: {exc}", file=sys.stderr)
        return 2

    if args.real:
        snapshots = await _collect_failures_real(per_workflow)
    else:
        try:
            stub = _STRATEGIES[args.strategy]
        except KeyError:
            print(f"error: unknown strategy {args.strategy!r}", file=sys.stderr)
            return 3
        snapshots = _collect_failures_stub(per_workflow, stub)

    print(
        f"collected {len(snapshots)} failures across "
        f"{len(per_workflow)} NL-gen workflows",
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

    summary = _result_summary(result)
    summary["per_workflow_failure_counts"] = _per_workflow_counts(snapshots)

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
        except (asyncpg.ConnectionFailureError, OSError) as exc:
            print(f"error: could not connect to DB: {exc}", file=sys.stderr)
            return 4
        summary["persisted_clusters"] = len(persisted)
    elif not db_url and not args.no_db:
        print(
            f"\nnote: {ENV_DB_URL} not set — skipping DB persistence "
            "(clusters not written).",
            file=sys.stderr,
        )

    print(json.dumps(summary, indent=2 if args.pretty else None))

    if args.require_clusters is not None and len(result.clusters) < args.require_clusters:
        print(
            f"error: clusters={len(result.clusters)} below threshold "
            f"{args.require_clusters}",
            file=sys.stderr,
        )
        return 5
    return 0


def _load_workflow_bundles() -> list[tuple[str, WorkflowSpec, EvalCaseSet]]:
    """Return [(workflow_id, spec, case_set)] for the 3 A4.1 fixtures."""
    bundles: list[tuple[str, WorkflowSpec, EvalCaseSet]] = []
    for key, spec in FIXTURES.items():
        case_set = EVAL_CASE_SET_FIXTURES[key]
        bundles.append((key, spec, case_set))
    return bundles


def _collect_failures_stub(
    bundles: list[tuple[str, WorkflowSpec, EvalCaseSet]],
    stub_fn,
) -> list[NLGenFailureSnapshot]:
    """Run the stub strategy across every case in every workflow.

    Snapshot ordering is deterministic: workflows in fixture-key order;
    within a workflow, by `analyze_nl_gen_failures`'s severity-desc /
    case_id-asc rule.
    """
    out: list[NLGenFailureSnapshot] = []
    for _key, spec, case_set in bundles:
        decisions = [(c.case_id, stub_fn(c)) for c in case_set.cases]
        out.extend(
            analyze_nl_gen_failures(case_set, spec, decisions=decisions)
        )
    return out


async def _collect_failures_real(
    bundles: list[tuple[str, WorkflowSpec, EvalCaseSet]],
) -> list[NLGenFailureSnapshot]:
    """Live Anthropic agent path. Costly — guarded behind --real."""
    from anthropic import AsyncAnthropic
    from ownevo_kernel.eval_runner.agent_solver import solve_with_agent
    from ownevo_kernel.nl_gen.fixtures import METRIC_FIXTURES, SIM_PLAN_FIXTURES

    client = AsyncAnthropic()
    out: list[NLGenFailureSnapshot] = []
    for key, spec, case_set in bundles:
        plan = SIM_PLAN_FIXTURES[key]
        metric = METRIC_FIXTURES[key]
        results = await solve_with_agent(
            client,
            case_set=case_set,
            plan=plan,
            spec=spec,
            metric=metric,
        )
        decisions = [(r.case_id, bool(r.actual_value)) for r in results]
        out.extend(
            analyze_nl_gen_failures(case_set, spec, decisions=decisions)
        )
    return out


def _build_stages(
    snapshots: list[NLGenFailureSnapshot],
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
        _build_stub_clusterer(snapshots),
        _StubLabeler(),
    )


def _result_summary(result: ClusteringResult) -> dict[str, object]:
    return {
        "signal": result.signal.value,
        "n_inputs_clustered": result.n_inputs,
        "n_noise": result.n_noise,
        "n_clusters": len(result.clusters),
        "insufficient_data_reason": result.insufficient_data_reason,
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


def _per_workflow_counts(
    snapshots: list[NLGenFailureSnapshot],
) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in snapshots:
        out[s.workflow_spec_id] = out.get(s.workflow_spec_id, 0) + 1
    return out


async def _ensure_workflow_row(conn: object, workflow_id: str) -> None:
    """Idempotent upsert — `cluster_nl_gen_failures` stands alone."""
    await conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, $2, '{}'::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        workflow_id,
        "NL-gen failure clusters (cluster_nl_gen_failures.py upsert)",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
