"""End-to-end orchestrator test for the M5 v1 baseline.

Pure-numpy seasonal-naive bodies — no pandas, no lightgbm, no Docker. The
test exists to prove the orchestrator wiring (data_loader → ... →
ensemble) is correct, that registered skills parse, and that a re-run
yields bit-identical predictions (the W2.6 reproducibility bar).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# `apps/kernel/baselines/` lives outside `src/`. Make it importable for the
# test process so `from baselines.m5_lightgbm import run_baseline` resolves
# the same way `scripts/m5_baseline.py` will at runtime.
_REPO_KERNEL = Path(__file__).resolve().parents[1]
if str(_REPO_KERNEL) not in sys.path:
    sys.path.insert(0, str(_REPO_KERNEL))

from baselines.m5_lightgbm import (  # noqa: E402
    SKILL_FILES,
    RawSeriesData,
    run_baseline,
    skill_files_dir,
)
from ownevo_kernel.benchmark import M5BenchmarkRunner, M5PipelineOutput  # noqa: E402
from ownevo_kernel.datasets import load_m5, make_held_out_fold  # noqa: E402
from ownevo_kernel.skills.format import parse_skill  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture — synthetic M5 with enough variance for a non-trivial baseline
# ---------------------------------------------------------------------------


def _build_synthetic_m5(root: Path, *, n_series: int = 5, n_days: int = 100) -> None:
    rng = np.random.default_rng(seed=42)
    sales = rng.integers(0, 10, size=(n_series, n_days)).astype(int)

    header = (
        "id,item_id,dept_id,cat_id,store_id,state_id,"
        + ",".join(f"d_{i}" for i in range(1, n_days + 1))
    )
    rows = [
        f"FOODS_{i:03d}_CA_1_validation,FOODS_{i:03d},FOODS_1,FOODS,CA_1,CA,"
        + ",".join(str(v) for v in sales[i])
        for i in range(n_series)
    ]
    (root / "sales_train_validation.csv").write_text(
        header + "\n" + "\n".join(rows) + "\n",
    )
    (root / "sell_prices.csv").write_text(
        "store_id,item_id,wm_yr_wk,sell_price\nCA_1,FOODS_000,11101,2.50\n",
    )
    (root / "calendar.csv").write_text("date,wm_yr_wk,d\n2011-01-29,11101,d_1\n")
    (root / "sample_submission.csv").write_text(
        "id,F1\nFOODS_000_CA_1_validation,0\n",
    )


@pytest.fixture
def m5_dir(tmp_path: Path) -> Path:
    _build_synthetic_m5(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Skill source — parses cleanly, registry-ready
# ---------------------------------------------------------------------------


def test_skill_files_present_and_parseable():
    """Every file in `SKILL_FILES` exists, parses, and declares its id."""
    skill_dir = skill_files_dir()
    seen_ids: set[str] = set()
    for fname in SKILL_FILES:
        path = skill_dir / fname
        assert path.is_file(), f"missing skill source: {path}"
        rec = parse_skill(path.read_text())
        assert rec.frontmatter.kind == "python"
        assert rec.frontmatter.id.startswith("m5.baseline.v1.")
        assert rec.frontmatter.retention.stateless is True
        seen_ids.add(rec.frontmatter.id)
    # Six distinct skills.
    assert len(seen_ids) == len(SKILL_FILES)


# ---------------------------------------------------------------------------
# Orchestrator end-to-end
# ---------------------------------------------------------------------------


def test_run_baseline_returns_well_shaped_output(m5_dir: Path):
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)
    out = run_baseline(catalog, fold)

    assert isinstance(out, M5PipelineOutput)
    n = len(out.series_ids)
    assert n == 5
    assert out.predictions.shape == (n, 28)
    assert out.actuals.shape == (n, 28)
    assert out.weights.shape == (n,)
    assert out.scales.shape == (n,)
    assert np.all(out.scales > 0)
    # Uniform weights (v1 — no dollar volume yet).
    assert np.allclose(out.weights, 1.0 / n)


def test_run_baseline_is_deterministic(m5_dir: Path):
    """W2.6 exit criterion: RMSE reproducible across two runs."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)
    a = run_baseline(catalog, fold)
    b = run_baseline(catalog, fold)
    np.testing.assert_array_equal(a.predictions, b.predictions)


@pytest.mark.asyncio
async def test_baseline_produces_finite_score_through_runner(m5_dir: Path):
    """Wire the orchestrator into M5BenchmarkRunner and check the loop."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)
    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=run_baseline)

    result = await runner.run()
    assert 0.0 < result.val_score <= 1.0
    arts = runner.last_artifacts
    assert arts is not None
    # Aggregate metrics finite + reproducible across two runs.
    assert np.isfinite(arts.rmse)
    assert np.isfinite(arts.wrmsse)

    second = await runner.run()
    assert result.rewards == second.rewards
    assert runner.last_artifacts is not None
    assert runner.last_artifacts.rmse == pytest.approx(arts.rmse)


def test_subset_scope_passes_through(m5_dir: Path):
    """Requesting a single series produces a 1-row output."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)
    out = run_baseline(catalog, fold, series_ids=["FOODS_002_CA_1_validation"])
    assert out.series_ids == ["FOODS_002_CA_1_validation"]
    assert out.predictions.shape == (1, 28)


# ---------------------------------------------------------------------------
# Stage contract — outlier_handler drops zero-movement series
# ---------------------------------------------------------------------------


