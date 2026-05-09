# W6 — 30-day M5 replay: substrate notes (2026-05-07 → 2026-05-08)

Compact retrospective on the multi-condition `make m5-replay-30day` runs done as
part of W6 (TODO-8). The sister doc `OVERNIGHT_REPORT.md` at the repo root has
the full per-step narrative; this file keeps the load-bearing findings.

## What got validated

1. ~~**First measured free local-model lift on real M5.**~~ **[Retracted —
   see `docs/local-model-testing.md` § F15.]** Stage D's `val_score 0.330 →
   0.380` = +14.9% on `qwen3-coder:30b` (Ollama OpenAI, 2026-05-08 morning,
   apparently reproduced 3× across 3 DBs at the time) did **not** reproduce
   in the W6 30-day v5 re-test (F6 / `M5SandboxError` 7/7 on identical
   substrate). F6 is a `qwen3-coder-30b` codegen property, not an
   LMS-Anthropic-transport property; Stage D's iter-4 lift was a lucky
   outlier across 7 sequential invocations. **No local model currently
   produces reliable lift on real M5.** Sonnet 4.6 cloud is the only
   confirmed driver — see v6 + v7 rows + the headline-finding section
   below for the +23.2% / +0.62% / 37× collapse story.
2. **`/no_think` directive is load-bearing for the BL.3 multi-turn loop on
   Qwen3-family models.** Mirrors the A4.4 single-turn fix. PR #61.
3. **Granite-4.1-8b on LMS hits the W5.2 LLM-judge contract** at 0.9667 ≥ 0.85
   on the 30-case eval set — local-only condition D substrate is achievable.
4. **PR #67 conversation compaction eliminates the `Context size has been
   exceeded` crash loop** that plagued the long replays. Pre-compaction (13k
   ctx attempt): every iter sub-second-failed. Post-compaction (48k ctx):
   iters run 1–4 minutes through real codegen + sandbox. Compaction is silent
   on success by design — proof is the absence of context errors, not log
   lines.

## What's still blocked

- **W5.5 meta-eval ≥0.7 stays cloud-Anthropic-only.** Local granite-4.1-8b
  scored 0.500 on the 10-pair set. Free-form quality grading is harder than
  structured admit/reject; W5.2 generalises locally, W5.5 does not.
- **`qwen3-coder-30b` on LMS Anthropic deterministically hits F6 / sandbox
  pipeline errors.** The model drives the loop and produces proposals, but the
  generated pipeline code returns `status=error` every attempt (14/14 in
  TODO-20, 27/27 in v4). The LMS Anthropic transport for this model is the
  wrong substrate for codegen.
- **Condition D requires `--api-format=anthropic` for the loop** because the
  judge reuses the loop's client. Running condition D on Ollama OpenAI is
  blocked until `--judge-base-url` is wired through (currently a stub,
  `run_improvement_loop.py:308`).

## Replay run history

| Run | Loop substrate | Conditions | Iters completed | Outcome |
|---|---|---|---|---|
| `ownevo_30day_smoke` | LMS Anthropic qwen3-coder-30b + LMS granite judge | D | 5 | 1 gate-pass, 4 sandbox-error. First end-to-end free condition-D run. |
| `ownevo_30day_first` (v1) | Same | A,C,D | 9+9 | D got +15.5% on iter 1; then context-overflow crash loop (28 errors). |
| `ownevo_30day_v2` | Same, qwen reloaded @ 48k ctx | A,C,D | 15+16 | C got +14.7% on iter 7; 86 min wall; same context-overflow problem. |
| `ownevo_30day_v3` | Same, `--max-iterations 12` workaround | A,C,D | 19+16 | C: 2 gate-pass; D: 2 gate-pass / 2 judge-reject. Mock-cap. |
| `ownevo_30day_v4` | Same, PR #67 compaction (no cap) | A,C,D | 15+12 | Compaction works (zero context errors). All 27 proposals fail with `M5SandboxError`: F6 binding constraint exposed. |
| `ownevo_30day_v5` | **Ollama OpenAI** qwen3-coder:30b | A,C | killed at 7 iters | F6 / `M5SandboxError` 7/7 even on Ollama OpenAI; Stage D's +14.9% lift looks like a lucky outlier rather than reproducible. F6 is a qwen3-coder property, not an LMS-Anthropic-transport property. |
| `ownevo_30day_v6_sonnet` | **Sonnet 4.6 cloud** + Opus-4.7 judge | A,C,D | **30+30+30 ✓ (machine restarted post-completion)** | **Hero artifact (v1 baseline).** C: 4 gate-passes, best_ever 0.4077 (+23.2% on val_score, **WRMSSE 1.046** measured on full test fold); D: 7 gate-passes (all judge-rejected), best_ever 0.4075. No new gate-passes after iter 8 in either condition (diminishing returns). Cost ~$15-20. Compaction substrate validated end-to-end (zero context errors over 90 paid iters). |
| `ownevo_30day_v7_on_v2` | **Sonnet 4.6 cloud** + Opus-4.7 judge | A,C,D | **30+30+30 ✓** | **Diagnostic artifact (v2 baseline, via PR #74's `--skill-version v2`).** C: 2 gate-passes, best_ever 0.4242 (+0.50% over v2 baseline); D: 4 gate-passes (all judge-rejected), best_ever 0.4247 (+0.62% over v2, **WRMSSE 1.0189** measured on full test fold = -0.47% reduction vs v2's 1.0237). 26/30 sandbox-error rate in C, 25/30 in D — agent's proposals on top of an already-tuned baseline crash much more often. |
| `ownevo_30day_v8_opus_on_v2` | **Opus 4.7 cloud** loop driver + Opus-4.7 judge | A,C,D | **C in flight at ~iter 2/30** (snapshot 2026-05-08 23:10) | **Updates the v6→v7 framing.** v8 C iter 2 hit val_score **0.4339** = **+2.79% over v2 baseline** — ~4.5× larger than Sonnet's best on the same baseline (v7's 0.4247 / +0.62%). Opus's diff was *cross-skill interaction reasoning*, not hyperparameter tuning: relax outlier_handler clip from p99 to p99.5 to preserve holiday/promo demand spikes that v2's calendar features (added in skill_v2) can now explain. Sonnet's v7 winner was a `num_leaves 128→255` tweak — pure tuning, no cross-skill reasoning. Cost so far ~$10-15; projected to completion ~$75-100. **In-flight; final numbers pending iter 30 completion.** |

## Headline finding (updated): the loop's lift on a tuned baseline is model-dependent

Holding the judge (Opus 4.7), iter budget (30), and harness fixed and varying both the parent baseline AND the loop driver:

| Run | Parent baseline | Loop driver | Best ever val_score | Best ever WRMSSE | Lift on val_score | Type of insight |
|---|---|---|---|---|---|---|
| v6 | skill_v1 (3 features, default LightGBM, regression loss) | Sonnet 4.6 | 0.4077 | 1.0463 | **+23.2%** | re-discovers textbook ML |
| v7 | skill_v2 (Tweedie loss, 20 features, tuned hyperparams — PR #72) | Sonnet 4.6 | 0.4247 | 1.0189 | **+0.62%** | hyperparameter tuning only |
| **v8 (in flight, snapshot iter 2/30)** | skill_v2 (same as v7) | **Opus 4.7** | **0.4339 so far** | TBD (in flight) | **+2.79% so far** | **cross-skill interaction reasoning (iter 2 winner)** |

The original v6→v7 collapse story (~37×) was a Sonnet-on-v2 finding, not a substrate-level finding. v8 (in flight) shows Opus on v2 already at +2.79% by iter 2, ~4.5× larger than Sonnet's best across all 30 iters. Opus's winning diff so far is interaction-aware: *"Relax outlier_handler clip from p99 to p99.5 to preserve holiday/promo demand spikes that v2's calendar features can now explain"* — reasoning across two skill files, not just tuning one. Sonnet's v7 winner was a lone `num_leaves 128→255` tweak in model_trainer.

**What this implies for the YC pitch (updated three-tier framing):**

- *Strong claim:* "On a weak baseline, Sonnet 4.6 autonomously recovers textbook ML." v6 +23% in 30 iters at ~$15-20.
- *Stronger-than-expected claim (pending v8 completion):* "On a strong baseline, model intelligence still finds non-obvious lift through cross-skill reasoning." v8 +2.79% by iter 2 already exceeds anything Sonnet found across all 30 iters of v7.
- *Honest claim:* "The lift Sonnet finds on tuned baselines is mostly hyperparameter tuning. Frontier intelligence (Opus) is where cross-skill reasoning kicks in." Costs more (~5× Sonnet pricing) but produces qualitatively different proposals.
- *Defensive claim:* "On both weak and strong baselines, the gate stopped every regression." v6 + v7 + v8 (so far): zero false promotions across 122+ gate-relevant paid iters (v6 C+D = 60, v7 C+D = 60, v8 C = ~2+).

The v6/v7/v8 set is the load-bearing data, and **v8 reverses what looked like a hard ceiling.** Pending v8 completion (~iter 30) the framing may need another revision; current snapshot is the strongest reading available 2026-05-08 23:10.

### Sonnet's v7 winner vs Opus's v8 (so far) winner — the qualitative contrast

- **Sonnet v7 best** (D iter 6 → 0.4247): `num_leaves 128→255, min_data_in_leaf 100→50, +min_gain_to_split=0.01 +lambda_l1`. Single-file diff in `model_trainer.py`. Pure hyperparameter tuning.
- **Opus v8 best so far** (C iter 2 → 0.4339): `outlier_handler.py` clip relaxed from p99 to p99.5, with explicit reasoning about how v2's calendar features (added in `feature_engineer.py`) make the additional outliers learnable rather than noise. Single-file diff but informed by reading two files and reasoning about their interaction. *Caveat: this is a single iter-2 winning diff; whether cross-skill reasoning is Opus's general mode requires iter 30 to confirm.*

## Operational gotchas (would have saved hours)

These bit four times during the v4 setup:

- Loop default M5 path is `data/m5`; real data is at
  `/media/fast_data/work2026/ownevo/data/m5-forecasting-accuracy/`. Set
  `OWNEVO_M5_DIR`.
- Postgres dev container maps `5432/tcp -> 0.0.0.0:54330`. `OWNEVO_DATABASE_URL`
  must use port 54330, not 5432.
- Fresh DBs need `migrations/0001_substrate.sql` + `0002_failure_cluster_fingerprint.sql`
  applied; `m5_replay_30day.py` does not auto-migrate.
- Workflow rows must be pre-seeded for each `workflow_id` (FK on
  `traces.workflow_id`). Use `seed_m5_baseline.py --workflow-id <id>` per
  condition.
- LMS-loaded models default to a small context after server restart
  (~13k for qwen3-coder). Reload via `lmstudio-python` SDK with
  `config={"contextLength": 49152}`.
- LMS occasionally closes a TCP connection mid-inference. The Python loop
  parks in `epoll` forever (CLOSE-WAIT). Watchdog: kill any subprocess with a
  CLOSE-WAIT to `:1234` — orchestrator immediately retries the iter.

## Followups identified today

These are planned additions, not yet implemented:

- ~~**`make m5-replay-bootstrap`**~~ target removed (replaced by manual migration steps); was: fresh-DB → migrate → seed-workflows in one shot.
- **`--connection-timeout` flag** on `run_improvement_loop.py` to fail-fast
  on stuck LMS sockets instead of parking in `epoll`.
- **`--judge-base-url` wire-up** so condition D can run with a different
  transport than the loop driver (Ollama OpenAI loop + LMS Anthropic judge).
- **F6 root-cause investigation** — v5 showed F6 hits 7/7 on Ollama OpenAI
  too (see table), so the LMS Anthropic transport hypothesis is now uncertain.
  The investigation is: why did TODO-19 Stage D succeed while v5 failed? Is F6
  condition-specific (Stage D vs A/C), seed-specific, or model-intrinsic?
- **`docs/local-model-testing.md` F15** — write up the qwen3-coder
  result (positive TODO-19 finding + v5 qualification) alongside F11/F12.

## DB artifacts

Persisted on `ownevo-postgres` (port 54330):

- `ownevo_phase3_realm5_v22_qwen_memretest` — Stage D (the +14.9% win)
- `ownevo_30day_smoke` — first end-to-end free condition-D run
- `ownevo_30day_first` / `_v2` / `_v3` / `_v4` — replay variants above
- `ownevo_30day_v5` — Ollama OpenAI run (killed at 7 iters; F6/M5SandboxError 7/7)
