"""M5 metric + fold-helper tests — pure unit, no DB needed.

Reference values for RMSE / WRMSSE come from analytic computation against
small fixtures so a regression in the metric math fails loudly.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from ownevo_kernel.datasets import (
    compute_wrmsse_weights_and_scales,
    load_m5,
    make_held_out_fold,
    rmse,
    wrmsse,
)

# ---------------------------------------------------------------------------
# RMSE
# ---------------------------------------------------------------------------


def test_rmse_zero_when_perfect():
    actuals = np.array([[1.0, 2.0, 3.0]])
    assert rmse(actuals, actuals) == 0.0


def test_rmse_known_fixture():
    """Pre-computed by hand: predictions off by 1 every cell."""
    preds = np.zeros((2, 3))
    actuals = np.ones((2, 3))
    # mean((-1)^2) = 1.0; sqrt(1.0) = 1.0
    assert rmse(preds, actuals) == pytest.approx(1.0)


def test_rmse_mixed_errors():
    preds = np.array([[0.0, 2.0], [3.0, 5.0]])
    actuals = np.array([[1.0, 0.0], [0.0, 4.0]])
    # diffs: -1, 2, 3, 1   squares: 1, 4, 9, 1   mean = 15/4 = 3.75
    assert rmse(preds, actuals) == pytest.approx(np.sqrt(3.75))


def test_rmse_rejects_shape_mismatch():
    p = np.zeros((2, 3))
    a = np.zeros((3, 3))
    with pytest.raises(ValueError, match="shape"):
        rmse(p, a)


def test_rmse_rejects_1d_arrays():
    p = np.zeros(5)
    a = np.zeros(5)
    with pytest.raises(ValueError, match="2D"):
        rmse(p, a)


# ---------------------------------------------------------------------------
# WRMSSE
# ---------------------------------------------------------------------------


def test_wrmsse_zero_when_perfect():
    actuals = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    weights = np.array([0.5, 0.5])
    scales = np.array([1.0, 1.0])
    assert wrmsse(actuals, actuals, weights=weights, scales=scales) == 0.0


def test_wrmsse_uniform_weights_known_fixture():
    """Two series, off by 1 each cell, scale=1 → per-series RMSSE = 1.
    Uniform weights → WRMSSE = 1.0."""
    preds = np.zeros((2, 4))
    actuals = np.ones((2, 4))
    weights = np.array([0.5, 0.5])
    scales = np.array([1.0, 1.0])
    assert wrmsse(preds, actuals, weights=weights, scales=scales) == pytest.approx(1.0)


def test_wrmsse_weighted_by_dollar_share():
    """One series perfect, one off by 1. Weight on the bad series controls
    the aggregate. Weight=0.9 on bad series + scale=1 → WRMSSE = 0.9."""
    preds = np.array([[1.0, 1.0], [0.0, 0.0]])
    actuals = np.array([[1.0, 1.0], [1.0, 1.0]])
    weights = np.array([0.1, 0.9])
    scales = np.array([1.0, 1.0])
    # series 0: RMSSE = 0;  series 1: RMSSE = 1.
    # WRMSSE = 0.1*0 + 0.9*1 = 0.9
    assert wrmsse(preds, actuals, weights=weights, scales=scales) == pytest.approx(0.9)


def test_wrmsse_scale_normalizes_per_series():
    """Same prediction errors but different training-volatility scales →
    high-volatility series gets a lower normalized error."""
    preds = np.zeros((2, 4))
    actuals = np.ones((2, 4))
    weights = np.array([0.5, 0.5])
    # series 0: scale=1 → RMSSE = 1.   series 1: scale=2 → RMSSE = 0.5.
    # WRMSSE = 0.5*1 + 0.5*0.5 = 0.75
    scales = np.array([1.0, 2.0])
    assert wrmsse(preds, actuals, weights=weights, scales=scales) == pytest.approx(0.75)


def test_wrmsse_rejects_zero_scale():
    """Zero-scale series (no training movement) must be filtered upstream."""
    preds = np.zeros((2, 4))
    actuals = np.ones((2, 4))
    weights = np.array([0.5, 0.5])
    scales = np.array([1.0, 0.0])
    with pytest.raises(ValueError, match="scales must be > 0"):
        wrmsse(preds, actuals, weights=weights, scales=scales)


def test_wrmsse_rejects_weight_shape_mismatch():
    preds = np.zeros((2, 4))
    actuals = np.zeros((2, 4))
    with pytest.raises(ValueError, match="weights shape"):
        wrmsse(
            preds, actuals,
            weights=np.array([0.5, 0.3, 0.2]),
            scales=np.array([1.0, 1.0]),
        )


# ---------------------------------------------------------------------------
# Weights/scales derivation
# ---------------------------------------------------------------------------


def test_compute_scales_from_first_differences():
    """scale = sqrt(mean of squared first differences) per series."""
    train = np.array([
        [0.0, 1.0, 0.0, 1.0, 0.0],   # diffs: 1, -1, 1, -1; squares: 1; mean=1; scale=1
        [0.0, 2.0, 0.0, 2.0, 0.0],   # diffs: 2, -2, 2, -2; squares: 4; mean=4; scale=2
    ])
    weights, scales = compute_wrmsse_weights_and_scales(train)
    np.testing.assert_allclose(scales, [1.0, 2.0])


def test_compute_uniform_weights_when_no_dollar_volume():
    train = np.array([[0.0, 1.0], [0.0, 2.0], [0.0, 3.0]])
    weights, _ = compute_wrmsse_weights_and_scales(train)
    np.testing.assert_allclose(weights, [1 / 3, 1 / 3, 1 / 3])


def test_compute_weights_from_dollar_volume():
    """Weights are dollar shares — sum to 1.0."""
    weights, _ = compute_wrmsse_weights_and_scales(
        np.array([[0.0, 1.0, 0.5], [0.0, 2.0, 1.0], [0.0, 3.0, 1.5]]),
        dollar_volume=np.array([100.0, 300.0, 600.0]),
    )
    np.testing.assert_allclose(weights, [0.1, 0.3, 0.6])
    assert weights.sum() == pytest.approx(1.0)


def test_compute_rejects_too_few_days():
    """Need at least 2 days to compute first differences."""
    with pytest.raises(ValueError, match=">=2 days"):
        compute_wrmsse_weights_and_scales(np.array([[1.0]]))


# ---------------------------------------------------------------------------
# Held-out fold
# ---------------------------------------------------------------------------

# 100 day columns to exercise the 28/28 fold without needing the real M5.
_SALES_HEADER = (
    "id,item_id,dept_id,cat_id,store_id,state_id,"
    + ",".join(f"d_{i}" for i in range(1, 101))
)
_SALES_ROW = (
    "FOODS_1_001_CA_1_validation,FOODS_1_001,FOODS_1,FOODS,CA_1,CA,"
    + ",".join(["1"] * 100)
)
_PRICES = "store_id,item_id,wm_yr_wk,sell_price\nCA_1,FOODS_1_001,11101,2.50\n"
_CALENDAR = "date,wm_yr_wk,d\n2011-01-29,11101,d_1\n"
_SAMPLE_SUBMISSION = "id,F1\nFOODS_1_001_CA_1_validation,0\n"


@pytest.fixture
def m5_dir(tmp_path: Path) -> Path:
    (tmp_path / "sales_train_validation.csv").write_text(
        _SALES_HEADER + "\n" + _SALES_ROW + "\n",
    )
    (tmp_path / "sell_prices.csv").write_text(_PRICES)
    (tmp_path / "calendar.csv").write_text(_CALENDAR)
    (tmp_path / "sample_submission.csv").write_text(_SAMPLE_SUBMISSION)
    return tmp_path


def test_make_held_out_fold_default_28_28(m5_dir: Path):
    """Phase 0 lock: last 28 days = test, prior 28 = validation, rest = train."""
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)
    assert len(fold.test) == 28
    assert len(fold.validation) == 28
    assert len(fold.train) == 100 - 28 - 28
    assert fold.test == tuple(f"d_{i}" for i in range(73, 101))
    assert fold.validation == tuple(f"d_{i}" for i in range(45, 73))
    # No overlap.
    assert set(fold.train) & set(fold.validation) == set()
    assert set(fold.validation) & set(fold.test) == set()


def test_fold_total_days_property(m5_dir: Path):
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog)
    assert fold.total_days == 100


def test_make_held_out_fold_custom_sizes(m5_dir: Path):
    catalog = load_m5(m5_dir)
    fold = make_held_out_fold(catalog, val_days=7, test_days=7)
    assert len(fold.test) == 7
    assert len(fold.validation) == 7
    assert len(fold.train) == 86


def test_make_held_out_fold_rejects_short_dataset(m5_dir: Path):
    """Fewer day columns than val+test must raise — silently truncating
    would corrupt the gate's held-out claim."""
    catalog = load_m5(m5_dir)
    with pytest.raises(ValueError, match="Not enough day columns"):
        make_held_out_fold(catalog, val_days=60, test_days=60)
