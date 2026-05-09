"""
---
id: m5.baseline.v2.feature_engineer
kind: python
created_by: m5-stronger-baseline-2026-05-08
capability_tags:
  - m5
  - baseline
  - feature_engineer
  - lag-features
  - rolling-mean
  - calendar-features
  - encoded-categoricals
retention:
  stateless: true
---

V2 stronger-baseline feature engineer. Bumps v1's 3 features to 20
without breaking the substrate's no-recursive-prediction contract:
5 lag offsets + 4 rolling means + 4 rolling stds + day_of_week +
is_weekend + 5 encoded categoricals.

Design notes
------------
* All lag features have lookback >= n_val (28) so the source is fully
  observed in `train_actuals` (for training rows targeting val) or
  `validation_actuals` (for test rows). No leakage; no recursive
  prediction at predict time.
* Rolling features (mean / std) are lagged by an extra 28 days so the
  window itself sits entirely in pre-val training history when building
  train rows, and entirely in pre-test (train + val) data when building
  test rows.
* Calendar / date features are deferred to a later port — they require
  extending data_loader to surface absolute dates per fold day. v2
  derives `is_weekend` from the dow array already present in
  `RawSeriesData` to capture weekend effects without that extension.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ownevo_kernel.datasets import M5Fold

from .. import FeatureMatrix, RawSeriesData

_TARGET = "y"

# Lag offsets (days). All >= n_val=28 so train/test rows get fully
# observed lag sources without recursive prediction.
_LAG_OFFSETS: tuple[int, ...] = (28, 56, 91, 182, 364)

# Rolling windows (in days). Each window is *itself* lagged by an
# additional `_ROLLING_LAG` days so the window stays out of the
# target fold during both train and test row construction.
_ROLLING_WINDOWS: tuple[int, ...] = (7, 28, 56, 91)
_ROLLING_LAG = 28

# Encoded categorical fields (in addition to v1's cat_id_code).
_CATEGORICAL_FIELDS: tuple[str, ...] = (
    "cat_id",
    "dept_id",
    "store_id",
    "state_id",
    "item_id",
)


def engineer(raw: RawSeriesData, fold: M5Fold) -> FeatureMatrix:
    """Build long-format train + test DataFrames with v2 features.

    Train rows target the validation fold; lag/rolling sources reach
    into ``train_actuals``. Test rows target the test fold; lag/rolling
    sources reach into ``train_actuals`` + ``validation_actuals``
    (concatenated as the "history" array passed to the lag builder).

    Categoricals are integer-encoded once per call against the per-
    series metadata; train and test share the same encoding.
    """
    n_val = len(fold.validation)
    n_test = len(fold.test)
    if n_val != n_test:
        raise ValueError(
            f"v2 feature_engineer assumes |val|=|test|; got val={n_val}, test={n_test}"
        )
    max_lookback = max(max(_LAG_OFFSETS), max(_ROLLING_WINDOWS) + _ROLLING_LAG)
    if raw.train_actuals.shape[1] < max_lookback:
        raise ValueError(
            f"need >= {max_lookback} train days for v2 features; "
            f"got {raw.train_actuals.shape[1]}"
        )

    # History arrays: pre-val for train rows; pre-test for test rows.
    train_history = raw.train_actuals
    test_history = np.concatenate([raw.train_actuals, raw.validation_actuals], axis=1)

    cat_codes_by_field: dict[str, list[int]] = {}
    for field in _CATEGORICAL_FIELDS:
        codes, _ = _encode_categorical([m.get(field, "") for m in raw.metadata])
        cat_codes_by_field[field] = codes

    train_df = _long_frame(
        series_ids=raw.series_ids,
        target=raw.validation_actuals,
        history=train_history,
        dow=raw.val_dow,
        cat_codes_by_field=cat_codes_by_field,
    )
    test_df = _long_frame(
        series_ids=raw.series_ids,
        target=raw.test_actuals,
        history=test_history,
        dow=raw.test_dow,
        cat_codes_by_field=cat_codes_by_field,
    )

    feature_cols = (
        [f"lag_{k}" for k in _LAG_OFFSETS]
        + [f"rolling_mean_{w}_lag28" for w in _ROLLING_WINDOWS]
        + [f"rolling_std_{w}_lag28" for w in _ROLLING_WINDOWS]
        + ["day_of_week", "is_weekend"]
        + [f"{field}_code" for field in _CATEGORICAL_FIELDS]
    )
    categorical_feature_cols = (
        ["day_of_week"] + [f"{field}_code" for field in _CATEGORICAL_FIELDS]
    )

    return FeatureMatrix(
        series_ids=list(raw.series_ids),
        train=train_df,
        test=test_df,
        target_col=_TARGET,
        feature_cols=feature_cols,
        categorical_feature_cols=categorical_feature_cols,
    )


def _long_frame(
    *,
    series_ids: list[str],
    target: np.ndarray,
    history: np.ndarray,
    dow: np.ndarray,
    cat_codes_by_field: dict[str, list[int]],
) -> pd.DataFrame:
    """Long-format frame: one row per (series, target_day).

    `target` is (n_series, n_days) — the fold being predicted.
    `history` is (n_series, n_history_days) — pre-fold actuals used as
    lag/rolling sources. For training rows, history = train_actuals
    only; for test rows, history = train + validation concatenated.

    Day j of the target maps to history column `n_history - n_days + j`
    when computing lag_28 (since lag_28 of target day j is the actual
    sales 28 days earlier, which is `n_history - 28 + j`-th column in
    history when |target| = 28).

    Rolling windows of W days lagged by L days end at history column
    `n_history - L + j` and start at `n_history - L - W + 1 + j`.
    """
    n_series, n_days = target.shape
    n_history = history.shape[1]

    columns: dict[str, np.ndarray] = {
        "series_id": np.repeat(np.asarray(series_ids, dtype=object), n_days),
        _TARGET: target.reshape(-1).astype("float64"),
        "day_of_week": np.tile(dow.astype(np.int64), n_series),
        "is_weekend": (np.tile(dow.astype(np.int64), n_series) >= 5).astype(np.int64),
    }

    # Lag features: for offset k, target day j → history column n_history - k + j.
    for k in _LAG_OFFSETS:
        if n_history - k + n_days - 1 >= n_history or n_history - k < 0:
            # Should be caught by max_lookback; defensive only.
            raise ValueError(
                f"lag_{k} index out of bounds for history of {n_history} days"
            )
        cols = np.arange(n_history - k, n_history - k + n_days)
        columns[f"lag_{k}"] = history[:, cols].reshape(-1).astype("float64")

    # Rolling features: window W lagged L=28 days ending at column
    # `n_history - L + j`, starting at `n_history - L - W + 1 + j`.
    for w in _ROLLING_WINDOWS:
        end_cols = np.arange(n_history - _ROLLING_LAG, n_history - _ROLLING_LAG + n_days)
        # Window indices per target day: shape (n_days, w)
        window_offsets = np.arange(-w + 1, 1)  # (-w+1, -w+2, ..., 0)
        window_idx = end_cols[:, None] + window_offsets[None, :]  # (n_days, w)
        # Gather: (n_series, n_days, w)
        gathered = history[:, window_idx]
        columns[f"rolling_mean_{w}_lag28"] = gathered.mean(axis=2).reshape(-1).astype("float64")
        columns[f"rolling_std_{w}_lag28"] = gathered.std(axis=2).reshape(-1).astype("float64")

    # Encoded categoricals: one column per field; constant per series.
    for field, codes in cat_codes_by_field.items():
        col = np.repeat(np.asarray(codes, dtype=np.int64), n_days)
        columns[f"{field}_code"] = col

    return pd.DataFrame(columns)


def _encode_categorical(values: list[str]) -> tuple[list[int], dict[str, int]]:
    """Stable integer codes for categorical strings (insertion order)."""
    index: dict[str, int] = {}
    codes: list[int] = []
    for v in values:
        if v not in index:
            index[v] = len(index)
        codes.append(index[v])
    return codes, index