def test_outlier_handler_drops_zero_movement_series():
    """Constant series get filtered so WRMSSE doesn't divide by zero.

    Metadata + DOW arrays are filtered in lockstep — feature engineering
    downstream relies on the kept-mask staying consistent across all
    per-series fields.
    """
    from baselines.m5_lightgbm.skill_v1 import outlier_handler

    raw = RawSeriesData(
        series_ids=["a", "b", "c"],
        train_actuals=np.array([
            [1.0, 1.0, 1.0, 1.0],   # zero movement → drop
            [0.0, 1.0, 0.0, 1.0],   # has movement → keep
            [2.0, 2.0, 2.0, 2.0],   # zero movement → drop
        ]),
        validation_actuals=np.zeros((3, 2)),
        test_actuals=np.zeros((3, 2)),
        dollar_volume=None,
        metadata=[
            {"cat_id": "FOODS"},
            {"cat_id": "HOBBIES"},
            {"cat_id": "HOUSEHOLD"},
        ],
        val_dow=np.array([0, 1]),
        test_dow=np.array([2, 3]),
    )
    cleaned = outlier_handler.handle(raw)
    assert cleaned.series_ids == ["b"]
    assert cleaned.train_actuals.shape == (1, 4)
    assert cleaned.metadata == [{"cat_id": "HOBBIES"}]
    np.testing.assert_array_equal(cleaned.val_dow, raw.val_dow)
    np.testing.assert_array_equal(cleaned.test_dow, raw.test_dow)


def test_outlier_handler_preserves_sparse_demand_series():
    """Sparse-demand series (≤1% non-zero days) used to be silently
    destroyed by the unconditional 99th-percentile clip: with cap=0,
    `np.minimum(row, 0)` zeroed the entire row, and the next step
    (`_compute_weights_and_scales`) raised because scale=0.

    The fix: skip the clip when its cap is non-positive. Sparse series
    are then preserved with their raw values intact, and their scale
    stays > 0 (because `np.diff` on `[0, …, 0, spike, 0, …, 0]` is not
    all zero). Dropping these series would lose model-able signal that
    the raw data carries.
    """
    from baselines.m5_lightgbm.skill_v1 import outlier_handler

    rng = np.random.default_rng(seed=42)
    dense = rng.integers(0, 10, size=200).astype(np.float64)
    sparse = np.zeros(200, dtype=np.float64)
    sparse[100] = 5.0  # one isolated spike — pre-fix, gets clipped to all 0

    raw = RawSeriesData(
        series_ids=["dense", "sparse"],
        train_actuals=np.stack([dense, sparse]),
        validation_actuals=np.zeros((2, 28)),
        test_actuals=np.zeros((2, 28)),
        dollar_volume=None,
        metadata=[{"item_id": "dense"}, {"item_id": "sparse"}],
        val_dow=np.zeros(28, dtype=np.int64),
        test_dow=np.zeros(28, dtype=np.int64),
    )

    cleaned = outlier_handler.handle(raw)
    assert cleaned.series_ids == ["dense", "sparse"], (
        f"both series should be preserved; got {cleaned.series_ids}"
    )
    # The sparse row's spike is intact (not clipped to zero).
    assert cleaned.train_actuals[1, 100] == 5.0
    # Every kept series satisfies the WRMSSE-scale > 0 contract that
    # _compute_weights_and_scales checks immediately downstream.
    diffs = np.diff(cleaned.train_actuals, axis=1)
    scales = np.sqrt(np.mean(diffs * diffs, axis=1))
    assert np.all(scales > 0), f"all kept series must have scale > 0; got {scales}"


def test_outlier_handler_real_m5_shape_runs_without_zero_scale():
    """Pre-fix regression: on real M5 (30,490 series × 1,857 train days),
    a non-trivial fraction of series are intermittent-demand and trip the
    'percentile=0 → clip→0 → scale=0' chain. The kept output must always
    satisfy scale > 0; the runner downstream depends on this invariant.

    This synthesises the relevant real-M5 shape — many series, few of
    which are sparse — without requiring the actual CSVs at test time.
    """
    from baselines.m5_lightgbm.skill_v1 import outlier_handler

    rng = np.random.default_rng(seed=7)
    n_series, n_days = 50, 1857  # real M5 train length
    train = rng.integers(0, 5, size=(n_series, n_days)).astype(np.float64)
    # Replace 5 of the rows with deliberately-sparse demand patterns
    for i in range(5):
        train[i] = 0.0
        # Spike at a single random day — << 1% non-zero
        train[i, rng.integers(0, n_days)] = float(rng.integers(1, 10))

    raw = RawSeriesData(
        series_ids=[f"s{i:03d}" for i in range(n_series)],
        train_actuals=train,
        validation_actuals=np.zeros((n_series, 28)),
        test_actuals=np.zeros((n_series, 28)),
        dollar_volume=None,
        metadata=[{"item_id": f"s{i:03d}"} for i in range(n_series)],
        val_dow=np.zeros(28, dtype=np.int64),
        test_dow=np.zeros(28, dtype=np.int64),
    )

    cleaned = outlier_handler.handle(raw)
    diffs = np.diff(cleaned.train_actuals, axis=1)
    scales = np.sqrt(np.mean(diffs * diffs, axis=1))
    assert np.all(scales > 0), (
        f"outlier_handler invariant broken: {int((scales <= 0).sum())} "
        f"output series have scale <= 0"
    )
