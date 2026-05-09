"""V2 stronger-baseline skill bodies for the M5 LightGBM pipeline.

Differences from v1 (see `m5_lightgbm/skill_v1/`):
  * `feature_engineer.py` — 20 features (vs v1's 3): 5 lag offsets
    {28,56,91,182,364}, rolling_mean + rolling_std at windows
    {7,28,56,91} all lagged 28 days (4+4), day_of_week + is_weekend,
    and 5 encoded categoricals for cat/dept/store/state/item.
  * `model_trainer.py` — Tweedie loss (variance_power=1.1), num_leaves
    128, num_boost_round 800, tuned min_data_in_leaf / lambda_l2.
  * `data_loader.py`, `outlier_handler.py`, `predictor.py`,
    `ensemble.py` — unchanged from v1 (no contract changes; no
    recursive prediction; no price-feature integration). All v2 lag
    offsets are >= n_val=28 so prediction stays single-shot.

Reference target: WRMSSE in 0.90-0.95 band (vs v1 baseline 1.30, vs
M5 leaderboard winners ~0.55-0.70). See
`docs/M5_STRONGER_BASELINE_PLAN.md` for the rationale and the
multi-day port that would push this to the top-10/top-50 rung.
"""
