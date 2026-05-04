"""Sandboxed M5 baseline tests — W2.6 #11c.

Runs the same in-process synthetic fixture from `test_baselines_m5_lightgbm.py`
through `SandboxedM5BenchmarkRunner` + `LocalDockerSandbox`. Pins the
reproducibility bar:

  1. The sandboxed run produces a finite, in-range val_score.
  2. Sandboxed predictions are bit-identical to the in-process baseline
     under matched library versions (same numpy/pandas/lightgbm pins).
  3. Two sandboxed runs produce bit-identical predictions (W2.6 exit
     criterion: deterministic across runs).

Skipped automatically when Docker isn't reachable or when the M5
sandbox image (`ownevo-sandbox-m5:0.1.0`) hasn't been built. CI gets
the image via `make sandbox-image-m5` (PR #11d wires the cache strategy);
local dev runs need to build it once.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

# `apps/kernel/baselines/` lives outside `src/`. Match the same
# sys.path bridge as test_baselines_m5_lightgbm.py.
_REPO_KERNEL = Path(__file__).resolve().parents[1]
if str(_REPO_KERNEL) not in sys.path:
    sys.path.insert(0, str(_REPO_KERNEL))

from baselines.m5_lightgbm import run_baseline  # noqa: E402
from ownevo_kernel.benchmark import (  # noqa: E402
    M5BenchmarkRunner,
    SandboxedM5BenchmarkRunner,
)
from ownevo_kernel.datasets import load_m5, make_held_out_fold  # noqa: E402
from ownevo_kernel.sandbox import LocalDockerSandbox, docker_available  # noqa: E402

M5_SANDBOX_IMAGE = "ownevo-sandbox-m5:0.1.0"


def _docker_ok() -> bool:
    return asyncio.run(docker_available())


def _image_present(tag: str) -> bool:
    """`docker image inspect` exits 0 when the image is local; non-0 if not.
    Skip rather than fail when missing — a developer who hasn't run
    `make sandbox-image-m5` shouldn't see a red CI."""
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", tag],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


pytestmark = pytest.mark.skipif(
    not _docker_ok() or not _image_present(M5_SANDBOX_IMAGE),
    reason=(
        "Sandboxed M5 tests require Docker + the sandbox image; "
        f"build it with `make sandbox-image-m5` (tag {M5_SANDBOX_IMAGE})."
    ),
)


# ---------------------------------------------------------------------------
# Synthetic fixture — same shape as test_baselines_m5_lightgbm.py
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
    # Sandbox container drops CAP_DAC_OVERRIDE — its uid 0 cannot read
    # files unless DAC permits. tmp_path defaults to 0700 on most systems.
    import os
    import stat
    os.chmod(tmp_path, 0o755)
    for f in tmp_path.iterdir():
        os.chmod(f, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    return tmp_path


@pytest.fixture
def sandbox() -> LocalDockerSandbox:
    return LocalDockerSandbox(
        image=M5_SANDBOX_IMAGE,
        # LightGBM intermediates + pandas DataFrames need more than the
        # 64MB default. 256MB is comfortable for the 5-series fixture
        # without bloating per-test runtime.
        tmpfs_size_mb=256,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_sandboxed_runner_produces_finite_val_score(
    m5_dir: Path,
    sandbox: LocalDockerSandbox,
):
    """End-to-end: load → fold → sandbox-run → score. Mirrors the
    in-process `test_baseline_produces_finite_score_through_runner`."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)
    runner = SandboxedM5BenchmarkRunner(
        catalog_dir=m5_dir,
        fold=fold,
        sandbox=sandbox,
    )
    result = await runner.run()

    assert 0.0 < result.val_score <= 1.0, f"val_score out of range: {result.val_score}"
    arts = runner.last_artifacts
    assert arts is not None
    assert np.isfinite(arts.rmse)
    assert np.isfinite(arts.wrmsse)
    n = len(arts.series_ids)
    assert n == 5
    assert arts.predictions.shape == (n, 28)


async def test_sandboxed_runner_is_deterministic(
    m5_dir: Path,
    sandbox: LocalDockerSandbox,
):
    """W2.6 exit criterion: bit-identical predictions across two
    sandboxed runs. This is the load-bearing test for the sandbox flip
    — if it ever flakes, the determinism story is broken."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    runner_a = SandboxedM5BenchmarkRunner(
        catalog_dir=m5_dir, fold=fold, sandbox=sandbox,
    )
    a = await runner_a.run()

    runner_b = SandboxedM5BenchmarkRunner(
        catalog_dir=m5_dir, fold=fold, sandbox=sandbox,
    )
    b = await runner_b.run()

    assert a.rewards == b.rewards
    assert runner_a.last_artifacts is not None
    assert runner_b.last_artifacts is not None
    np.testing.assert_array_equal(
        runner_a.last_artifacts.predictions,
        runner_b.last_artifacts.predictions,
    )


async def test_sandboxed_matches_in_process_predictions(
    m5_dir: Path,
    sandbox: LocalDockerSandbox,
):
    """Parity check: same inputs, same skill bodies, matched library
    versions → bit-identical predictions vs the in-process path. If
    this breaks, the sandbox image's pinned versions have drifted from
    `uv.lock`."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)

    in_proc = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=run_baseline)
    in_proc_result = await in_proc.run()
    in_proc_arts = in_proc.last_artifacts
    assert in_proc_arts is not None

    sandboxed = SandboxedM5BenchmarkRunner(
        catalog_dir=m5_dir, fold=fold, sandbox=sandbox,
    )
    sandboxed_result = await sandboxed.run()
    sandboxed_arts = sandboxed.last_artifacts
    assert sandboxed_arts is not None

    np.testing.assert_array_equal(
        sandboxed_arts.predictions,
        in_proc_arts.predictions,
    )
    np.testing.assert_array_equal(
        sandboxed_arts.actuals,
        in_proc_arts.actuals,
    )
    assert tuple(sandboxed_arts.series_ids) == tuple(in_proc_arts.series_ids)
    assert sandboxed_result.rewards == in_proc_result.rewards


async def test_sandboxed_runner_supports_subset(
    m5_dir: Path,
    sandbox: LocalDockerSandbox,
):
    """Passing `task_ids` scopes the sandboxed pipeline to a subset of
    series — required for the gate's regression-suite step (re-score
    specific tasks)."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)
    runner = SandboxedM5BenchmarkRunner(
        catalog_dir=m5_dir, fold=fold, sandbox=sandbox,
    )
    result = await runner.run(task_ids=["FOODS_002_CA_1_validation"])
    assert set(result.rewards.keys()) == {"FOODS_002_CA_1_validation"}
    arts = runner.last_artifacts
    assert arts is not None
    assert arts.predictions.shape == (1, 28)
