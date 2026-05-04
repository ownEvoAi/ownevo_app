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

import lightgbm as lgb

from ownevo_kernel.datasets import M5Fold

from .. import FeatureMatrix, TrainedModel

# Tight model on purpose: this is the Day-1 floor. The agent will
# expand depth, leaves, and rounds in W4 once the loop is live.
_NUM_BOOST_ROUND = 100
_PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 5,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 5,
    "verbose": -1,
    # Determinism — the W2.6 reproducibility bar requires bit-identical
    # predictions across runs. Multi-threaded LightGBM accumulates floats
    # in non-deterministic order; pin to one thread for the baseline.
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
    """Fit a single global LightGBM regressor on the validation fold.

    The fitted booster + the column lists travel into `predictor.predict`
    via `TrainedModel`. We don't carry the booster's internal feature
    name list in `TrainedModel` because it's already implicit in
    `feature_cols` — and lightgbm enforces alignment at predict time
    when a column subset is passed.
    """
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
