"""
---
id: m5.baseline.v2.predictor
kind: python
created_by: bootstrap-2026-W2.6
capability_tags:
  - m5
  - baseline
  - predictor
retention:
  stateless: true
---
"""

from __future__ import annotations

import numpy as np

from ownevo_kernel.datasets import M5Fold

from .. import FeatureMatrix, TrainedModel


def predict(
    model: TrainedModel,
    features: FeatureMatrix,
    fold: M5Fold,
) -> np.ndarray:
    """Forecast 28 days per series with the fitted LightGBM booster.

    The test frame is already in long format with one row per
    (series, test_day); we predict, clip negatives to zero (sales are
    non-negative — letting LightGBM emit small negatives skews WRMSSE),
    and reshape back to (n_series, n_test_days). Series order is
    preserved because `feature_engineer` builds the long frame in
    series-major order.
    """
    test_df = features.test
    test_X = test_df[model.feature_cols]
    raw_preds = model.booster.predict(test_X)
    clipped = np.clip(np.asarray(raw_preds, dtype=np.float64), 0.0, None)

    n_test = len(fold.test)
    n_series = len(features.series_ids)
    if clipped.shape != (n_series * n_test,):
        raise ValueError(
            "predictor row count mismatch: "
            f"booster returned {clipped.shape}, expected {(n_series * n_test,)}",
        )
    return clipped.reshape((n_series, n_test))
