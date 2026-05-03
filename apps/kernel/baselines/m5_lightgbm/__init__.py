"""M5 baseline orchestrator (Day-1 LightGBM).

Six skill modules chained into one forecasting pipeline:

    data_loader  → outlier_handler  → feature_engineer
                 → model_trainer    → predictor
                 → ensemble

The 6-file split mirrors the agent's intended iteration target: in W4,
the loop will propose a diff to one of these modules at a time
(per `docs/PLAN.md` § Track B "One hypothesis per iteration").

LightGBM model shape
--------------------
A single global LightGBM regressor over a long-format frame:

  * One row per (series, target_day) for both train and predict.
  * Features: `lag_28` (the actual cell 28 days prior — non-recursive
    for a 28-day horizon since the lag window is wholly inside the prior
    fold), `day_of_week` (0..6), and `cat_id` as a LightGBM categorical.
  * Target: unit sales for the target day.
  * Train set is the validation fold (target = `validation_actuals`,
    lag-28 looks back into the train fold). Test predictions look back
    into the validation fold. The agent never sees test cells in
    training — gate's train/test discipline is preserved by construction.
  * Determinism: `seed`/`bagging_seed`/`feature_fraction_seed`/
    `data_random_seed` all pinned, `num_threads=1` and
    `deterministic=True` set; predictions are bit-identical across runs.

PR #11c lifts the Docker sandbox boundary (`LocalDockerSandbox` via
`run_pipeline` instead of in-process import) and PR #11d wires the
B3.4 reproducibility CI cache strategy. The orchestrator + runner do
not change shape.

In-process vs sandbox
---------------------
This orchestrator runs the skills **in-process** by direct Python
import — kernel pulls `lightgbm` + `pandas` only via the
`baselines-m5` extra (`pip install ownevo-kernel[baselines-m5]`).
PR #11c will execute the same skill bodies inside `LocalDockerSandbox`
via `run_pipeline`. The skill bodies are written to be portable to
that path: no module-level state, no global file handles, all I/O via
the catalog path passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ownevo_kernel.benchmark import M5PipelineOutput
from ownevo_kernel.datasets import M5Catalog, M5Fold

# ---------------------------------------------------------------------------
# Inter-skill data contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawSeriesData:
    """data_loader output → outlier_handler / feature_engineer input.

    Per-series arrays aligned on `series_ids`. Train and test windows are
    separated explicitly so the gate can audit train/test discipline.
    """

    series_ids: list[str]
    train_actuals: np.ndarray   # (n_series, n_train_days)
    validation_actuals: np.ndarray  # (n_series, n_val_days)
    test_actuals: np.ndarray    # (n_series, n_test_days)
    dollar_volume: np.ndarray | None  # (n_series,) or None when prices absent
    metadata: list[dict[str, str]]  # per-series cat_id/dept_id/store_id/...
    val_dow: np.ndarray   # (n_val_days,) day-of-week 0..6 for fold.validation
    test_dow: np.ndarray  # (n_test_days,) day-of-week 0..6 for fold.test


@dataclass(frozen=True)
class FeatureMatrix:
    """feature_engineer output → model_trainer / predictor input.

    Long-format frames keyed by stage name:
      * `train` — one row per (series, val_day); supervised target known.
      * `test`  — one row per (series, test_day); target held out for scoring.

    LightGBM consumes `train` for `fit`; `test` for `predict`. The
    `categorical_feature_cols` list keeps the column set explicit so the
    model_trainer doesn't have to guess at LightGBM's auto-detection.
    """

    series_ids: list[str]
    train: object   # pandas.DataFrame; declared as object to keep this module pandas-free
    test: object    # pandas.DataFrame
    target_col: str
    feature_cols: list[str]
    categorical_feature_cols: list[str]


@dataclass(frozen=True)
class TrainedModel:
    """model_trainer output → predictor input.

    Holds the fitted LightGBM Booster. Declared as `object` so this
    module doesn't require lightgbm at import time — the sandbox path
    in PR #11c will marshal the booster across the boundary as a
    serialized model_str.
    """

    booster: object  # lightgbm.Booster
    feature_cols: list[str]
    categorical_feature_cols: list[str]


# ---------------------------------------------------------------------------
# Orchestrator entrypoint
# ---------------------------------------------------------------------------


def run_baseline(
    catalog: M5Catalog,
    fold: M5Fold,
    series_ids: list[str] | None = None,
) -> M5PipelineOutput:
    """In-process pipeline: data_loader → ... → ensemble.

    Conforms to `M5PipelineFn` so an `M5BenchmarkRunner` can be built
    around it with `M5BenchmarkRunner(catalog, fold, run_baseline)`.
    """
    # Lazy imports keep this module's import-time cheap and let `register_*`
    # walk the directory without triggering all skill bodies.
    from .skill_v1 import (
        data_loader,
        ensemble,
        feature_engineer,
        model_trainer,
        outlier_handler,
        predictor,
    )

    raw = data_loader.load(catalog, fold, series_ids=series_ids)
    raw = outlier_handler.handle(raw)
    features = feature_engineer.engineer(raw, fold)
    model = model_trainer.train(features, raw, fold)
    preds = predictor.predict(model, features, fold)
    final_preds = ensemble.ensemble([preds])

    weights, scales = _compute_weights_and_scales(raw)

    return M5PipelineOutput(
        predictions=final_preds,
        actuals=raw.test_actuals,
        series_ids=list(raw.series_ids),
        weights=weights,
        scales=scales,
    )


# ---------------------------------------------------------------------------
# Skill source discovery (used by scripts/m5_baseline.py to register skills)
# ---------------------------------------------------------------------------


SKILL_FILES: tuple[str, ...] = (
    "data_loader.py",
    "outlier_handler.py",
    "feature_engineer.py",
    "model_trainer.py",
    "predictor.py",
    "ensemble.py",
)


def skill_files_dir() -> Path:
    """Filesystem path to the v1 skill source files. The bootstrap
    script reads each file as raw bytes and registers it via
    `ownevo_kernel.skills.registry.register_skill`."""
    return Path(__file__).parent / "skill_v1"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _compute_weights_and_scales(raw: RawSeriesData) -> tuple[np.ndarray, np.ndarray]:
    """Derive WRMSSE weights + scales from training data.

    Wraps `ownevo_kernel.datasets.compute_wrmsse_weights_and_scales` and
    filters zero-scale series to satisfy `M5BenchmarkRunner`'s contract
    (raises if any scale <= 0).
    """
    from ownevo_kernel.datasets import compute_wrmsse_weights_and_scales

    weights, scales = compute_wrmsse_weights_and_scales(
        raw.train_actuals,
        dollar_volume=raw.dollar_volume,
    )
    if np.any(scales <= 0):
        raise ValueError(
            "Training actuals contain zero-scale series (no movement). "
            "outlier_handler must filter or impute these before WRMSSE.",
        )
    return weights, scales
