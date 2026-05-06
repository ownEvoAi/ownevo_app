# Local-Model Testing Guide

How to evaluate local LLM backends (Ollama, LM Studio) for the
ownEvo M5 improvement loop. Living document — update with new
findings each sweep.

---

## Why local models

The improvement loop is the heart of ownEvo's MVP. Running it on a
hosted frontier model (Claude / GPT-4) is fine for proof-of-concept,
but not for a customer demo where:

- Cost-per-iteration matters for the 30-day M5 replay narrative.
- Air-gapped deployments are an explicit ask from regulated buyers.
- Reproducing a customer's failure mode locally is part of the
  approval-UX value prop.

The MVP needs a credible "best local model on the loop" claim,
backed by a sweep that's reproducible from this file.

---

## Backend overview

### Ollama (`/v1/chat/completions` via OpenAI-compatible endpoint)

- Speaks both `/api/chat` (native) and `/v1/chat/completions` (OpenAI shim).
- We use the OpenAI shim from `run_improvement_loop.py --api-format openai`.
- **Catalog and per-request behavior depend heavily on daemon env vars:**
  - `OLLAMA_CONTEXT_LENGTH=65536` — *daemon-level* default; not honored
    by `/v1` per-request without an explicit override.
  - `OLLAMA_NUM_PARALLEL=1` — **important**: any value > 1 splits the
    daemon context across slots, so a single agent sees only
    `OLLAMA_CONTEXT_LENGTH / NUM_PARALLEL` tokens. Set to 1 for
    single-user agent workloads.
  - `OLLAMA_KV_CACHE_TYPE=q8_0` — saves 0.5–1GB VRAM per loaded model
    with neutral throughput.
  - `OLLAMA_FLASH_ATTENTION=1` — neutral throughput, enables KV quantization.
  - `OLLAMA_MAX_LOADED_MODELS=1` — prevents VRAM contention; matches
    the sweep's "one model loaded" discipline.
- **Unload pattern works:** a no-op generate with `keep_alive=0`
  evicts a loaded model immediately.
- **No prompt caching** on `/v1`: each turn re-sends the full
  conversation. Wall-time cost grows with conversation length.

### LM Studio (`/v1/messages` Anthropic endpoint, plus `/v1/chat/completions` OpenAI endpoint)

- Speaks Anthropic streaming (`/v1/messages`) AND OpenAI-compatible
  (`/v1/chat/completions`). Both supported by our runner via
  `--api-format anthropic` or `--api-format openai`.
- **Anthropic streaming is the productive path** for the loop:
  - Heavy prompt caching (`cache_read` typically 80–90% of input).
  - Per-turn cost is dominated by output, not input — agent loops
    can run 25+ iterations cheaply.
