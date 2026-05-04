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

import numpy as np
from ownevo_kernel.datasets import M5Fold

from .. import FeatureMatrix, RawSeriesData


def engineer(raw: RawSeriesData, fold: M5Fold) -> FeatureMatrix:
    """Build the seasonal-naive feature: the last 28 training days per series.

    `predictor.predict` consumes `last_n_train` directly as the next
    n-day forecast (canonical seasonal-naive baseline).

    LightGBM v2 will extend this with lag/rolling/categorical features
    and reshape the matrix to long form (one row per (series, day)).
    """
    if raw.train_actuals.shape[1] < len(fold.test):
        raise ValueError(
            "Not enough training days to seed the seasonal-naive forecast: "
            f"train_days={raw.train_actuals.shape[1]}, "
            f"test_horizon={len(fold.test)}",
        )
    horizon = len(fold.test)
    last_n = raw.train_actuals[:, -horizon:].astype(np.float64, copy=True)
    return FeatureMatrix(
        series_ids=list(raw.series_ids),
        features={"last_n_train": last_n},
    )
