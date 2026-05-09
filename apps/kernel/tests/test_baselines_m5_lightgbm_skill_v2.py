"""Smoke tests for the v2 stronger-baseline skill bodies.

These exercise `feature_engineer.engineer()` and `model_trainer.train()`
in isolation against a hand-built `RawSeriesData` so the lag math, rolling
window math, feature-column contract, and bounds checks are verified
without relying on the M5 CSV fixture or a Docker sandbox.

Why this lives outside the existing v1 test file: v2 is shipped as an
independent baseline snapshot consumed via the `skill_override_dir`
bind-mount. v1's orchestrator contract (`from .skill_v1 import …`) is
unaffected and stays covered by `test_baselines_m5_lightgbm.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_KERNEL = Path(__file__).resolve().parents[1]
if str(_REPO_KERNEL) not in sys.path:
    sys.path.insert(0, str(_REPO_KERNEL))

from baselines.m5_lightgbm import RawSeriesData  # noqa: E402
from baselines.m5_lightgbm.skill_v2 import feature_engineer  # noqa: E402
from ownevo_kernel.datasets import M5Fold  # noqa: E402

_N_TRAIN = 400  # >= max lag (364) and rolling lookback (91 + 28)
_N_VAL = 28
_N_TEST = 28
_N_SERIES = 3


def _fold(n_val: int = _N_VAL, n_test: int = _N_TEST) -> M5Fold:
    """Minimal M5Fold — engineer() only reads len(validation) / len(test)."""
    return M5Fold(
        train=tuple(f"d_{i}" for i in range(1, _N_TRAIN + 1)),
        validation=tuple(f"d_{i}" for i in range(_N_TRAIN + 1, _N_TRAIN + n_val + 1)),
        test=tuple(
            f"d_{i}"
            for i in range(_N_TRAIN + n_val + 1, _N_TRAIN + n_val + n_test + 1)
        ),
    )


def _raw(
    *,
    n_train: int = _N_TRAIN,
    n_val: int = _N_VAL,
    n_test: int = _N_TEST,
    n_series: int = _N_SERIES,
) -> RawSeriesData:
    """Synthetic raw data with `cell[i,j] = i*1000 + j` so any lag access
    has a unique, easy-to-verify value."""
    train = (np.arange(n_series)[:, None] * 1000 + np.arange(n_train)).astype(np.float64)
    val = (np.arange(n_series)[:, None] * 1000 + np.arange(n_train, n_train + n_val)).astype(np.float64)
    test = (
        np.arange(n_series)[:, None] * 1000
        + np.arange(n_train + n_val, n_train + n_val + n_test)
    ).astype(np.float64)
    return RawSeriesData(
        series_ids=[f"S_{i}" for i in range(n_series)],
        train_actuals=train,
        validation_actuals=val,
        test_actuals=test,
        dollar_volume=None,
        metadata=[
            # Two distinct cat_ids (S_0 and S_2 share) so categorical encoding is testable.
            {"cat_id": "FOODS", "dept_id": "FOODS_1", "store_id": "CA_1", "state_id": "CA", "item_id": f"FOODS_{i:03d}"}
            if i != 1
            else {"cat_id": "HOBBIES", "dept_id": "HOBBIES_1", "store_id": "CA_1", "state_id": "CA", "item_id": "HOBBIES_001"}
            for i in range(n_series)
        ],
        val_dow=np.arange(n_val) % 7,
        test_dow=(np.arange(n_test) + 5) % 7,  # offset so dow differs from val
    )


def test_feature_cols_are_the_documented_20():
    """The skill_v2 docstring claims 20 features; lock it down."""
    features = feature_engineer.engineer(_raw(), _fold())
    expected = (
        ["lag_28", "lag_56", "lag_91", "lag_182", "lag_364"]
        + [f"rolling_mean_{w}_lag28" for w in (7, 28, 56, 91)]
        + [f"rolling_std_{w}_lag28" for w in (7, 28, 56, 91)]
        + ["day_of_week", "is_weekend"]
        + [f"{f}_code" for f in ("cat_id", "dept_id", "store_id", "state_id", "item_id")]
    )
    assert features.feature_cols == expected
    assert len(features.feature_cols) == 20
    # Categorical columns: day_of_week + 5 encoded id codes (NOT is_weekend).
    assert features.categorical_feature_cols == [
        "day_of_week",
        "cat_id_code",
        "dept_id_code",
        "store_id_code",
        "state_id_code",
        "item_id_code",
    ]


def test_long_frame_shapes():
    """Train rows = n_series * n_val; test rows = n_series * n_test."""
    features = feature_engineer.engineer(_raw(), _fold())
    assert len(features.train) == _N_SERIES * _N_VAL
    assert len(features.test) == _N_SERIES * _N_TEST
    assert features.target_col == "y"
    # Target column is val_actuals for train rows, test_actuals for test rows.
    # Series S_1, val day 0 → cell[1, n_train] = 1*1000 + 400 = 1400.
    train = features.train
    s1_v0 = train[(train["series_id"] == "S_1")].iloc[0]
    assert s1_v0["y"] == pytest.approx(1400.0)


def test_lag_math_known_values():
    """lag_28 of val day j sources train_actuals[:, n_train - 28 + j]; lag_364 sources [:, n_train - 364 + j]."""
    features = feature_engineer.engineer(_raw(), _fold())
    train = features.train
    s0 = train[train["series_id"] == "S_0"].reset_index(drop=True)

    # Series 0 cell[0, j] = j; lag_28 of val day 0 → train col 372 → value 372.
    assert s0.loc[0, "lag_28"] == pytest.approx(_N_TRAIN - 28)        # 372
    assert s0.loc[27, "lag_28"] == pytest.approx(_N_TRAIN - 1)        # 399
    assert s0.loc[0, "lag_364"] == pytest.approx(_N_TRAIN - 364)      # 36
    # Series 1 has +1000 offset.
    s1_lag28_d0 = train[train["series_id"] == "S_1"].reset_index(drop=True).loc[0, "lag_28"]
    assert s1_lag28_d0 == pytest.approx(1000 + _N_TRAIN - 28)         # 1372


def test_rolling_window_known_values():
    """rolling_mean_W_lag28 of val day j averages history cols [n - 28 - W + 1 + j, n - 28 + j]."""
    features = feature_engineer.engineer(_raw(), _fold())
    s0 = features.train[features.train["series_id"] == "S_0"].reset_index(drop=True)

    # W=7 lag=28 day 0: cols [400-34, ..., 400-28] inclusive = [366..372]; mean = 369.
    assert s0.loc[0, "rolling_mean_7_lag28"] == pytest.approx(369.0)
    # W=7 lag=28 day 27: cols [393..399]; mean = 396.
    assert s0.loc[27, "rolling_mean_7_lag28"] == pytest.approx(396.0)
    # rolling_std on a contiguous arithmetic sequence of length 7 (numpy ddof=0).
    expected_std = np.arange(366, 373).std()
    assert s0.loc[0, "rolling_std_7_lag28"] == pytest.approx(expected_std)


def test_test_rows_use_train_plus_val_history():
    """test rows reach back into train+val concatenated; lag_28 of test day 0 is the first val cell."""
    features = feature_engineer.engineer(_raw(), _fold())
    s0_test = features.test[features.test["series_id"] == "S_0"].reset_index(drop=True)
    # n_history = n_train + n_val = 428. lag_28 of test day 0 → history col 400 → first val cell = 400.
    assert s0_test.loc[0, "lag_28"] == pytest.approx(400.0)
    # lag_28 of test day 27 → history col 427 → last val cell = 427.
    assert s0_test.loc[27, "lag_28"] == pytest.approx(427.0)


def test_categorical_encoding_is_stable_and_consistent():
    """Codes are insertion-order; train and test share the same encoding."""
    features = feature_engineer.engineer(_raw(), _fold())
    # cat_id: S_0=FOODS, S_1=HOBBIES, S_2=FOODS → codes 0, 1, 0.
    train = features.train
    codes_by_series = {
        sid: train[train["series_id"] == sid]["cat_id_code"].iloc[0]
        for sid in ("S_0", "S_1", "S_2")
    }
    assert codes_by_series == {"S_0": 0, "S_1": 1, "S_2": 0}
    # Same code in the test frame.
    test = features.test
    assert test[test["series_id"] == "S_0"]["cat_id_code"].iloc[0] == 0
    assert test[test["series_id"] == "S_1"]["cat_id_code"].iloc[0] == 1


def test_is_weekend_flag_matches_dow():
    """is_weekend = 1 iff day_of_week >= 5 (Sat=5, Sun=6)."""
    features = feature_engineer.engineer(_raw(), _fold())
    train = features.train
    weekdays = train[train["day_of_week"] < 5]
    weekends = train[train["day_of_week"] >= 5]
    assert (weekdays["is_weekend"] == 0).all()
    assert (weekends["is_weekend"] == 1).all()


def test_insufficient_history_raises():
    """max_lookback = max(364, 91+28) = 364; anything less must raise."""
    raw_short = _raw(n_train=363)
    with pytest.raises(ValueError, match="need >= 364 train days"):
        feature_engineer.engineer(raw_short, _fold())


def test_val_test_length_mismatch_raises():
    """v2 assumes |val| == |test|; doc'd contract."""
    raw = _raw(n_test=21)
    with pytest.raises(ValueError, match=r"\|val\|=\|test\|"):
        feature_engineer.engineer(raw, _fold(n_val=28, n_test=21))


def test_engineer_then_train_runs_end_to_end():
    """Smoke-test the engineer → trainer chain. Skips when lightgbm isn't installed."""
    pytest.importorskip("lightgbm")
    from baselines.m5_lightgbm.skill_v2 import model_trainer

    raw = _raw()
    fold = _fold()
    features = feature_engineer.engineer(raw, fold)
    model = model_trainer.train(features, raw, fold)
    assert model.feature_cols == features.feature_cols
    assert model.categorical_feature_cols == features.categorical_feature_cols
    # Booster is trained — predict on test frame returns one float per test row.
    preds = model.booster.predict(features.test[features.feature_cols])
    assert preds.shape == (_N_SERIES * _N_TEST,)
    assert np.all(np.isfinite(preds))