- **OpenAI mode on LMS** has the same per-turn-cost shape as Ollama
  (no cache_read), but the conversation isn't truncated by NUM_PARALLEL
  splitting (that's an Ollama-specific gotcha).
- **Adapter rejection failure mode:** LMS's Anthropic shim sometimes
  fails on certain models' tool-call format mid-stream
  (`APIStatusError: Failed to generate a valid tool call.`). Affects
  Mistral-family models with `[TOOL_CALLS]` native format and some
  Qwen variants. Workaround: route via direct Ollama instead.
- **Unload via REST IS supported, just not via the v0 endpoints we
  initially tried** (verified 2026-05-04). Working paths:
  - `POST /api/v1/models/unload` with body `{"instance_id": "<id>"}`
    (LMS 0.4.0+; v1, not v0). [Source](https://lmstudio.ai/docs/developer/rest/unload).
  - `lmstudio-python` SDK with remote `baseUrl=http://<host>:1234`:
    `client.llm.unload()` + `config={"contextLength": 65536}` at load.
    [Source](https://lmstudio.ai/docs/python/llm-prediction/parameters).
  - CLI: `lms load <model> --context-length 65536` (run on the LMS
    host directly).
  - Default context length falls back to per-model `lms.json`. The
    OpenAI-compat endpoints DON'T expose context-length config; load-
    time SDK or CLI is required.
  - The previously-tried `keep_alive=0`, `/v1/internal/model/unload`,
    `/api/v0/models/<id>/unload`, `/v0/models/unload` all 404 or
    no-op silently — the v0 surface predates the working v1 endpoint.
  - **Follow-up:** wire `lmstudio-python` into a small `lms_remote.py`
    helper to drive proper unload + context-length-on-load before the
    real Phase 1 sweep.

---

## Sweep methodology

Four phases (a Phase 0 pre-flight tier was added 2026-05-04 once the
probe scripts shipped). **All runs sequential, only one model active
at a time.**

### Phase 0 — Pre-flight probes (PR #29, PR #31)

**Purpose:** triage the candidate list before paying for a full Phase
1 sandboxed-loop run. Catches API-level rejection, models that don't
emit tool calls, em-dash / smart-quote / signature regressions in
codegen — all in ~90s/model wall instead of 5-15 min/model for Phase 1.

**Tools:**

- `apps/kernel/scripts/probe_tool_calling.py` — single-turn `read_skill`
  call. Exit 0 (pass) / 1 (fail-no-tool) / 2 (transport error). ~30s.
- `apps/kernel/scripts/probe_skill_quality.py` — sends `predictor.py`
  with a 1-line modification request. Validates: AST parses, frontmatter
  `id:` intact, `def predict(model, features, fold)` signature intact,
  modification present, no em-dashes / smart-quotes / NBSP in code
  positions. ~60s.
- `apps/kernel/scripts/sweep_probes.py` — batch driver over a
  `<backend> <model>` list. JSONL + markdown summary, resumable via
  `--skip-completed`. Per-probe timeouts (120s tool-calling, 240s
  skill-quality) bound a hung model.

**Pass criterion:** both probes exit 0. Records as `overall=pass`.

**What probes CANNOT tell you (don't over-read it):**

- F4 stragglers — 8B models pass simple probes but stall in the M5
  multi-turn read-loop. Probe-passers still need Phase 1 confirmation.
- Skill-quality probe is a "rewrite the file" prompt that bypasses the
  agent's tool surface. Codegen quality proxy, not the real workflow.

**Empirical (2026-05-04 partial sweep, 25/48 candidates done):** 60%
Ollama probe-pass rate (15/25). LMS half not yet attempted (sweep
paused; resume planned with the `lms_remote.py` helper).

### Phase 1 — Synthetic-fixture compatibility scan

**Purpose:** filter the candidate list to models that can drive the
loop end-to-end at all. Synthetic fixture is small, deterministic,
doesn't trigger any real-data pipeline edge cases — a Phase 1 fail
is unambiguously the model's fault.

**Fixture:** `/tmp/m5_synth_smoke/` (5 series × 100 days; rebuilt
via `_build_synthetic_m5` in
`apps/kernel/tests/test_baselines_m5_lightgbm_sandboxed.py`).

**Pass criterion:** ≥1 `iterations` row written + `val_score`
recorded + no adapter-side rejection. Marginal/partial outcomes
(no `write_skill`, read-loop stall) are recorded but disqualified
from Phase 2.

### Phase 2 — Single full-real-M5 baseline

Model is irrelevant — the baseline is fixed code. **One run, not
five.** Produces the real-M5 baseline `val_score` that Phase 3
lifts against.

Resource bumps required:

| Knob | Default | Real M5 |
|---|---|---|
| `tmpfs_size_mb` | 512 | 4096 |
| `memory_mb` | 4096 | 16384 |
| `timeout_seconds` | 600 | 1800 |
| outer wall `timeout` | n/a | 7200 (Phase 3 only) |

### Phase 3 — Full improvement loop, top 1–2 models, real M5

The load-bearing claim. Iterations cap 50, hard timeout 2h. Per-iter
sandbox call uses the Phase 2 resource bumps. Output: iteration-by-
iteration `val_score` curve + agent diffs from the workflow DB.

---

## Findings (cumulative, updated each sweep)

### F1 — Ollama `/v1` truncates context unless `extra_body.options.num_ctx` is set

`AsyncOpenAI` doesn't pass `options.num_ctx` natively (not in the
OpenAI spec). Without it, Ollama's `/v1/chat/completions` uses a
default smaller than the daemon-level `OLLAMA_CONTEXT_LENGTH` —
even with `OLLAMA_NUM_PARALLEL=1`. Result: agent loops silently
truncate mid-run; the model loses early turns by the time it has to
recover from a tool error.

Same model (`qwen3-coder:30b`):

| Backend | Input tokens | cache_read | Outcome |
|---|---|---|---|
| LMS Anthropic streaming | 157,000 | 142,000 | ✅ write_skill, gate ran |
| Ollama `/v1` (NUM_PARALLEL=4) | 23,170 | 0 | ⚠️ end_turn early |
| Ollama `/v1` (NUM_PARALLEL=1, no patch) | 30,884 | 0 | ⚠️ end_turn early |

Fixed in PR #24 (`fix-openai-runner-num-ctx`): pass
`extra_body={"options": {"num_ctx": N}}` from `run_agent_turn_openai`,
plumbed via `--ollama-num-ctx` CLI flag.

### F2 — B4.1 (sandbox skill override) verified on real LLM output

PR #21 added `skill_override_dir` to `SandboxedM5BenchmarkRunner` so
the gate scores the agent's proposed skill, not the baked-in baseline.
Verified on a real LMS-OpenAI run during the sweep:

- Agent called `write_skill` proposing `m5.baseline.v1.model_trainer`.
- Bind-mount delivered the override to
  `/opt/ownevo/apps/kernel/baselines/m5_lightgbm/skill_v1/model_trainer.py`
  inside the sandbox.
- Sandbox imported the override and crashed with `SyntaxError` (the
  agent wrote malformed SKILL_FORMAT — see F3).
- Gate correctly classified the run as `sandbox-error`, persisted an
  `iterations` row + start/end audit entries, marked the proposal
  `gate-failed`.

This is stronger evidence than the synthetic regression test — it
shows the override path delivering arbitrary LLM-generated content,
the sandbox's natural import path picking it up, and the gate
correctly classifying the resulting failure.

### F3 — Agent SKILL_FORMAT compliance unreliable on lean-context conversations

On the same run that verified B4.1, the model wrote `---` at line 1
of its proposed `model_trainer.py` — frontmatter without the
surrounding Python docstring wrapper that
`docs/SKILL_FORMAT.md` requires. With Anthropic-streaming
context (157K), the gold-standard run produced valid SKILL_FORMAT;
with OpenAI-runner context (21K), the same model didn't.

Two hypotheses:

- (a) System prompt examples aren't carrying the docstring wrapper
  through context truncation.
- (b) `write_skill` should validate SKILL_FORMAT *before* persisting
  the proposal — strict parse + reject early. Currently it persists,
  the sandbox crashes, the gate logs `sandbox-error`. Catching format
  errors pre-gate would surface a clean `tool_call_result`
  `error_class=ValidationError` and let the agent iterate.

### F4 — 8B-class models stall in the read-loop, never commit to write_skill

| Model | Backend | iter | tool_calls | tool_errors | input | cache_read | output | Outcome |
|---|---|---|---|---|---|---|---|---|
| `granite-4.1-8b` | LMS Anthropic | 25 (max) | 25 | 11 | 80K | 64K | 10K | ⚠️ stall |
| `ibm/granite-3.2-8b` | LMS Anthropic | 25 (max) | 25 | 0 | 102K | 98K | 8.5K | ⚠️ stall |
| `meta-llama-3.1-8b-instruct` | LMS Anthropic | 25 (max) | 25 | 0 | 84K | 80K | **650** | ⚠️ stall (extreme) |

All three 8B models read + explore the workflow but never commit to
a hypothesis. Anthropic prompt caching works as expected
(`cache_read` ~80% of input). The most extreme case (llama-3.1-8b)
generated only 650 output tokens across 25 turns — ~26 tokens per
turn, essentially mechanical tool-calling with no reasoning trace.

**Takeaway (refined by F5 below):** the 8B stall is a real
capability gap, but the gap doesn't close at 14B or 32B either —
size alone is not the issue. See F5.

### F5 — qwen3-coder-30b is the only LMS-Anthropic model that reliably drives the loop (12 runs, 1 PASS)

After 10 LMS-Anthropic-streaming runs spanning 8B → 32B, the only
PASS is `qwen/qwen3-coder-30b` (val_score 0.4642 on synthetic, 19
iter, 18 calls, 11 tool errors recovered, 157K input / 142K
cache_read / 14K output). The remaining 9 runs fail in 5 distinct
modes:

| Mode | Models hit |
|---|---|
| **Read-loop stall** (no commit, output token count tiny) | granite-4.1-8b, granite-3.2-8b, llama-3.1-8b |
| **Output exhaustion** (max_tokens after 1–2 iter) | omnicoder-9b |
| **Tool-format struggle** (errors > 50%) | phi-4 (68%), qwen2.5-coder-32b (48%), qwen3-32b base (96%) |
| **Adapter rejection** (LMS shim refuses output) | qwen3.5-27b Claude-distill, qwen3.6-35b/27b, devstral-small-2 |
| **LMS-side load failure** | qwen3-30b-a3b-2507 |

**The capability is in the fine-tune, not the size.** qwen3-32b
(dense base) at the same size as qwen3-coder-30b had the *worst*
tool-format compliance of any model tested (96% errors). qwen2.5-coder
(prior coder generation) at 32B couldn't commit either. Only
qwen3-coder's specific training distribution — code + tool-use +
agentic recovery — produces the "drive the loop end-to-end" stack.

**Implications:**

- **Phase 3 picks itself.** With the current substrate
  (`run_agent_turn` + Anthropic streaming via LMS), the only viable
  local model for the M5 improvement loop is `qwen/qwen3-coder-30b`.
  Either Phase 3 runs on it as a single-model result, or the
  substrate is expanded.
- **F1 fix #2 (PR #24) is high-leverage.** It opens the Ollama side
  with `qwen3-coder:30b` direct (apples-to-apples confirmation) and
  unlocks Ollama-only 30B coders (`qwen3:30b-instruct`, `qwen3:30b-a3b`)
  that may also work.
- **Don't waste the sweep on more 8B–14B models.** The pattern is
  consistent. Future sweeps should jump straight to 27B+ class with
  fine-tunes that target tool-use (qwen3-coder, devstral coder
  variants, future qwen3.6-coder when released).

### F6 — qwen3-coder-30b's feature_engineer fail is deterministic; LMS strict-validation needs runtime workarounds

**Two new synthetic-fixture runs (post #21/#23/#24) on the same model and
substrate, same kickoff, both gate-failed with the same conceptual bug:**

| Endpoint | iter | tool_calls | tool_errors | input | cache_read | output | Outcome |
|---|---|---|---|---|---|---|---|
| LMS Anthropic streaming | 8 | 7 | 1 | 89,800 | 73,220 (82%) | 9,484 | gate-failed (sandbox-error) |
| LMS OpenAI compat | 10 | 9 | 3 | 118,615 | 0 | 11,354 | gate-failed (sandbox-error) |

Both runs proposed a `feature_engineer.py` rewrite that indexes the
1-D `dow` array as 2-D — `IndexError: too many indices for array`.
Different rewrite paths (line 193 vs 243) but **the same conceptual
misunderstanding** about array shape. This is reproducible model
behavior, not run-to-run noise. **Score didn't move** (B4.1 + F2
working as designed: gate refused to bless `0.99513` as a new "best
ever" because the proposal crashed).

**Two operational findings on top:**

#### F6a — LMS per-model context cap is separate from `OLLAMA_CONTEXT_LENGTH`

A first attempt at this run hit
`anthropic.APIStatusError: 'Model reloaded.'` mid-stream around
iteration 25. Root cause: LMS's per-model **JIT-load context** was at
the default (12k for this model). Conversation grew past 12k, LMS
silently evicted and reloaded the model, the streaming connection
broke. Bumping the JIT context to 64k in the LMS UI fixed it on the
retry. **Pre-load explicitly** before long runs:

```bash
lms unload --all
lms load qwen/qwen3-coder-30b --context-length 65536
```

This is orthogonal to the Ollama `--ollama-num-ctx` issue (F1) — LMS
has its own per-model cap that defaults low and isn't exposed via
the API request.

#### F6b — LMS `/v1/messages` strict validation is by-design, not a bug

The "adapter rejection" failure mode in F5 (and also a transient mid-run
`APIStatusError: Failed to generate a valid tool call` we hit) is
**intentional** per the LMS changelog: *"the Anthropic-compatible
`/v1/messages` API surfaces errors when the model generates an invalid
tool call, enabling Claude Code to recover gracefully"*. The shim
expects the client to catch the error, inject a synthetic
"that tool call was malformed; retry" turn, and continue.

**Recovery is now implemented** in `run_agent_turn`
(`apps/kernel/src/ownevo_kernel/middleware/claude_sdk/runner.py`):
catches the `"Failed to generate a valid tool call"` substring,
injects a synthetic `[assistant, user]` retry pair to keep message
alternation valid, and continues the loop. The rejected turn costs
one iteration toward `max_iterations` so recovery is automatically
bounded. Anthropic streaming remains the default.

The LMS OpenAI endpoint (`--api-format openai
--llm-base-url http://$OWNEVO_LLM_HOST:1234/v1`) is still a valid
alternative — it passes through whatever the model emits without
strict validation — but trades away `cache_read` tokens, raising
per-turn cost.

The qwen3-coder ↔ LMS tool-call mismatch is a known upstream issue:
[lmstudio-bug-tracker #825](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/825)
(non-OpenAI-compatible custom format),
[#1071](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1071)
(streaming XML tags),
[#827](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/827)
(parser confused by `<think>` blocks). Fixes ship piecemeal upstream;
adapter rejection will keep happening on subsets of runs until they
all land.

#### Implication for Phase 3

The model can drive the loop end-to-end on the M5 task, but the
proposal it converges on is buggy in a deterministic way. **Phase 3
on this model alone won't produce lift** until either:
- the kickoff message includes shape-contract hints for skills the
  model is likely to rewrite (e.g., "feature_engineer's `dow` is
  shape `(n_train_days,)`, broadcast series-major"), OR
- the model is given >1 attempt + an error-trace round-trip in the
  same conversation (which #21 already enables — but the model needs
  to actually pivot rather than trying the same approach twice in a
  row).

### F7 — Anthropic-cloud benchmark: Sonnet 4.6 first gate-PASS on real M5; Haiku hits same F6 bug

After PR #30 + #32 landed, three additional Phase-3 runs on real M5,
same kickoff + substrate, same `feature_engineer.py` target:

| Run | Model | Backend | Decision | val_score | iter | Cost |
|---|---|---|---|---|---|---|
| v9  | `claude-haiku-4-5-20251001` | Anthropic cloud | sandbox-error | – | 10 | $0.17 |
| **v10** | **`claude-sonnet-4-6`** | **Anthropic cloud** | **gate-PASS (bootstrap)** | **0.395143** | **6** | **$0.31** |
| v11 | `qwen/qwen3-coder-30b` | LMS Anthropic + #32 recovery | sandbox-error | – | 10 | local |

- **Sonnet 4.6 produced runnable code** — the first model to do so on
  this task, after qwen3-coder-30b (×3 prior runs in F6) and Haiku 4.5
  both deterministically hit the F6 length-mismatch bug.
- Sonnet committed in 6 iterations / 6 tool calls / 1 error — far
  more efficient than Haiku (10 iter / 4 errors) or qwen (10-11 iter /
  3-4 errors). Smaller / cheaper models retried more without
  recovering.
- `val_score = 0.3951` is a **+19% lift over the static Phase 2
  baseline `0.3310`** (val_score is mean reward, higher is better
  per the gate's `FAIL_NO_IMPROVEMENT` semantics). Bootstrap-mode
  `iteration_index=0` so `best_ever_score` advances `null → 0.3951`.

A multi-iteration replay (v12, 2026-05-04) confirmed the gate
correctly enforces improvement on iteration 2:

| Run | val_score | best_ever_before | gate decision |
|---|---|---|---|
| v12 (Sonnet, same model + workflow) | 0.3851 | 0.3951 | **gate-blocked-no-improvement** |

v12's rationale: *"val_score 0.3851 did not beat best_ever 0.3951
(epsilon 0)"* — confirms direction (higher = better) and demonstrates
the regression-blocking path. **B4.2 (First lift on M5)** ✅ achieved
at v10; **B4.3 (First gate-blocked regression)** ✅ achieved at v12.

**Takeaway:** the F6 bug pattern is **task-shape-specific**, not
model-class-specific. Both qwen3-coder-30b (open-weight, local) and
Haiku 4.5 (frontier, hosted) hit it; Sonnet 4.6 does not. The substrate
itself works correctly across all three (gate ran, audit chain logged,
override bind-mount delivered the agent's diff).

### F8 — LMS local prompt-caching is real but modest (~20% speedup), not Anthropic-cloud-equivalent

LMS reports `cache_read_input_tokens` in Anthropic-streaming responses
(typical 80-90% of input on long conversations). **The reported number
is real KV cache reuse, but the speedup is much smaller than Anthropic
cloud's marketing.** Empirical measurement (2026-05-04, 3 successive
runs of `probe_skill_quality.py` on `qwen/qwen3-coder-30b` LMS Anthropic):

| Run | elapsed | Δ from cold |
|---|---|---|
| #1 (cold) | 4.3s | — |
| #2 (warm, ≤60s after #1) | 3.4s | **−21%** |
| #3 (warm) | 3.3s | **−23%** |

Compare to Anthropic cloud, where `cache_read` typically delivers
3-5× faster TTFT. LMS appears to cache the system prompt prefix only;
full-prefix cache reuse may not be implemented.

**Operational implication:** treat `cache_read` as a **soft progress
signal** (high cache_read = same prefix being reused = not getting
truncated mid-run, useful F1-territory diagnostic), not a perf
optimization. Pick backend on agent quality + recovery support
(F6b runner fix favors LMS Anthropic), not on cache_read.

### F9 — Sonnet 4.6 hits a repeated month-feature bug when best_ever is set (Stage B 7-iter replay, 2026-05-04)

**Setup:** Stage B replay — 7 sequential iterations against a fresh DB
(`ownevo_phase3_realm5_stageb_v1`, workflow `m5-stageb-v1`),
`claude-sonnet-4-6` via Anthropic cloud, prompt caching auto-enabled (PR #33).
All 7 iterations used the same workflow_id so `best_ever_score`
accumulated across runs.

**Results:**

| iter | decision | val_score | best_ever | cache_read | output_tok | wall | cost |
|---|---|---|---|---|---|---|---|
| 0 | **gate-pass** | **0.3958** | 0.3958 | 37,020 | 10,485 | 150s | $0.28 |
| 1 | gate-blocked-no-improvement | 0.3317 | 0.3958 (held) | 33,893 | 3,233 | 95s | $0.11 |
| 2 | sandbox-error | null | 0.3958 (held) | 32,846 | 8,060 | 119s | $0.23 |
| 3 | sandbox-error | null | 0.3958 (held) | 70,924 | 11,529 | 151s | $0.34 |
| 4 | sandbox-error | null | 0.3958 (held) | 47,536 | 9,947 | 142s | $0.27 |
| 5 | sandbox-error | null | 0.3958 (held) | 35,950 | 9,942 | 133s | $0.28 |
| 6 | sandbox-error | null | 0.3958 (held) | 56,954 | 12,345 | 158s | $0.33 |

**Total: $1.84. Wall: ~16 min.**

**Observations:**

1. **Gate held `best_ever=0.3958` across all 6 non-pass iterations.** No
   regression was ever adopted. The audit chain has 14 entries (2 per
   iteration: gate-run-started + gate-run-completed). This is the
   regression-gate working correctly under sustained adversarial pressure.

2. **Caching engaged from iter 0.** `cache_read=37,020` even in iter 0 —
   the system prompt is cached within the multi-turn agent conversation.
   Iter 1 started with `cache_read=33,893` (cross-iteration cache hit;
   system prompt TTL > time between runs).

3. **Iters 2–6: deterministic month-feature bug (F9).** Each independently
   attempted to add a `month` seasonality feature via
   `pd.Timestamp(d).month` where `d` is an M5 date string in `d_NNNN`
   format (e.g., `d_1858`). This raises
   `pandas.DateParseError: Unknown datetime string format, unable to parse: d_1858`.
   The agents correctly identified that month seasonality would help M5
   forecasting — but all five independently made the wrong assumption that
   `fold.validation` dates are calendar-parseable. No cross-iteration
   memory exists to break the pattern.

**Why iters 2–6 all try `month` instead of something else:**
The winning skill from iter 0 has lag features but no calendar-based
seasonality. Sonnet consistently identifies month as the next best feature
to add. Because each iteration is a fresh conversation with no memory of
prior sandbox failures, the same reasoning path is taken every time.

**Implication — two possible mitigations:**

- **Prompt fix (low cost, shipped on `feat/f9-fix-and-sandbox-mem`):**
  Add to `m5_agent_prompt.md`:
  `fold.validation` and `fold.test` are lists of M5 day-ID strings like
  `"d_1858"`. They are NOT calendar dates. Do not pass them to
  `pd.Timestamp()`. To derive month: use
  `pd.Timestamp("2011-01-29") + pd.Timedelta(days=int(d[2:]) - 1)`.
- **Cross-iteration failure memory (bigger):** Populate the
  `failure_clusters` table from sandbox-error iterations so
  `analyze_failures` returns the F9 pattern. The agent can then read it
  and avoid the same approach. This is the correct long-term solution —
  the prompt fix is a workaround.

**Captured as TODO-22 (see `TODOS.md`).**

### F10 — Anthropic prompt-caching works cross-iteration at Sonnet latency (confirmed Stage B)

PR #33 added `cache_control: {"type": "ephemeral"}` on the system prompt
and the last tool definition. Stage B confirms it works end-to-end:

- Iter 0 had `cache_read_input_tokens=37,020` within its own multi-turn
  conversation.
- Iter 1 had `cache_read_input_tokens=33,893` at the *start* of a fresh
  invocation, confirming the cache survived across the ~2.5 min gap
  between runs (within the 5-minute Anthropic cache TTL).
- Subsequent iters all show substantial cache reads, with iter 3 peaking
  at 70,924 (its conversation accumulated more tool-result context than
  others before the cache was written).

**Cost structure validated:** non-cached input + output tokens dominate
on gate-pass iterations; cache savings are real but not dramatic on
7-iteration M5 scale (output cost is too high relative to input to see
the 80% headline reduction). The main benefit is latency — iter 1's 95s
wall vs iter 0's 150s was partly from shorter output (gate-blocked early),
partly from cache serving the system prompt.

### F11 — First compound lift on real M5: Stage C, F9 prompt fix validated, two gate-passes in 7 iters

**Setup:** Stage C — 7 sequential iterations against a fresh DB
(`ownevo_phase3_realm5_stagec_v1`, workflow `m5-stagec-v1`),
`claude-sonnet-4-6` via Anthropic cloud, prompt caching auto-enabled
(PR #33), F9-mitigation prompt fix in place (PR #35). All 7 iters
shared the workflow_id so `best_ever_score` accumulated.

**Results:**

| iter | decision | val_score | best_ever_after | cache_read | output_tok | wall | cost |
|---|---|---|---|---|---|---|---|
| 0 | **gate-pass** | **0.3859** | 0.3859 | 30,586 | 9,049 | 193s | $0.23 |
| 1 | gate-blocked-no-improvement | 0.3313 | 0.3859 | 36,408 | 3,092 | 95s | $0.11 |
| 2 | **gate-pass (compound)** | **0.3988** | **0.3988** | 49,592 | 12,363 | 217s | $0.33 |
| 3 | gate-blocked-no-improvement | 0.3302 | 0.3988 | 97,823 | 5,039 | 134s | $0.22 |
| 4 | sandbox-error | null | 0.3988 | 69,819 | 14,687 | 253s | $0.38 |
| 5 | gate-blocked-no-improvement | 0.3301 | 0.3988 | 47,107 | 4,555 | 171s | $0.18 |
| 6 | sandbox-error (OOM) | null | 0.3988 | 58,920 | 15,354 | 270s | $0.42 |

**Total: $1.86, ~22 min wall.**

**Outcomes:**

1. **First compound lift on real M5.** iter 0 (val_score 0.3859, +16.6% vs
   static baseline 0.3310) followed by iter 2 (val_score 0.3988, +20.5% vs
   baseline; +3.4% vs iter 0). The gate held best_ever between them
   (correctly rejecting iter 1's near-baseline 0.3313). This is the
   strongest "loop produces compounding improvement" empirical signal
   we have.

2. **F9 fix VALIDATED.** Stage C iter 0 successfully integrated the
   `month` feature (diff_summary: *"Add lag_7, rolling_mean_7,
   rolling_mean_28, month, and is_weekend features..."*). No
   `DateParseError`. Compare to Stage B where iters 2–6 all failed at
   `pd.Timestamp("d_1858").month`. The single-paragraph prompt addition
   (`_M5_ORIGIN + Timedelta(days=int(d[2:])-1)`) unblocked the
   month-seasonality branch.

3. **Gate held under sustained adversarial pressure.** 7 iterations,
   2 promotions, 5 rejections (3× gate-blocked-no-improvement,
   2× sandbox-error). Zero false promotions. Audit chain has 14 entries
   (2 per iteration). Same gate semantics as Stage B; with the F9 fix
   in place, productive iterations now happen alongside rejections.

4. **Iter 4 + iter 6 OOMed at the 512 MB sandbox default.** Stage C used
   the default; future replays should pass `--sandbox-mem-mb 1024` (added
   in PR #35) to give the agent's diffs more headroom on the 30,490-series
   real-M5 fold. The OOMs do NOT reach `best_ever` (gate correctly handles
   them as `error_class=OOM` → no advance), so the lift signal is
   preserved — but they are wasted iterations.

**Implication for the YC narrative:** B4.2 (first lift) and B4.3
(gate-blocked regression) were both already established before Stage C.
Stage C adds **the lift curve** — proof that the loop can produce more
than one improvement on the same problem. The single 2-step compound
demonstrates the loop's headline behavior (improve → gate-rejects-bad
→ improve again). 7 iterations, 2 promotions is consistent with what
we'd expect: most agent ideas don't improve, but the gate enforces
monotonicity and the agent eventually finds another winner.

### F12 — Cross-iteration failure memory is the binding constraint (Stage C + Stage B + TODO-20 + TODO-21 v2)

Across the 4 multi-iter runs we now have evidence that the missing piece
isn't model capability — it's **agent memory of prior failures within the
workflow**.

| Run | Model | Pattern |
|---|---|---|
| Stage B (Sonnet) | iters 2–6 | All 5 independently tried `pd.Timestamp("d_1858")` → DateParseError. No memory of the earlier failure. F9 fix patched the prompt. |
| Stage C (Sonnet) | iter 4 + iter 6 | Both OOMed at 512 MB default. No memory of prior OOM patterns. |
| Stage C (Sonnet) | iter 5 | Returned val_score 0.3301 — essentially the static baseline. No memory of which features iter 0 / iter 2 already promoted; agent re-explored a known-bad direction. |
| TODO-20 (qwen3-coder-30b) | iter 0 | Same `_long_frame` length-mismatch as the prior 13 attempts. The F6 prompt warning paragraph wasn't enough to overcome the model's deterministic codegen prior. |
| TODO-21 v2 (devstral) | 13 iter / 9 errors | Multiple run_pipeline failures, no successful write_skill. No memory of which approaches it had already tried. |

**Pattern:** every multi-iter run is bottlenecked on the agent
re-exploring known-bad directions. Prompt fixes (F6 warning, F9 day-ID
note) work *when the same kind of bug happens to be addressed*, but they
scale linearly with bug types — we'd need a hand-written paragraph for
every failure mode the agent might re-discover.

The architectural fix is **`analyze_failures` returning recent
`sandbox-error` rationale strings as a structured failure signature**,
so the agent reads "iter 4 OOMed adding feature X — try lighter alternatives"
on iter 5+. This is captured as **TODO-23** (graduated from TODO-22
option (b)).

The substrate works correctly across all of this — it's the agent's
context that's missing the prior-failure signal.

### F13 — A4.4 single-turn classification gate: devstral-small-2 (24B) is the local reference, matches Sonnet 4.6 (2026-05-05)

Different track from F4-F12 (which all measured *multi-turn agent loop*
on M5). The A4.4 NL-gen smoketest is a **single-turn forced tool-use**
gate: agent receives a workflow description + tool vocabulary +
trajectory through `target_step_index` (target event's bool label
redacted), emits `predict_label(value, rationale)` once. Score via the
A4.2 metric. Lower bar than the multi-turn loop — should be easier for
small models.

Setup: LiteLLM proxy translates Anthropic `/v1/messages` →
`ollama_chat/<model>` `/api/chat`. Config at
`infra/litellm/ollama.yaml`; dogfood script at
`apps/kernel/scripts/run_a4_4_local_smoke.sh`. Same metric-aware
prompt the cloud agent gets (per-workflow gate-metric framing block
naming family + target + dominant error mode).

Verdict against the 3 NL-gen fixtures (haiku 4.5, sonnet 4.6, opus 4.7
above the table for cloud comparison):

| backend | model | demand-pred (recall ≥0.50) | credit-risk (balanced_acc ≥0.40) | contract-review (f1 ≥0.75) | wall | cost |
|---|---|---:|---:|---:|---:|---:|
| Anthropic | haiku 4.5 | 0.20 ❌ | 0.25 ❌ | 0.91 ✅ | ~60s | $0.10 |
| Anthropic | **sonnet 4.6** (cloud reference) | **0.60 ✅** | **0.50 ✅** | 0.77 ✅ | ~128s | $0.50 |
| Anthropic | opus 4.7 | 0.20 ❌ | 0.42 ✅ (thin) | 1.00 ✅ | ~100s | $2 |
| Ollama | qwen2.5-coder:32b | 1.00 ✅ (always-True) | 0.50 ✅ | 0.89 ✅ | ~117s | $0 |
| Ollama | qwen3-coder:30b | 0.40 ❌ | 0.25 ❌ | 0.89 ✅ | ~90s | $0 |
| Ollama | **devstral-small-2** (24B, local reference) | **0.80 ✅** | **0.42 ✅** | 0.89 ✅ | ~100s | $0 |
| Ollama | gpt-oss:20b | err (max_tokens before tool-call) | — | — | — | $0 |

**Findings:**

1. **devstral-small-2 (24B, local) matches/beats Sonnet 4.6 on the
   single-turn gate.** It catches `winter-boot-spike-week-47` (the
   canonical past-miss Sonnet missed). Strongest local-model result
   in any sweep we've done — the NL-gen gate is *not* frontier-only.
2. **qwen3-coder:30b — F5's multi-turn gold standard — is weak at
   single-turn classification under partial info.** Strong codegen
   training distribution doesn't transfer to "predict the redacted
   bool from past trajectory + rule inference." Capability is task-
   shape-specific, not raw-capability-specific.
3. **qwen2.5-coder:32b passes all 3 by exploiting the recall-target
   framing — predicts True on every demand-prediction case.** Recall
   = 1.0 by construction, zero specificity. The metric-aware prompt's
   "lean True under uncertainty when recall is the gate" instruction
   gets taken to the literal extreme. A future smoketest pass should
   use a balanced-accuracy or f1 metric on demand-prediction to catch
   this degenerate strategy. Captured here as a calibration note —
   the single-recall-metric design has this exposure.
4. **gpt-oss:20b — F5-confirmed token exhaustion.** Same pattern:
   model hits `max_tokens` before committing to the tool call. Also
   true on the simpler single-turn task. Deeply structural to this
   model.

**LiteLLM proxy gotchas (now in `infra/litellm/ollama.yaml`):**

- Use `ollama_chat/<model>` provider strings, **not** `ollama/<model>`.
  The `_chat` variant routes through Ollama's `/api/chat`, which is
  the only path that exposes proper tool-call translation in LiteLLM
  1.83.x. Plain `ollama/<model>` uses `/api/generate` and silently
  drops tool definitions, causing the agent to "respond in text only"
  and trip `NoPredictToolUseError`.
- Set `num_ctx: 65536` per model (mitigates F1 — Ollama `/v1` and
  `/api/chat` both default to the model's tokenizer-default ctx, which
  can be lower than 32k on some quants).
- Use `os.environ/OWNEVO_OLLAMA_HOST` for env-var substitution in the
  LiteLLM YAML (not shell `${...}` — LiteLLM doesn't expand shell
  syntax).

**Implication for Phase 3 of the original M5 sweep:** the F4-F12
findings on multi-turn loops still hold (qwen3-coder:30b is the only
viable local M5 driver). The A4.4 single-turn gate uses different
selection criteria — devstral-small-2 isn't on the F5 multi-turn
shortlist but is the best on this lower-bar task. The two tracks
have orthogonal model selection.

---

## Candidate models — Ollama (8B–40B)

Sorted ascending by parameter size. Phase 1 sequence order. Run
each via `--api-format openai --ollama-num-ctx 65536` (after PR #24
merges) — without that flag the conversation truncates mid-run.

| Model | Size |
|---|---|
| `llama3.1:8b` | 8.0B |
| `qwen3:8b` | 8.2B |
| `granite4.1:8b` | 8.8B |
| `ministral-3:8b` | 8.9B |
| `qwen3.5:9b` | 9.7B |
| `gemma3:12b` | 12.2B |
| `phi4-reasoning:latest` | 14.7B |
| `qwen3:14b` | 14.8B |
| `qwen2.5:14b` | 14.8B |
| `mychen76/qwen3_cline_roocode:14b` | 14.8B (tool-tuned) |
| `tom_himanen/deepseek-r1-roo-cline-tools:14b` | 14.8B (tool-tuned, R1 distill) |
| `gpt-oss:20b` | 20.9B |
| `lfm2:latest` | 23.8B |
| `fomenks/devstral-small_cline_roocode-64k:latest` | 23.6B |
| `devstral-small-2:latest` | 24.0B |
| `gemma4:26b` | 25.8B |
| `kwangsuklee/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-GGUF:latest` | 26.9B |
| `qwen3.5:27b` | 27.8B |
| `qwen3.6:27b` | 27.8B |
| `gemma3:27b` | 27.4B |
| `granite4.1:30b` | 28.9B |
| `glm-4.7-flash:latest` | 29.9B |
| `qwen3-coder:30b` | 30.5B (gold standard via LMS Anthropic) |
| `qwen3:30b-instruct` | 30.5B |
| `qwen3:30b-a3b` | 30.5B |
| `gemma4:31b` | 31.3B |
| `nemotron-cascade-2:latest` | 31.6B |
| `qwen3:32b` | 32.8B |
| `Qwq:32b` | 32.8B (reasoning-flavored) |
| `qwen2.5-coder:32b` | 32.8B |
| `qwen3.5:35b-a3b` | 36.0B |
| `qwen3.6:35b-a3b` | 36.0B |

## Candidate models — LM Studio (8B–40B)

Re-query before each Phase 1 batch; LMS catalog changes with
downloads. Run via `--api-format anthropic` (preferred — has cache_read)
or `--api-format openai` (no cache_read; useful only if Anthropic
shim rejects the model).

| Model | Status |
|---|---|
| `qwen/qwen3-coder-30b` | ✅ end-to-end PASS via Anthropic streaming (val_score 0.4642 on synthetic) |
| `granite-4.1-8b` | ⚠️ read-loop stall via Anthropic (F4) |
| `ibm/granite-3.2-8b` | ⚠️ read-loop stall via Anthropic (F4) |
| `qwen/qwen3.6-35b-a3b` | ❌ adapter rejection via Anthropic (retry via direct Ollama) |
| `qwen/qwen3.6-27b` | ❌ adapter rejection via Anthropic (retry via direct Ollama) |
| `mistralai/devstral-small-2-2512` | ❌ adapter rejection via Anthropic (Mistral `[TOOL_CALLS]` shim issue) |
| `openai/gpt-oss-20b` | ⚠️ partial via Anthropic — no write_skill |
| `zai-org/glm-4.7-flash` | ⚠️ partial via Anthropic — read-loop stall |
| `google/gemma-4-26b-a4b` | not yet smoked |
| `gemma-4-31b-it` | not yet smoked (dense 31B, non-thinking) |
| `gemma-4-e4b-it` | not yet smoked (~8B, small MoE) |
| `microsoft/phi-4` | not yet smoked |
| `qwen/qwen2.5-coder-14b` | not yet smoked |
| `qwen2.5-coder-32b-instruct` | not yet smoked |
| `qwopus3.5-27b-v3` | not yet smoked |
| `qwen/qwen3-30b-a3b-2507` | not yet smoked |
| `qwen/qwen3-30b-a3b` | not yet smoked |
| `qwen/qwen3-32b` | not yet smoked |

---

## How to run a smoke (single model, Phase 1)

Set `OWNEVO_LLM_HOST` to your local LLM server (or use `--llm-base-url`
explicitly). Logs and per-run state go under `.temp/runlogs/<run_id>/`,
which is git-ignored.

```bash
RUN_ID="$(date +%Y%m%d-%H%M%S)-<backend>-<slug>-phase1"
RUN_DIR=".temp/runlogs/$RUN_ID"
mkdir -p "$RUN_DIR"

# 1. VRAM pre-flight (abort if >1 model loaded)

# 2. Scratch DB
SLUG="<slug>"
docker exec ownevo-postgres psql -U ownevo -d postgres -c "DROP DATABASE IF EXISTS ownevo_smoke_phase1_$SLUG;"
docker exec ownevo-postgres psql -U ownevo -d postgres -c "CREATE DATABASE ownevo_smoke_phase1_$SLUG;"
OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:54330/ownevo_smoke_phase1_$SLUG \
  uv run --project apps/kernel python -c "
import asyncio, asyncpg, os
from ownevo_kernel.db import migrate
async def main():
    conn = await asyncpg.connect(os.environ['OWNEVO_DATABASE_URL'])
    try: await migrate(conn)
    finally: await conn.close()
asyncio.run(main())
"

# 3. Snapshot pre-state (which models are loaded)
{
  echo '{"ollama_ps":'; curl -s http://$OWNEVO_LLM_HOST:11434/api/ps; echo ','
  echo '"lms_loaded":'; curl -s http://$OWNEVO_LLM_HOST:1234/api/v0/models \
    | python3 -c 'import json,sys; print(json.dumps([m["id"] for m in json.load(sys.stdin).get("data",[]) if m.get("state")=="loaded"]))'
  echo '}'
} > "$RUN_DIR/pre_state.json"

# 4. Run the loop. Pick ONE of the four backend variants:

# 4a) LMS Anthropic streaming — heavy prompt caching, productive default
OWNEVO_LLM_MODEL="<lms-model-id>" \
OWNEVO_M5_DIR=/tmp/m5_synth_smoke \
OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:54330/ownevo_smoke_phase1_$SLUG \
timeout 1200 uv run --project apps/kernel python apps/kernel/scripts/run_improvement_loop.py \
  --workflow-id "m5-bootstrap-phase1-$SLUG" \
  --api-format anthropic \
  --llm-base-url "http://$OWNEVO_LLM_HOST:1234" \
  2>&1 | tee "$RUN_DIR/loop.log"

# 4b) Ollama OpenAI (post PR #24 — pass --ollama-num-ctx 65536)
OWNEVO_LLM_MODEL="<ollama-model-name>" \
OWNEVO_M5_DIR=/tmp/m5_synth_smoke \
OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:54330/ownevo_smoke_phase1_$SLUG \
timeout 1200 uv run --project apps/kernel python apps/kernel/scripts/run_improvement_loop.py \
  --workflow-id "m5-bootstrap-phase1-$SLUG" \
  --api-format openai \
  --llm-base-url "http://$OWNEVO_LLM_HOST:11434/v1" \
  --ollama-num-ctx 65536 \
  2>&1 | tee "$RUN_DIR/loop.log"

# 5. Snapshot post-state + extract summary, then unload model
{
  echo '{"ollama_ps":'; curl -s http://$OWNEVO_LLM_HOST:11434/api/ps; echo ','
  echo '"lms_loaded":'; curl -s http://$OWNEVO_LLM_HOST:1234/api/v0/models \
    | python3 -c 'import json,sys; print(json.dumps([m["id"] for m in json.load(sys.stdin).get("data",[]) if m.get("state")=="loaded"]))'
  echo '}'
} > "$RUN_DIR/post_state.json"

# 6. Unload (Ollama only — LMS REST unload does not work)
curl -s "http://$OWNEVO_LLM_HOST:11434/api/generate" \
  -d '{"model":"<ollama-model-name>","keep_alive":0,"prompt":"","stream":false}' >/dev/null
```

## VRAM pre-flight assertion

Run before every smoke; aborts if more than one model is loaded
across the two backends.

```bash
ollama_loaded=$(curl -s "http://$OWNEVO_LLM_HOST:11434/api/ps" | jq '.models | length')
lms_loaded=$(curl -s "http://$OWNEVO_LLM_HOST:1234/api/v0/models" \
  | jq '[.data[] | select(.state == "loaded")] | length')
total=$((ollama_loaded + lms_loaded))
test "$total" -le 1 || { echo "ABORT: $total total models loaded"; exit 9; }
```

---

## Per-run summary fields (extract once each run finishes)

```json
{
  "run_id": "20260504-141500-ollama-qwen3-coder-30b-phase1",
  "backend": "ollama|lms-anthropic|lms-openai",
  "model": "qwen3-coder:30b",
  "phase": 1,
  "fixture": "synthetic|m5-subset|m5-real",
  "outcome": "PASS|PARTIAL-NO-WRITESKILL|PARTIAL-READ-LOOP-STALL|GATE-SANDBOX-ERROR|FAIL-ADAPTER",
  "val_score": 0.4642,
  "iterations": 19,
  "tool_calls": 18,
  "tool_errors": 11,
  "wall_seconds": 120,
  "input_tokens": 157000,
  "cache_read_tokens": 142000,
  "output_tokens": 14000,
  "notes": "..."
}
```

The session-level model-tracking table lives in
`BL3_MODEL_SMOKE_TODO.md` (untracked — local notes per session).
This guide stays committed; that file is throwaway.

---

## Known gaps / followups

- **F3 (SKILL_FORMAT validation):** add a strict parse on `write_skill`
  insert so malformed agent output surfaces as a clean
  `tool_call_result` error rather than a `sandbox-error` after the
  gate runs. ~30 LOC.
- **No per-run Postgres-state snapshot in the summary.** Today the
  run dir captures stdio + LLM-loaded state. Pulling the iteration
  row + audit entries from the scratch DB into `summary.json` would
  make sweeps fully reproducible without re-running the loop.
- **Sandbox image rebuild after baseline patches.** The Docker image
  (`ownevo-sandbox-m5:0.1.0`) bakes the baseline at build time, so
  baseline-side fixes (e.g., the v1 outlier_handler patch in PR #23)
  don't take effect in the sandboxed path until `make sandbox-image-m5`
  is re-run. In-process baseline picks up changes immediately.
- **F2 model auto-eviction on LMS via REST.** The local `lms` CLI works
  but isn't reachable from a remote run script. A small wrapper that
  SSHes to the LMS host and runs `lms unload` would close the loop.
