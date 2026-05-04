"""M5BenchmarkRunner tests — synthetic pipeline_fn, no pandas/lightgbm needed.

These tests exercise the runner contract: Protocol conformance, reward
formula, artifact recording, scope filtering. The actual baseline pipeline
lives in `apps/kernel/baselines/m5_lightgbm/` and is tested separately
where its dependencies (pandas) are present.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from ownevo_kernel.benchmark import (
    BenchmarkResult,
    BenchmarkRunner,
    M5BenchmarkRunner,
    M5PipelineOutput,
    SandboxedM5BenchmarkRunner,
)
from ownevo_kernel.sandbox import LocalDockerSandbox
from ownevo_kernel.datasets import (
    M5Fold,
    load_m5,
    make_held_out_fold,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_SALES_HEADER = (
    "id,item_id,dept_id,cat_id,store_id,state_id,"
    + ",".join(f"d_{i}" for i in range(1, 101))
)
_SALES_ROWS = "\n".join(
    f"FOODS_{i:03d}_CA_1_validation,FOODS_{i:03d},FOODS_1,FOODS,CA_1,CA,"
    + ",".join(["1"] * 100)
    for i in range(1, 4)
)
_PRICES = "store_id,item_id,wm_yr_wk,sell_price\nCA_1,FOODS_001,11101,2.50\n"
_CALENDAR = "date,wm_yr_wk,d\n2011-01-29,11101,d_1\n"
_SAMPLE_SUBMISSION = "id,F1\nFOODS_001_CA_1_validation,0\n"


@pytest.fixture
def m5_dir(tmp_path: Path) -> Path:
    (tmp_path / "sales_train_validation.csv").write_text(
        _SALES_HEADER + "\n" + _SALES_ROWS + "\n",
    )
    (tmp_path / "sell_prices.csv").write_text(_PRICES)
    (tmp_path / "calendar.csv").write_text(_CALENDAR)
    (tmp_path / "sample_submission.csv").write_text(_SAMPLE_SUBMISSION)
    return tmp_path


def _make_output(
    *,
    n_series: int = 3,
    n_test_days: int = 28,
    error_per_cell: float = 0.0,
    scales: np.ndarray | None = None,
) -> M5PipelineOutput:
    rng = np.random.default_rng(0)
    actuals = rng.uniform(0, 5, size=(n_series, n_test_days))
    predictions = actuals + error_per_cell
    series_ids = [f"FOODS_{i:03d}_CA_1_validation" for i in range(n_series)]
    weights = np.full(n_series, 1.0 / n_series)
    if scales is None:
        scales = np.ones(n_series)
    return M5PipelineOutput(
        predictions=predictions,
        actuals=actuals,
        series_ids=series_ids,
        weights=weights,
        scales=scales,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_satisfies_benchmark_runner_protocol(m5_dir: Path):
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    def stub(c, f, ids):
        return _make_output()

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    assert isinstance(runner, BenchmarkRunner)
    assert isinstance(await runner.run(), BenchmarkResult)


# ---------------------------------------------------------------------------
# Reward formula
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perfect_predictions_score_full_credit(m5_dir: Path):
    """Reward = exp(-rmsse) → exp(0) = 1.0 when predictions match actuals."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    def stub(c, f, ids):
        return _make_output(error_per_cell=0.0)

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    result = await runner.run()
    for series_id, reward in result.rewards.items():
        assert reward == pytest.approx(1.0), f"{series_id} should score 1.0"
    assert result.val_score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_constant_error_yields_known_reward(m5_dir: Path):
    """error=1 every cell, scale=1 → rmsse=1 → reward=exp(-1)≈0.368."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    def stub(c, f, ids):
        return _make_output(error_per_cell=1.0, scales=np.ones(3))

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    result = await runner.run()
    expected = math.exp(-1.0)
    for reward in result.rewards.values():
        assert reward == pytest.approx(expected)


@pytest.mark.asyncio
async def test_higher_scale_reduces_rmsse_so_raises_reward(m5_dir: Path):
    """Same absolute error, larger training-volatility scale → smaller
    RMSSE → larger reward. Sanity-check the per-series normalization."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    def stub(c, f, ids):
        return _make_output(
            error_per_cell=1.0,
            scales=np.array([1.0, 2.0, 4.0]),
        )

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    result = await runner.run()
    rewards = list(result.rewards.values())
    assert rewards[0] < rewards[1] < rewards[2]


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_artifacts_populated_after_run(m5_dir: Path):
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    def stub(c, f, ids):
        return _make_output(error_per_cell=0.5, scales=np.ones(3))

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    assert runner.last_artifacts is None

    await runner.run()
    arts = runner.last_artifacts
    assert arts is not None
    assert arts.predictions.shape == (3, 28)
    assert arts.actuals.shape == (3, 28)
    assert len(arts.series_ids) == 3
    # Aggregate WRMSSE: per-series rmsse = 0.5; uniform weights → 0.5.
    assert arts.wrmsse == pytest.approx(0.5)
    assert arts.rmse == pytest.approx(0.5)
    # rewards mirrors what BenchmarkResult exposed.
    assert set(arts.rewards) == set(arts.series_ids)


