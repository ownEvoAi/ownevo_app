"""M5 metric implementations + held-out fold helper (W2.6 prerequisite).

Functions in this module are pure numpy — no pandas dep. Predictions and
actuals come in as 2D arrays with shape `(n_series, n_days)`. Series
order between predictions and actuals MUST match.

What's here:
  rmse(preds, actuals)
      Standard RMSE across all (series, day) cells. Used as the headline
      M5-baseline number per the benchmark plan.
  wrmsse(preds, actuals, weights, scales)
      The official M5 metric. Per-series RMSSE = RMSE divided by the
      training-set first-difference scale; weighted by sales-dollar share.
  compute_wrmsse_weights_and_scales(train_actuals, train_dollars)
      Helper that derives the per-series scale (from training first
      differences) and weights (from training dollar volume).
  make_held_out_fold(catalog, val_days, test_days)
      Computes the train / val / test day-column split per Phase 0 lock:
      last 28 days = test; prior 28 = validation; everything before = train.

Phase 2 wires the sandbox-side pipeline output (a CSV of forecasts) to
these scorers. Today the gate self-test uses synthetic arrays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .m5 import M5Catalog


# ---------------------------------------------------------------------------
# Held-out fold
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class M5Fold:
    """Training / validation / test split over M5 day columns.

    M5's sales table uses `d_1` ... `d_N` as day columns. This split is
    contiguous in time per Phase 0: train = oldest, then val_days, then
    test_days = newest. The agent only sees train + val rows; test rows
    are held out for the gate's val_score computation.
    """

    train: tuple[str, ...]
    validation: tuple[str, ...]
    test: tuple[str, ...]

    @property
    def total_days(self) -> int:
        return len(self.train) + len(self.validation) + len(self.test)


def make_held_out_fold(
    catalog: M5Catalog,
    *,
    val_days: int = 28,
    test_days: int = 28,
) -> M5Fold:
    """Compute the train/val/test day-column split.

    Reads the day columns from `sales_train_validation.csv` (those starting
    with `d_`). Last `test_days` go to test; prior `val_days` to validation;
    rest to train. Order is preserved so the gate runner can replay in
    chronological order.
    """
    day_cols = tuple(c for c in catalog.sales_train.columns if c.startswith("d_"))
    if len(day_cols) < val_days + test_days:
        raise ValueError(
            f"Not enough day columns to carve a fold: have {len(day_cols)}, "
            f"need {val_days + test_days} for val+test alone.",
        )
    test = day_cols[-test_days:]
    validation = day_cols[-(val_days + test_days):-test_days]
    train = day_cols[:-(val_days + test_days)]
    return M5Fold(train=train, validation=validation, test=test)


# ---------------------------------------------------------------------------
# Metric functions
# ---------------------------------------------------------------------------


def rmse(predictions: np.ndarray, actuals: np.ndarray) -> float:
    """Root mean squared error across all cells.

    Both arrays must share shape; broadcasting is intentionally rejected
    so a series-mismatch bug surfaces here, not silently as a misaligned
    score.
    """
    p, a = _asarray(predictions, actuals)
    diff = p - a
    return float(np.sqrt(np.mean(diff * diff)))


def wrmsse(
    predictions: np.ndarray,
    actuals: np.ndarray,
    *,
    weights: np.ndarray,
    scales: np.ndarray,
) -> float:
    """Weighted RMSSE per the M5 paper.

    Per-series:
        RMSSE_i = sqrt(mean_t((Yhat_{i,t} - Y_{i,t})^2) / scale_i^2)

    Aggregate:
        WRMSSE = sum_i (weight_i * RMSSE_i)

    `scales` are the training-set first-difference scales; `weights` are
    sales-dollar shares. Both are 1-D arrays of length n_series. Scales
    must be > 0 — pre-filter zero-scale series (intermittent demand with
    no movement in training) before calling, or the result is +inf.
    """
    p, a = _asarray(predictions, actuals)
    n_series = p.shape[0]
    if weights.shape != (n_series,):
        raise ValueError(
            f"weights shape {weights.shape} != ({n_series},)",
        )
    if scales.shape != (n_series,):
        raise ValueError(
            f"scales shape {scales.shape} != ({n_series},)",
        )
    if np.any(scales <= 0):
        raise ValueError(
            "All scales must be > 0; pre-filter zero-scale series "
            "(no training movement) before calling wrmsse.",
        )
    per_series_mse = np.mean((p - a) ** 2, axis=1)
    rmsse_per_series = np.sqrt(per_series_mse / (scales * scales))
    return float(np.sum(weights * rmsse_per_series))


def compute_wrmsse_weights_and_scales(
    train_actuals: np.ndarray,
    *,
    dollar_volume: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Derive (weights, scales) from the training set.

    `train_actuals`: (n_series, n_train_days) of unit sales.
    `dollar_volume`: (n_series,) of per-series sales-dollar volume in the
        training window. If None, weights are uniform 1/n_series.

    Scale formula matches the M5 reference:
        scale_i = sqrt(mean_t((Y_{i,t} - Y_{i,t-1})^2)) over training days.

    Returned `scales` may contain zeros (intermittent series with flat
    training); caller must filter before passing to `wrmsse`.
    """
    train = np.asarray(train_actuals, dtype=np.float64)
    if train.ndim == 2 and train.shape[0] == 0:
        raise ValueError(
            "No series remain — all were filtered before compute_wrmsse_weights_and_scales. "
            "Check that outlier_handler is not dropping every series.",
        )
    if train.ndim != 2 or train.shape[1] < 2:
        raise ValueError(
            f"train_actuals must be 2D with >=2 days; got shape {train.shape}",
        )
    diffs = np.diff(train, axis=1)
    scales = np.sqrt(np.mean(diffs * diffs, axis=1))

    n_series = train.shape[0]
    if dollar_volume is None:
        weights = np.full(n_series, 1.0 / n_series)
    else:
        dv = np.asarray(dollar_volume, dtype=np.float64)
        if dv.shape != (n_series,):
            raise ValueError(
                f"dollar_volume shape {dv.shape} != ({n_series},)",
            )
        total = float(np.sum(dv))
        if total <= 0:
            raise ValueError("Total dollar_volume must be > 0")
        weights = dv / total

    return weights, scales


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _asarray(p: np.ndarray, a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pa = np.asarray(p, dtype=np.float64)
    aa = np.asarray(a, dtype=np.float64)
    if pa.shape != aa.shape:
        raise ValueError(
            f"predictions shape {pa.shape} != actuals shape {aa.shape}",
        )
    if pa.ndim != 2:
        raise ValueError(f"expected 2D arrays (n_series, n_days), got {pa.ndim}D")
    return pa, aa
