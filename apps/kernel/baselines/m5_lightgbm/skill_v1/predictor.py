"""
---
id: m5.baseline.v1.predictor
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
    """Forecast 28 days per series.

    Seasonal-naive v1 returns the last-N training cells as the forecast.
    Shape: (n_series, len(fold.test)).

    LightGBM v2 will replace this with recursive prediction using the
    fitted booster from `model.params`.
    """
    del model
    horizon = len(fold.test)
    template = features.features["last_n_train"]
    if template.shape[1] != horizon:
        raise ValueError(
            "feature template column count does not match test horizon: "
            f"template={template.shape[1]}, horizon={horizon}",
        )
    return np.asarray(template, dtype=np.float64).copy()
