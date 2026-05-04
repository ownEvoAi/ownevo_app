"""M5BenchmarkRunner — drives a forecasting pipeline over the held-out fold (W2.6).

Implements the `BenchmarkRunner` Protocol (`benchmark/types.py`) over M5.
The pipeline itself stays out of the kernel (pandas / lightgbm / sklearn live
in `apps/kernel/baselines/m5_lightgbm/`); the runner takes the pipeline as an
injected callable so kernel imports remain pandas-free per CLAUDE.md.

Per-task reward formula
-----------------------
Each M5 series is one task in the `BenchmarkResult`. Per-series reward maps
RMSSE — the M5 paper's per-series scaled error — into [0, 1] via:

    reward_i = exp(-rmsse_i)

`exp(-x)` is monotonically decreasing in error and bounded in (0, 1]:
perfect prediction (rmsse=0) → 1.0; rmsse=1 → ~0.37; rmsse=2 → ~0.14.
Mean across series gives a single scalar `val_score` the gate can compare
iteration-to-iteration. WRMSSE/RMSE are the human-facing summary numbers
printed to stdout; `iterations.val_score` stores the gate metric
`mean(exp(-RMSSE_i))` (in (0, 1]), not WRMSSE-scale values.

Series with `scale_i == 0` (intermittent items with zero training movement)
are filtered before scoring — `wrmsse()` rejects them with ValueError, and
RMSSE is undefined when the denominator is zero. The runner records how
many were dropped so a regression that suddenly drops half the catalog is
visible in the artifact.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..datasets.m5 import M5Catalog
from ..datasets.m5_metric import (
    M5Fold,
    rmse,
    wrmsse,
)
from .types import BenchmarkResult


@dataclass(frozen=True)
class M5PipelineOutput:
    """What a forecasting pipeline returns to the runner.

    The pipeline owns reading the CSVs, fitting a model, and producing
    predictions over the test horizon. The runner owns scoring. Splitting
    here is what keeps the kernel pandas-free: pipelines bring their own
    pandas/lightgbm/sklearn install; the runner is pure numpy.

    All arrays must be aligned on `series_ids` (row order matches across
    `predictions`, `actuals`, `weights`, `scales`).
    """

    predictions: np.ndarray
    """(n_series, n_test_days) — pipeline's forecast over the test fold."""

    actuals: np.ndarray
    """(n_series, n_test_days) — ground-truth sales for the same window.

    The pipeline returns this (rather than the runner re-deriving it) because
    series filtering / outlier handling can change which series are in scope,
    and the actuals must match that filtering exactly.
    """

    series_ids: list[str]
    """Per-row series identifier (e.g., M5 `id` column). Length = n_series."""

    weights: np.ndarray
    """(n_series,) — sales-dollar shares used for WRMSSE aggregation."""

    scales: np.ndarray
    """(n_series,) — training first-difference scales for RMSSE.

    Strictly positive. Caller filters zero-scale series before returning;
    the runner re-checks and surfaces a clear error if any slip through."""


class M5PipelineFn(Protocol):
    """A forecasting pipeline. Implementations live under `baselines/`."""

    def __call__(
        self,
        catalog: M5Catalog,
        fold: M5Fold,
        series_ids: list[str] | None = None,
    ) -> M5PipelineOutput:
        """Produce predictions for the test fold.

        `series_ids=None` means "all series the pipeline chooses to score."
        A non-None list scopes to that subset (used by the gate's
        regression-suite step to re-score specific series after a change).
        """
        ...


@dataclass(frozen=True)
class M5RunArtifacts:
    """Everything the runner observed in one `run()` call.

    Attached to the runner after `run()`; `scripts/m5_baseline.py` reads
    these to print the human summary and write the `iterations` row.
    """

    predictions: np.ndarray
    actuals: np.ndarray
    series_ids: tuple[str, ...]
    weights: np.ndarray
    scales: np.ndarray
    rmse: float
    wrmsse: float
    rewards: dict[str, float]
    """Per-series reward in [0, 1] keyed by series_id (frozen view of what
    `run()` returned in `BenchmarkResult.rewards`)."""


