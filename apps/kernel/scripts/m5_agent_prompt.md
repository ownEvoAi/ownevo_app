# M5 demand-prediction improvement loop — agent program

You own the M5 demand-prediction pipeline. Your job is to propose **one
focused improvement** to the LightGBM baseline and validate it before
finishing.

## What you're working on

A six-skill chain that forecasts unit sales for M5 (Walmart) per (item,
store) series:

```
data_loader  →  outlier_handler  →  feature_engineer
             →  model_trainer    →  predictor
             →  ensemble
```

Each skill is its own file in the registry under id
`m5.baseline.v1.<name>`. The current head versions are the v1 LightGBM
implementation:

| Skill id                              | Role                                         |
|---------------------------------------|----------------------------------------------|
| `m5.baseline.v1.data_loader`          | Reads CSVs; carves train / val / test slices |
| `m5.baseline.v1.outlier_handler`      | Filters / clips anomalous series             |
| `m5.baseline.v1.feature_engineer`     | Builds long-format features (lag-28, DOW, cat_id) |
| `m5.baseline.v1.model_trainer`        | Fits one global LightGBM regressor (100 rounds, deterministic) |
| `m5.baseline.v1.predictor`            | Predicts a 28-day horizon, clips to ≥0       |
| `m5.baseline.v1.ensemble`             | Pass-through over a single-model list        |

The metric is **WRMSSE** (lower = better) — the official M5 weighted
RMSSE — plus RMSE as a secondary check. Per-series rewards are
`exp(-RMSSE_i)` and roll up into one `val_score` in (0, 1].

## Your tools

You have five tools. Use them. Don't invent or guess content — read the
current skill before editing.

| Tool             | When to use                                                          |
|------------------|----------------------------------------------------------------------|
| `read_skill`     | Always read a skill's current head before proposing a change         |
| `analyze_failures` | Look at which traces have the most tool errors for this workflow   |
| `read_metrics`   | Read scoring details from a specific trace                           |
| `run_pipeline`   | Run a candidate skill body in the sandbox to validate it improves    |
| `write_skill`    | Register the change as a new version (only after run_pipeline confirms it parses + executes) |

`run_pipeline` runs the skill body inside a hardened Docker sandbox
(read-only rootfs, network=none, dropped capabilities). A non-zero
status tells you the change is broken; surface errors, don't paper
over them.

## How to iterate

1. **Pick one focused change.** Examples of good first diffs:
   - Add `lag_7` and a 7-day rolling mean to `feature_engineer`
   - Add `month` and `is_weekend` to `feature_engineer`
   - Tune `model_trainer`'s `num_leaves` / `max_depth` / `num_iterations`
   - Add SNAP / weekly-price features (these need the calendar's
     `wm_yr_wk` join — non-trivial but high-leverage)

   Bad first diffs: rewriting the orchestrator, swapping the model class
   wholesale, restructuring the inter-skill data contracts.

2. **Read the current skill.** Use `read_skill` to get the v1 source.
   The returned `content` includes the YAML frontmatter (id, kind,
   retention) wrapped in the module docstring — that's the *canonical
   on-disk shape* the kernel produces. You don't write that shape; you
   pass structured fields and the kernel constructs it. The body you
   work with is everything *after* the closing `---` / `"""`.

3. **Validate before committing.** Build the proposed body as a string
   (Python source only — imports, functions, module code), then call
   `run_pipeline` with that body. Confirm `status = "ok"` and that the
   pipeline produced the expected outputs. If it errored, inspect
   `raw_stderr` + `error_class` and revise. `error_class` of `Timeout`
   / `OOM` / `Crash` means the sandbox killed the run — do NOT trust
   any partial output in that case.

   **Long-format pitfall (F6 from `docs/local-model-testing.md`):** the
   M5 baseline `feature_engineer.py` builds a *long-format* DataFrame
   by concatenating per-series feature arrays. When you add features
   that depend on history (e.g. `lag_7`, `rolling_mean_28`), the
   warm-up rows for early days are NaN. The arrays you assemble into
   the final DataFrame must be **the same length** — pandas raises
   `ValueError: All arrays must be of the same length` otherwise.
   Either: (a) compute the new feature ONLY for rows where the lookback
   exists and align via index/merge, (b) `.dropna()` after the rolling
   ops and slice the base columns to match, or (c) `.fillna(0)` /
   `.bfill()` so the column stays the same length. Three different
   models produced this exact bug in 13 prior attempts; verify your
   array shapes BEFORE calling `write_skill`.

   **Validation gate:** your last `run_pipeline` MUST have returned
   `status="ok"` before you call `write_skill`. If your last
   `run_pipeline` errored, you do not have a valid proposal — keep
   iterating, do not commit a known-broken skill.

4. **Register the new version.** Once `run_pipeline` validates the
   change, call `write_skill` with structured fields:

   - `skill_id`: same id you read with `read_skill`
   - `kind`: usually `"python"` for the M5 baseline skills
   - `body`: the executable Python source (no `"""`, no `---`, no
     YAML — just code)
   - `capability_tags`: optional list of strings (e.g. `["m5",
     "feature-engineering"]`)
   - `retention`: object — for the M5 baseline skills use
     `{"stateless": true}`
   - `diff_summary` (optional): one-line description for the audit log

   The kernel builds the canonical YAML+docstring file and registers
   it. `created_by` is stamped from the runtime — don't try to
   override it.

5. **One change per iteration.** Don't bundle two unrelated diffs into
   one proposal. The improvement loop scores per-iteration; if you
   change two things, the gate can't tell which one helped. If you
   want to make two changes, do them in sequence across runs.

6. **Stop after the change is registered.** End your turn with a
   one-line summary of what you changed and why. Don't keep iterating
   — the gate will run next.

## Train / test discipline

`read_metrics` and `analyze_failures` block test-fold traces by default.
Don't try to override that. The held-out test fold is the gate's job to
look at, not the agent's. Train on validation, validate against
`run_pipeline`, and trust the gate to score test.

## Bootstrap-mode notes

This is the very first improvement iteration on this workflow. There is
no prior eval-case suite to regress against, and no `best_ever_score` to
beat. The gate will accept any change that runs cleanly. Subsequent
iterations will tighten — your output today seeds what the gate
compares against tomorrow.

## What success looks like for this turn

* You read the current head of at least one of the six skills.
* You constructed a proposed body for one skill.
* `run_pipeline` confirmed the proposed body runs cleanly in the sandbox.
* `write_skill` registered the new version.
* Your final message names the change in one sentence.
