"""
---
id: m5.baseline.v2.model_trainer
kind: python
created_by: m5-stronger-baseline-2026-05-08
capability_tags:
  - m5
  - baseline
  - model_trainer
  - tweedie-loss
  - tuned-hyperparams
retention:
  stateless: true
---

V2 stronger-baseline model trainer. Three changes vs v1:

1. **Tweedie loss** with ``variance_power=1.1``. M5 sales are
   zero-inflated (long tail of slow movers, many zero-sales days).
   Tweedie regression with variance_power in [1.0, 2.0] is the
   well-established M5 loss; v1's ``regression`` (squared error)
   pushes the model toward over-smoothed positive predictions on
   zero-heavy series. Tweedie 1.1 keeps it close to Gaussian for
   high-volume series while accommodating the spike-and-tail shape.
2. **Larger model**: ``num_leaves`` 31 → 128, ``min_data_in_leaf``
   5 → 100 (more conservative leaf splits), ``num_boost_round``
   100 → 800. v1's tight model was the deliberately-minimal floor;
   v2 lets LightGBM use its capacity.
3. **Same determinism guarantees as v1** — fixed seeds, single
   thread, ``deterministic=True``. Bit-identical predictions across
   runs are still required for the gate's reproducibility contract.
"""

from __future__ import annotations

import lightgbm as lgb

from ownevo_kernel.datasets import M5Fold

from .. import FeatureMatrix, RawSeriesData, TrainedModel

_NUM_BOOST_ROUND = 800
_PARAMS = {
    "objective": "tweedie",
    "tweedie_variance_power": 1.1,
    "metric": "rmse",
    "learning_rate": 0.05,
    "num_leaves": 128,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 0.1,
    "verbose": -1,
    # Determinism — same as v1.
    "seed": 0,
    "bagging_seed": 0,
    "feature_fraction_seed": 0,
    "data_random_seed": 0,
    "deterministic": True,
    "num_threads": 1,
    "force_col_wise": True,
}


def train(
    features: FeatureMatrix,
    raw: RawSeriesData,
    fold: M5Fold,
) -> TrainedModel:
    """Fit a single global LightGBM regressor with Tweedie loss + tuned
    hyperparams on the validation fold."""
    del raw, fold
    train_df = features.train
    train_set = lgb.Dataset(
        train_df[features.feature_cols],
        label=train_df[features.target_col],
        categorical_feature=features.categorical_feature_cols,
        free_raw_data=False,
    )
    booster = lgb.train(
        params=_PARAMS,
        train_set=train_set,
        num_boost_round=_NUM_BOOST_ROUND,
    )
    return TrainedModel(
        booster=booster,
        feature_cols=list(features.feature_cols),
        categorical_feature_cols=list(features.categorical_feature_cols),
    )
