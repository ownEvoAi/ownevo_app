"""
---
id: m5.baseline.v1.ensemble
kind: python
created_by: bootstrap-2026-W2.6
capability_tags:
  - m5
  - baseline
  - ensemble
retention:
  stateless: true
---
"""

from __future__ import annotations

import numpy as np


def ensemble(predictions_list: list[np.ndarray]) -> np.ndarray:
    """Combine per-model predictions into a single forecast.

    v1 ships single-model — the seasonal-naive predictor is the sole
    contributor — but the stage exists so the agent can iterate to a
    real ensemble (LightGBM + XGBoost + linear blend) without the
    surrounding pipeline shape changing.

    With one input, this is a passthrough; with N, it's a uniform mean.
    Weighted blending (LightGBM v3+) will replace the mean.
    """
    if not predictions_list:
        raise ValueError("ensemble requires at least one prediction array")
    shapes = {p.shape for p in predictions_list}
    if len(shapes) != 1:
        raise ValueError(
            f"ensemble inputs have inconsistent shapes: {sorted(shapes)}",
        )
    if len(predictions_list) == 1:
        return predictions_list[0].astype(np.float64, copy=True)
    return np.mean(np.stack(predictions_list, axis=0), axis=0)