@dataclass
class M5BenchmarkRunner:
    """Runs an M5 forecasting pipeline and scores it as a `BenchmarkResult`.

    Construction takes the catalog + fold + pipeline once; the runner is
    re-entrant — `run()` can be called repeatedly with different
    `task_ids` subsets and produces an updated `last_artifacts` each call.
    """

    catalog: M5Catalog
    fold: M5Fold
    pipeline_fn: Callable[[M5Catalog, M5Fold, list[str] | None], M5PipelineOutput]
    last_artifacts: M5RunArtifacts | None = field(default=None, init=False, repr=False)

    async def run(
        self,
        task_ids: list[str] | None = None,
    ) -> BenchmarkResult:
        import asyncio
        out = await asyncio.to_thread(self.pipeline_fn, self.catalog, self.fold, task_ids)
        _validate_pipeline_output(out)

        rewards = _compute_rewards(out.predictions, out.actuals, out.scales, out.series_ids)

        agg_wrmsse = wrmsse(
            out.predictions, out.actuals,
            weights=out.weights, scales=out.scales,
        )
        agg_rmse = rmse(out.predictions, out.actuals)

        self.last_artifacts = M5RunArtifacts(
            predictions=out.predictions,
            actuals=out.actuals,
            series_ids=tuple(out.series_ids),
            weights=out.weights,
            scales=out.scales,
            rmse=agg_rmse,
            wrmsse=agg_wrmsse,
            rewards=dict(rewards),
        )
        return BenchmarkResult(rewards={k: v for k, v in rewards.items()})


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _compute_rewards(
    predictions: np.ndarray,
    actuals: np.ndarray,
    scales: np.ndarray,
    series_ids: list[str],
) -> dict[str, float]:
    """Per-series reward = exp(-RMSSE_i). Pure numpy, no pandas."""
    if len(series_ids) != predictions.shape[0]:
        raise ValueError(
            f"series_ids length {len(series_ids)} does not match "
            f"predictions row count {predictions.shape[0]}",
        )
    diff = predictions - actuals
    per_series_mse = np.mean(diff * diff, axis=1)
    rmsse_per_series = np.sqrt(per_series_mse / (scales * scales))
    rewards_arr = np.exp(-rmsse_per_series)
    return {sid: float(r) for sid, r in zip(series_ids, rewards_arr, strict=True)}


def _validate_pipeline_output(out: M5PipelineOutput) -> None:
    n = len(out.series_ids)
    if out.predictions.shape[0] != n or out.actuals.shape[0] != n:
        raise ValueError(
            "pipeline output row count mismatch: "
            f"series_ids={n}, predictions={out.predictions.shape[0]}, "
            f"actuals={out.actuals.shape[0]}",
        )
    if out.predictions.shape != out.actuals.shape:
        raise ValueError(
            f"predictions shape {out.predictions.shape} != "
            f"actuals shape {out.actuals.shape}",
        )
    if out.weights.shape != (n,) or out.scales.shape != (n,):
        raise ValueError(
            f"weights/scales must be ({n},); got "
            f"weights={out.weights.shape}, scales={out.scales.shape}",
        )
    if np.any(out.scales <= 0):
        raise ValueError(
            "pipeline returned scales <= 0 — filter zero-scale series "
            "(intermittent items with no training movement) upstream.",
        )
    if not np.all(np.isfinite(out.predictions)):
        raise ValueError(
            "pipeline returned NaN or inf in predictions — the gate would "
            "store NaN in iterations.val_score and silently break forever.",
        )
    if not np.all(np.isfinite(out.actuals)):
        raise ValueError("pipeline returned NaN or inf in actuals.")
