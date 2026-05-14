# M5 stronger-baseline plan

Companion to `OVERNIGHT_REPORT.md` and `docs/W6_30DAY_REPLAY_NOTES.md`. Written
2026-05-08 after v6's best lifted skill landed at WRMSSE 1.05 against a static
baseline of 1.30 — a real lift, but well above the M5 leaderboard winner band.
This doc captures (a) what WRMSSE actually means, (b) what it would take to
seed the loop with leaderboard-credible code as the *new* baseline, and (c)
the pragmatic v2 we're doing today as the bridge.

## What WRMSSE is

**Weighted Root Mean Squared Scaled Error** — the official M5 evaluation
metric. Two pieces:

1. **RMSSE per series** (Root Mean Squared *Scaled* Error):
   `RMSSE_i = sqrt( mean_h (yhat_{i,h} - y_{i,h})^2 / scale_i )` where
   `scale_i` is the in-sample mean squared first-difference of training
   sales. The scaling makes the metric unitless and comparable across
   series with very different sales magnitudes (a hot SKU vs a slow one).
2. **Dollar-weighted aggregation:** `WRMSSE = sum_i (w_i * RMSSE_i)` where
   `w_i` is the series' share of total sales-dollar volume in the last 28
   days of training. Forecasting the high-revenue items well matters more
   than forecasting tail items.

Lower is better. The metric punishes (a) errors larger than the series'
own day-to-day noise and (b) errors on dollar-heavy series.

**Reference points on the M5 Accuracy leaderboard (Kaggle, public):**

| Tier | WRMSSE band | What got teams there |
|---|---|---|
| Winner (Yeonjun In) | ~0.520 | 5-stage recursive LightGBM stacking + custom NN ensemble |
| Top-10 | 0.55–0.65 | LightGBM with Tweedie loss, per-store training, recursive prediction, calendar + price + lag + rolling features (~50–100 cols), ensembled with statsmodels methods |
| Top-50 | 0.65–0.80 | Tuned single-model LightGBM, Tweedie loss, ~50 features, single-pass prediction |
| Median entry | ~0.95 | Tuned LightGBM, ~20 features, default loss |
| sNaive baseline | ~1.10 | Last-week-same-day repeat |
| **ownEvo static v1** | **1.30** | Single LightGBM, 3 features (lag_28, day_of_week, cat_id), 100 boosting rounds, default hyperparams |
| **ownEvo v6 best (agent-driven)** | **1.05** | v1 + agent-added lag_3..lag_364, rolling means/stds, ewm, calendar features. +19.5% improvement on v1 baseline. |

## What it would take to port a leaderboard-grade baseline

The v1 substrate is deliberately minimal so the agent has obvious wins
available. Porting a stronger baseline means rewriting most of the v1 skill
files. Tradeoffs by tier:

### Tier 1: top-10 rung (~WRMSSE 0.65) — multi-day PR

**Effort:** ~3–4 days focused work. Filed as a TODO; not for today.

**Scope:**

- **`data_loader.py`** — extend to join weekly sell prices through the
  calendar's `wm_yr_wk` index. Currently the v1 loader explicitly skips
  this (line 33 comment). This adds ~5 hours of careful code (the
  many-to-one join is a memory pitfall on 30,490 series).
- **`feature_engineer.py`** — ~80 features:
  - Lags {7, 14, 21, 28, 42, 56, 91, 182, 364}
  - Rolling means with windows {7, 14, 28, 56, 91, 182} all lagged ≥28 days
  - Rolling stds with windows {7, 28, 91}
  - Price features: sell_price, normalized_sell_price, price_momentum,
    price_max_min, weekly_price_change
  - Calendar: month, week_of_year, day_of_year, year, quarter, is_weekend,
    days_to_christmas, days_to_thanksgiving
  - SNAP indicators per state (snap_CA, snap_TX, snap_WI)
  - Event features (event_name_1, event_type_1, event_name_2, event_type_2)
  - Encoded categoricals: item_id, dept_id, cat_id, store_id, state_id
