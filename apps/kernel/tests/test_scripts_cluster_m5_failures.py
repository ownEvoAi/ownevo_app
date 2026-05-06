"""Smoke tests for `scripts/cluster_m5_failures.py` internals.

We don't run the full CLI (it re-runs the LightGBM baseline; out of
scope for unit tests). Instead we exercise the stub stages, the result-
summary helper, and the arg parser.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from ownevo_kernel.benchmark import M5FailureSnapshot, analyze_m5_failures  # noqa: E402
from ownevo_kernel.benchmark.m5 import M5RunArtifacts  # noqa: E402
from ownevo_kernel.clustering import (  # noqa: E402
    EMBEDDING_DIM,
    ClusteringResult,
    ClusteringSignal,
    QualityThresholds,
    cluster_failures,
)
from scripts.cluster_m5_failures import (  # noqa: E402
    _build_stages,
    _HashEmbedder,
    _parse_args,
    _result_summary,
    _StubLabeler,
    build_stub_clusterer,
)


def _snap(
    series_id: str,
    *,
    rmsse: float = 1.0,
    cat_id: str | None = None,
    hints: tuple[str, ...] = ("under-forecast",),
) -> M5FailureSnapshot:
    cat = cat_id if cat_id is not None else series_id.split("_")[0]
    return M5FailureSnapshot(
        series_id=series_id,
        item_id=f"{cat}_1_001",
        dept_id=f"{cat}_1",
        cat_id=cat,
        store_id="CA_1",
        state_id="CA",
        rmsse=rmsse,
        reward=0.5,
        mean_actual=2.0,
        mean_predicted=1.5,
        peak_error_day_offset=3,
        peak_error_day_label="d_1900",
        peak_error_value=-2.0,
        feature_gap_hints=hints,
        text_signature=(
            f"{series_id} [{cat}/{cat}_1 @ CA/CA_1] rmsse=1.00 peak +1.00 day 0 "
            f"hints=[{','.join(hints) if hints else 'none'}]"
        ),
    )


# ---------------------------------------------------------------------------
# Stub stages
# ---------------------------------------------------------------------------


def test_hash_embedder_is_deterministic_and_correct_shape() -> None:
    emb = _HashEmbedder()
    a = emb.embed(["foo", "bar"])
    b = emb.embed(["foo", "bar"])
    assert a.shape == (2, EMBEDDING_DIM)
    assert a.dtype == np.float32
    np.testing.assert_array_equal(a, b)
    # Different texts → different embeddings.
    assert not np.allclose(a[0], a[1])


def test_stub_clusterer_buckets_by_cat_and_hint() -> None:
    snaps = [
        _snap("HOBBIES_1_001_CA_1_validation", cat_id="HOBBIES", hints=("under-forecast",)),
        _snap("HOBBIES_1_002_CA_1_validation", cat_id="HOBBIES", hints=("under-forecast",)),
        _snap("HOBBIES_1_003_CA_1_validation", cat_id="HOBBIES", hints=("over-forecast",)),
        _snap("FOODS_1_001_CA_1_validation", cat_id="FOODS", hints=("under-forecast",)),
        _snap("FOODS_1_002_CA_1_validation", cat_id="FOODS", hints=("under-forecast",)),
    ]
    clusterer = build_stub_clusterer(snaps)
    # Reduced shape only matters for length; values irrelevant.
    reduced = np.zeros((5, 8), dtype=np.float32)
    out = clusterer.cluster(reduced)
    # Three buckets: (HOBBIES,under), (HOBBIES,over), (FOODS,under).
    assert sorted(set(out.labels.tolist())) == [0, 1, 2]
    assert out.labels.tolist() == [0, 0, 1, 2, 2]


def test_stub_clusterer_alignment_loss_raises() -> None:
    snaps = [_snap(f"HOBBIES_1_{i:03d}_CA_1_validation") for i in range(3)]
    clusterer = build_stub_clusterer(snaps)
    with pytest.raises(ValueError, match="alignment lost"):
        clusterer.cluster(np.zeros((2, 8), dtype=np.float32))


def test_stub_labeler_extracts_cat_and_hint() -> None:
    sample = (
        "HOBBIES_1_001_CA_1_validation [HOBBIES/HOBBIES_1 @ CA/CA_1] "
        "rmsse=1.50 peak +2.00 day 4 hints=[under-forecast,zero-inflated]"
    )
    labeler = _StubLabeler()
    label = labeler.label([sample], cluster_index=0)
    assert label == "HOBBIES — under-forecast"


def test_stub_labeler_falls_back_when_text_unparseable() -> None:
    labeler = _StubLabeler()
    assert labeler.label(["nothing parseable here"], cluster_index=2) == "unknown — drift"
    assert labeler.label([], cluster_index=2) == "cluster-2"


# ---------------------------------------------------------------------------
# End-to-end pipeline run with stubs (no DB, no LLM, no model download)
# ---------------------------------------------------------------------------


def test_pipeline_with_stubs_produces_expected_clusters() -> None:
    under = ("under-forecast",)
    over = ("over-forecast",)
    zinf = ("zero-inflated",)
    snaps = [
        _snap("HOBBIES_1_001_CA_1_validation", cat_id="HOBBIES", hints=under, rmsse=1.5),
        _snap("HOBBIES_1_002_CA_1_validation", cat_id="HOBBIES", hints=under, rmsse=1.4),
        _snap("HOBBIES_1_003_CA_1_validation", cat_id="HOBBIES", hints=under, rmsse=1.3),
        _snap("FOODS_1_001_CA_1_validation", cat_id="FOODS", hints=over, rmsse=1.2),
        _snap("FOODS_1_002_CA_1_validation", cat_id="FOODS", hints=over, rmsse=1.1),
        _snap("HOUSEHOLD_1_001_CA_1_validation", cat_id="HOUSEHOLD", hints=zinf, rmsse=1.0),
        _snap("HOUSEHOLD_1_002_CA_1_validation", cat_id="HOUSEHOLD", hints=zinf, rmsse=0.9),
    ]
    embedder, reducer, clusterer, labeler = _build_stages(snaps, real=False)
    out = cluster_failures(
        snaps,
        embedder=embedder,
        reducer=reducer,
        clusterer=clusterer,
        labeler=labeler,
        thresholds=QualityThresholds(),
    )
    assert out.signal is ClusteringSignal.OK
    # 3 buckets: HOBBIES/under, FOODS/over, HOUSEHOLD/zero-inflated.
    assert len(out.clusters) == 3
    labels = sorted(c.label for c in out.clusters)
    assert labels == [
        "FOODS — over-forecast",
        "HOBBIES — under-forecast",
        "HOUSEHOLD — zero-inflated",
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_result_summary_shape() -> None:
    snaps = [
        _snap(f"HOBBIES_1_{i:03d}_CA_1_validation", cat_id="HOBBIES")
        for i in range(6)
    ]
    embedder, reducer, clusterer, labeler = _build_stages(snaps, real=False)
    out = cluster_failures(
        snaps,
        embedder=embedder,
        reducer=reducer,
        clusterer=clusterer,
        labeler=labeler,
    )
    summary = _result_summary(out, top_k=10)
    # JSON-serializable
    json.dumps(summary)
    assert summary["signal"] == "ok"
    assert summary["n_inputs_clustered"] == 6
    assert summary["top_k"] == 10
    assert summary["n_clusters"] == len(out.clusters)
    assert all("samples" in c for c in summary["clusters"])


def test_arg_parser_defaults_and_overrides() -> None:
    args = _parse_args(["--no-db"])
    assert args.no_db is True
    assert args.real is False
    assert args.top_k == 50
    assert args.workflow_id == "m5-demand-prediction"

    args = _parse_args([
        "--top-k", "20",
        "--max-cases-per-cluster", "3",
        "--min-reward-floor", "0.4",
        "--workflow-id", "demo-1",
        "--real",
        "--pretty",
    ])
    assert args.top_k == 20
    assert args.max_cases_per_cluster == 3
    assert args.min_reward_floor == pytest.approx(0.4)
    assert args.workflow_id == "demo-1"
    assert args.real is True
    assert args.pretty is True


# ---------------------------------------------------------------------------
# Analyzer → clustering chain (substitute a synthetic M5RunArtifacts so we
# don't need the real M5 dataset; proves the analyzer's text_signature
# format flows cleanly into the stub stages).
# ---------------------------------------------------------------------------


def test_analyzer_to_pipeline_chain_with_synthetic_artifacts() -> None:
    series_specs = [
        ("HOBBIES_1_001_CA_1_validation",),
        ("HOBBIES_1_002_CA_1_validation",),
        ("FOODS_3_500_TX_2_validation",),
        ("FOODS_3_501_TX_2_validation",),
        ("HOUSEHOLD_2_300_WI_1_validation",),
        ("HOUSEHOLD_2_301_WI_1_validation",),
    ]
    actuals = np.full((len(series_specs), 4), 5.0)
    # Different bias patterns by category so hints diverge.
    preds = np.array([
        [1.0, 1.0, 1.0, 1.0],   # HOBBIES under
        [1.0, 1.0, 1.0, 1.0],
        [9.0, 9.0, 9.0, 9.0],   # FOODS over
        [9.0, 9.0, 9.0, 9.0],
        [0.0, 0.0, 0.0, 0.0],   # HOUSEHOLD severe under (treated as under-forecast)
        [0.0, 0.0, 0.0, 0.0],
    ])
    series_ids = tuple(s[0] for s in series_specs)
    diff = preds - actuals
    scales = np.full(len(series_specs), 1.0)
    rmsse = np.sqrt(np.mean(diff * diff, axis=1) / (scales * scales))
    rewards = {sid: float(np.exp(-r)) for sid, r in zip(series_ids, rmsse, strict=True)}
    arts = M5RunArtifacts(
        predictions=preds,
        actuals=actuals,
        series_ids=series_ids,
        weights=np.ones(len(series_specs)),
        scales=scales,
        rmse=float(np.sqrt(np.mean(diff * diff))),
        wrmsse=0.0,
        rewards=rewards,
    )
    snaps = analyze_m5_failures(arts, k=10)
    assert len(snaps) == 6

    embedder, reducer, clusterer, labeler = _build_stages(snaps, real=False)
    result = cluster_failures(
        snaps,
        embedder=embedder,
        reducer=reducer,
        clusterer=clusterer,
        labeler=labeler,
    )
    assert isinstance(result, ClusteringResult)
    assert result.signal is ClusteringSignal.OK
    # 3 (cat, hint) buckets — the by-category bias produces 3 clusters
    # of size 2 each.
    assert len(result.clusters) == 3
    sizes = sorted(len(c.member_indices) for c in result.clusters)
    assert sizes == [2, 2, 2]
