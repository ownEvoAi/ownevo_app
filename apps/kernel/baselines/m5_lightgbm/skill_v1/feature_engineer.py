"""
---
id: m5.baseline.v1.feature_engineer
kind: python
created_by: bootstrap-2026-W2.6
capability_tags:
  - m5
  - baseline
  - feature_engineer
retention:
  stateless: true
---
"""

from __future__ import annotations

import pandas as pd

from ownevo_kernel.datasets import M5Fold

from .. import FeatureMatrix, RawSeriesData

_TARGET = "y"
_FEATURE_COLS = ["lag_28", "day_of_week", "cat_id_code"]
_CATEGORICAL_FEATURE_COLS = ["cat_id_code"]


def engineer(raw: RawSeriesData, fold: M5Fold) -> FeatureMatrix:
    """Build long-format train + test DataFrames for the LightGBM regressor.

    Train rows target the validation fold (lag-28 reaches into train —
    fully observed). Test rows target the test fold (lag-28 reaches into
    validation — also fully observed since `|val| == |test|` by
    construction). The agent never sees test cells in training.

    Categoricals are integer-encoded from the series metadata. Train and
    test use the same series_ids so the code mapping is always stable.
    """
    n_val = len(fold.validation)
    n_test = len(fold.test)
    if raw.train_actuals.shape[1] < n_val:
        raise ValueError(
            "Need at least n_val days of training history for lag-28 features: "
            f"train_days={raw.train_actuals.shape[1]}, n_val={n_val}",
        )

    train_lag = raw.train_actuals[:, -n_val:]
    test_lag = raw.validation_actuals
    cat_codes, _ = _encode_categorical([m.get("cat_id", "") for m in raw.metadata])

    train_df = _long_frame(
        series_ids=raw.series_ids,
        target=raw.validation_actuals,
        lag=train_lag,
        dow=raw.val_dow,
        cat_codes=cat_codes,
    )
    test_df = _long_frame(
        series_ids=raw.series_ids,
        target=raw.test_actuals,
        lag=test_lag,
        dow=raw.test_dow,
        cat_codes=cat_codes,
    )
    del n_test

    return FeatureMatrix(
        series_ids=list(raw.series_ids),
        train=train_df,
        test=test_df,
        target_col=_TARGET,
        feature_cols=list(_FEATURE_COLS),
        categorical_feature_cols=list(_CATEGORICAL_FEATURE_COLS),
    )


def _long_frame(
    *,
    series_ids: list[str],
    target,
    lag,
    dow,
    cat_codes,
) -> pd.DataFrame:
    n_series, n_days = target.shape
    return pd.DataFrame({
        "series_id": [sid for sid in series_ids for _ in range(n_days)],
        _TARGET: target.reshape(-1).astype("float64"),
        "lag_28": lag.reshape(-1).astype("float64"),
        "day_of_week": (
            [int(d) for _ in range(n_series) for d in dow]
        ),
        "cat_id_code": [code for code in cat_codes for _ in range(n_days)],
    })


def _encode_categorical(values: list[str]) -> tuple[list[int], dict[str, int]]:
    """Stable integer codes for categorical strings (insertion order)."""
    index: dict[str, int] = {}
    codes: list[int] = []
    for v in values:
        if v not in index:
            index[v] = len(index)
        codes.append(index[v])
    return codes, index
