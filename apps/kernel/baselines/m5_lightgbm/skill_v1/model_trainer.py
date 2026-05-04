"""
---
id: m5.baseline.v1.model_trainer
kind: python
created_by: bootstrap-2026-W2.6
capability_tags:
  - m5
  - baseline
  - model_trainer
retention:
  stateless: true
---
"""

from __future__ import annotations

from ownevo_kernel.datasets import M5Fold

from .. import FeatureMatrix, RawSeriesData, TrainedModel


def train(
    features: FeatureMatrix,
    raw: RawSeriesData,
    fold: M5Fold,
) -> TrainedModel:
    """Fit the model.

    Seasonal-naive v1 has no parameters to fit — the forecast template
    is the feature itself. We still return a `TrainedModel` so the
    pipeline shape is stable when LightGBM v2 lands and this stage
    starts producing a real booster.

    The empty `params` is intentional — the predictor reads its template
    from the `FeatureMatrix`, not from the model. LightGBM will populate
    `params` with serialized booster bytes.
    """
    del features, raw, fold
    return TrainedModel(params={})