- **`model_trainer.py`** — Tweedie loss (`variance_power=1.1`), per-store
  training (10 separate LightGBM models), num_leaves=128, num_boost_round=
  1500, early stopping on the validation fold.
- **`predictor.py`** — recursive prediction h=1..28. For each forecast
  day, lag-7/lag-14 features need yesterday's prediction. This breaks the
  current single-shot prediction contract; the orchestrator's gate may
  need updates to handle the recursion deterministically.
- **`ensemble.py`** — keep simple (single-model average); leave the
  LightGBM × NN ensemble for tier 0 (winner-grade).
- **Memory:** ~80 features × 30,490 series × 1850 rows ≈ 35 GB raw, ~5 GB
  after LightGBM binning. Sandbox memory cap likely needs to bump from
  4 GiB to 8 GiB.

**Open questions before the multi-day PR starts:**

- Do we want to cite a specific Kaggle notebook or github repo as the
  port reference, or implement from M5-public-knowledge? Citing makes
  the result more defensible for the live pitch.
- Per-store training breaks the current "single global model" contract.
  Does the gate's reproducibility guarantee still hold across 10
  independently-trained LightGBM models? (Probably yes if seeds are
  fixed per store, but worth verifying.)
- Recursive prediction at the gate boundary: the gate's
  `validate_pipeline_output` shape may need a "predicted with recursion"
  marker so the agent's diff_summary can call out which days it
  forecasted forward.

### Tier 2: top-50 rung (~WRMSSE 0.75–0.80) — long PR

**Effort:** ~1–2 days focused work.

Same as Tier 1 minus per-store training (~10× memory savings, single
global model with store_id as feature). Drop the NN ensemble and the
multi-stage recursive stacking. Keep the price-feature join, recursive
prediction, Tweedie loss, ~60 features. WRMSSE expected ~0.75–0.85.

### Tier 3: pragmatic v2 (~WRMSSE 0.90–0.95) — today

**Effort:** ~3–4 hours.

Smallest meaningful strengthening. No price-feature integration (skip
the loader extension), no recursive prediction (keep single-shot), no
per-store training. The win comes from:

- ~25 features in `feature_engineer.py`: lags {7, 14, 28, 56, 91, 182,
  364}, rolling means {7, 14, 28, 56} (all lagged ≥28 days), rolling
  stds {7, 28}, calendar (month, week_of_year, year, is_weekend),
  encoded categoricals (item_id, dept_id, store_id, state_id, cat_id).
- Tweedie loss in `model_trainer.py` (`variance_power=1.1`), num_leaves
  bumped from default 31 to 128, num_boost_round 100 → 800.
- Same data_loader, predictor, ensemble — no contract changes.

Expected WRMSSE: **0.90–0.95**. Materially stronger than v1 (1.30) but
not leaderboard-grade.

The v7 30-day replay then runs Sonnet 4.6 on top of this. The lift
question becomes: can the loop find improvements on a tuned-LightGBM
baseline, not just a toy baseline?

## Decision: do tier 3 today, defer tiers 1–2

Tier 3 ships today as a feature branch; lands a real strengthening of the
baseline without breaking the v1 contract. Tiers 1–2 stay as TODO entries
for a focused multi-day session. This doc is the spec for that session.

Followup tracking:

- **TODO:** open a PR-spec issue for the tier-1 port (per-store training
  + recursive prediction + price features). Link this doc.
- **TODO:** before starting the tier-1 port, decide whether to cite a
  specific public notebook (Kaggle / github) as the reference port or
  implement from M5-public-knowledge.
- **TODO:** Audit whether the gate's reproducibility contract holds for
  per-store-trained models + recursive prediction. May require small
  changes to `validate_pipeline_output` and `iterations` schema.