@pytest.mark.asyncio
async def test_run_is_idempotent_for_deterministic_pipeline(m5_dir: Path):
    """Two runs with the same pipeline → same val_score (no hidden state)."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    def stub(c, f, ids):
        return _make_output(error_per_cell=0.7, scales=np.ones(3))

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    a = await runner.run()
    b = await runner.run()
    assert a.val_score == pytest.approx(b.val_score)
    assert a.rewards == b.rewards


# ---------------------------------------------------------------------------
# Scope filtering — task_ids passes through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_ids_subset_passed_to_pipeline(m5_dir: Path):
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)
    seen: list[list[str] | None] = []

    def stub(c, f, ids):
        seen.append(ids)
        n = len(ids) if ids else 3
        return _make_output(n_series=n)

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    await runner.run(task_ids=["FOODS_000_CA_1_validation"])
    await runner.run(task_ids=None)
    assert seen == [["FOODS_000_CA_1_validation"], None]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_rejects_zero_scale(m5_dir: Path):
    """Per-series rmsse divides by scale; the runner must catch zero-scale
    series rather than emitting a NaN/+inf reward."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    def stub(c, f, ids):
        return _make_output(scales=np.array([1.0, 0.0, 1.0]))

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    with pytest.raises(ValueError, match="scales <= 0"):
        await runner.run()


@pytest.mark.asyncio
async def test_runner_rejects_shape_mismatch(m5_dir: Path):
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    def stub(c, f, ids):
        out = _make_output()
        # Predict more days than we have actuals for.
        return M5PipelineOutput(
            predictions=np.zeros((3, 30)),
            actuals=out.actuals,
            series_ids=out.series_ids,
            weights=out.weights,
            scales=out.scales,
        )

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    with pytest.raises(ValueError, match="shape"):
        await runner.run()


@pytest.mark.asyncio
async def test_runner_rejects_series_id_count_mismatch(m5_dir: Path):
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    def stub(c, f, ids):
        out = _make_output()
        return M5PipelineOutput(
            predictions=out.predictions,
            actuals=out.actuals,
            series_ids=out.series_ids[:2],   # one short
            weights=out.weights,
            scales=out.scales,
        )

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=stub)
    with pytest.raises(ValueError, match="series_ids"):
        await runner.run()


# ---------------------------------------------------------------------------
# Fold sanity (regression — runner must accept the M5Fold shape unchanged)
# ---------------------------------------------------------------------------


def test_fold_shape_unchanged():
    """If `M5Fold` ever grows fields the runner depends on, this surfaces it."""
    fields = ("train", "validation", "test")
    fold = M5Fold(train=("d_1",), validation=("d_2",), test=("d_3",))
    for f in fields:
        assert hasattr(fold, f)


# ---------------------------------------------------------------------------
# B4.1: SandboxedM5BenchmarkRunner.skill_override_dir validation (no Docker)
# ---------------------------------------------------------------------------

_FAKE_FOLD = M5Fold(train=("d_1",), validation=("d_2",), test=("d_3",))


def _fake_sandbox(tmp_path: Path) -> LocalDockerSandbox:
    return LocalDockerSandbox(image="ownevo-sandbox-m5:test")


def test_skill_override_dir_nonexistent_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="skill_override_dir"):
        SandboxedM5BenchmarkRunner(
            catalog_dir=tmp_path,
            fold=_FAKE_FOLD,
            sandbox=_fake_sandbox(tmp_path),
            skill_override_dir=tmp_path / "does_not_exist",
        )


def test_skill_override_dir_valid_path_resolves(tmp_path: Path) -> None:
    override = tmp_path / "override"
    override.mkdir()
    runner = SandboxedM5BenchmarkRunner(
        catalog_dir=tmp_path,
        fold=_FAKE_FOLD,
        sandbox=_fake_sandbox(tmp_path),
        skill_override_dir=override,
    )
    assert runner.skill_override_dir == override.resolve()


def test_skill_override_dir_none_leaves_field_none(tmp_path: Path) -> None:
    runner = SandboxedM5BenchmarkRunner(
        catalog_dir=tmp_path,
        fold=_FAKE_FOLD,
        sandbox=_fake_sandbox(tmp_path),
    )
    assert runner.skill_override_dir is None
