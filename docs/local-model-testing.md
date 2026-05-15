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
- **Load with context length via REST (verified 2026-05-06):** `POST
  /api/v1/models/load` with body
  `{"model": "<id>", "context_length": 32768, "flash_attention": true,
  "echo_load_config": true}` returns `{"instance_id": "<id>", ...}`.
  Subsequent `/v1/chat/completions` calls **must use the returned
  `instance_id` as the `model` field** — passing the original model id
  routes to whatever instance LMS auto-loaded with default context.
  `run_lmstudio_sweep.sh` does this automatically with a 32k → 16k → 8k
  fallback ladder for VRAM-tight loads.

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
| LMS Anthropic streaming | 157,000 | 142,000 | write_skill, gate ran |
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
the regression-blocking path. **B4.2 (First lift on M5)** achieved
at v10; **B4.3 (First gate-blocked regression)** achieved at v12.

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

**Tracked internally as a follow-up.**

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

**Implication for the product narrative:** B4.2 (first lift) and B4.3
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
`apps/kernel/scripts/run_nl_gen_smoke.sh`. Same metric-aware
prompt the cloud agent gets (per-workflow gate-metric framing block
naming family + target + dominant error mode).

Verdict against the 3 NL-gen fixtures (haiku 4.5, sonnet 4.6, opus 4.7
above the table for cloud comparison):

| backend | model | demand-pred (recall ≥0.50) | credit-risk (balanced_acc ≥0.40) | contract-review (f1 ≥0.75) | wall | cost |
|---|---|---:|---:|---:|---:|---:|
| Anthropic | haiku 4.5 | 0.20 ❌ | 0.25 ❌ | 0.91 | ~60s | $0.10 |
| Anthropic | **sonnet 4.6** (cloud reference) | **0.60** | **0.50** | 0.77 | ~128s | $0.50 |
| Anthropic | opus 4.7 | 0.20 ❌ | 0.42 (thin) | 1.00 | ~100s | $2 |
| Ollama | qwen2.5-coder:32b | 1.00 (always-True) | 0.50 | 0.89 | ~117s | $0 |
| Ollama | qwen3-coder:30b | 0.40 ❌ | 0.25 ❌ | 0.89 | ~90s | $0 |
| Ollama | **devstral-small-2** (24B, local reference) | **0.80** | **0.42** | 0.89 | ~100s | $0 |
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

### F14 — A4.4 OpenAI-compat direct sweep: three backends, 19 models pass 3/3 (2026-05-06/07)

Different infrastructure from F13 (no LiteLLM proxy). The agent solver
now speaks OpenAI `/v1/chat/completions` directly to Ollama and LM Studio
via `AsyncOpenAI`. Three infrastructure fixes were required before any
model could pass:

1. **`tool_choice="required"` (string).** LMS and Ollama reject the object
   form `{"type": "function", "function": {"name": ...}}` with
   `BadRequestError: Invalid tool_choice type: 'object'`. Changed to the
   string `"required"` in `agent_solver.py`.

2. **`DEFAULT_MAX_TOKENS_OPENAI = 8_000` (default), `--max-tokens 10000`
   on the Ollama sweep.** Reasoning/thinking models (Qwq, gemma4,
   phi4-reasoning, qwen3 thinking mode) emit a long preamble before the
   tool call; 1k was exhausted before `predict_label` could fire. 8k is
   now the auto-selected default for the OpenAI path; the new
   `--max-tokens` CLI flag in `nl_gen_smoketest.py` overrides per-call.

3. **LM Studio load API with context fallback (32k → 16k → 8k).** LMS
   loads models at 4k context by default; demand-prediction trajectories
   run ~4.8k tokens so demand-pred returned "—" on every model. Fixed via
   `POST /api/v1/models/load` with `context_length` and routing completions
   to the returned `instance_id`. The sweep script tries 32k → 16k → 8k
   on load failure (OOM / VRAM pressure).

**Three sweeps run across two hosts and two backends:**

| Sweep | Host | Backend | Models | Filtered | 3/3 pass |
|---|---|---|---|---|---|
| Desktop LMS | localhost:1234 | LMS OpenAI-compat | 49 | 42 | 7 |
| Laptop LMS  | localhost:1234     | LMS OpenAI-compat | 77 | 62 | 8 |
| Desktop Ollama | localhost:11434 | Ollama OpenAI-compat | 65 | 51 | 4 |

#### F14a — Desktop LMS (32k context, 8k max-tokens) — 7 / 42

| model | ctx | demand | credit | contract | wall |
|---|---|---:|---:|---:|---:|
| `granite-4.1-8b` | 32k | 1.00 | 0.50 | 0.91 | 33s |
| `google/gemma-4-e4b` | 32k | 0.60 | 0.42 | 0.89 | 34s |
| `ibm/granite-3.2-8b` | 32k | 1.00 | 0.50 | 0.83 | 43s |
| `mistralai/ministral-3-14b-reasoning` | 32k | 1.00 | 0.50 | 0.91 | 47s |
| `qwen/qwen3-32b` | 32k | 0.60 | 0.42 | 0.83 | 96s |
| `qwen2.5-coder-32b-instruct` | 16k | 1.00 | 0.50 | 0.89 | 98s |
| `google/gemma-4-31b` | 32k | 0.60 | 0.42 | 1.00 | 229s |

Full sweep logs: `temp/lmstudio_sweep/20260506-124340/summary.md`
(25-model canonical run) + per-model dirs from the 17-model resume run.

**Notable 2/3-pass on desktop LMS:**

| model | failing workflow | score |
|---|---|---|
| `ibm/granite-4-h-tiny` | contract-review | 0.62 ❌ |
| `microsoft/phi-4` | credit-risk | 0.17 ❌ |
| `mistralai/magistral-small` | contract-review | 0.33 ❌ |
| `liquid/lfm2-24b-a2b` | contract-review | 0.67 ❌ (variance; once passed) |
| `openai/gpt-oss-20b` | credit-risk | 0.25 ❌ (variance; once 0.42) |

#### F14b — Laptop LMS (32k context, 8k max-tokens) — 8 / 62

Different model catalog from desktop (more 4B-class quants and MLX builds).
Many 27B+ models in the catalog wouldn't load on laptop VRAM (load API
returns 0.0s). The 1.7B `qwen3-1.7b` passing 3/3 is the smallest model
to pass any A4.4 gate to date.

| model | demand | credit | contract | wall |
|---|---:|---:|---:|---:|
| `qwen/qwen3-4b-2507` | 1.00 | 0.42 | 0.91 | 152s |
| `openai/gpt-oss-20b` | 0.80 | 0.42 | 1.00 | 197s |
| `qwen3.5-4b` | 0.80 | 0.42 | 0.89 | 304s |
| `nvidia_nvidia-nemotron-nano-9b-v2` | 1.00 | 0.58 | 0.83 | 548s |
| `qwen/qwen3-1.7b` | 0.80 | 0.50 | 0.89 | 826s |
| `qwen/qwen3-4b` | 0.60 | 0.42 | 0.89 | 1855s |
| `qwen/qwen3-8b` | 1.00 | 0.42 | 0.89 | 3141s |
| `qwen/qwen3-14b` | 0.80 | 0.58 | 0.89 | 4734s (79 min) |

Full sweep log: `temp/lmstudio_sweep/20260506-184434/summary.md`.

**Cross-host comparison (same model, both hosts):**

| model | desktop wall | laptop wall | desktop pass | laptop pass |
|---|---:|---:|---|---|
| `openai/gpt-oss-20b` | 36s (0.25 ❌ credit) | 197s (3/3 pass) | ❌ 2/3 | 3/3 |
| `qwen/qwen3-32b` | 96s (3/3 pass) | 0.0s (load failed) | 3/3 | ❌ |

The variance on `gpt-oss-20b` between desktop runs (0.42 → 0.25 ❌)
plus laptop's clean 3/3 suggests it's a credible 3/3 model with run noise
on the credit-risk metric. Counted in laptop totals.

#### F14c — Desktop Ollama (10k max-tokens) — 4 / 51

The 10k max-tokens fix unblocked the thinking models hitting
`stop_reason='length'` at 8k. `Qwq:32b` went from 0.0s NoPredictToolUse
to a full 3/3 in 38 minutes — strong evidence that thinking-flavored
reasoning models need >8k for partial-info classification gates.

| model | demand | credit | contract | wall |
|---|---:|---:|---:|---:|
| `qwen3:8b` | 0.80 | 0.42 | 0.91 | 373s |
| `mychen76/qwen3_cline_roocode:14b` | 0.60 | 0.67 | 1.00 | 629s |
| `qwen3.5:35b-a3b` | 0.60 | 0.58 | 0.91 | 669s (11 min) |
| `Qwq:32b` | 0.60 | 0.50 | 0.83 | 2301s (38 min) |

Full sweep log: `temp/ollama_sweep/20260506-175042/summary.md`.

**Notable 2/3-pass on desktop Ollama:**

| model | failing workflow | score |
|---|---|---|
| `qwen3-coder:30b` | demand-pred | 0.40 ❌ |
| `qwen3:32b` | demand-pred | 0.40 ❌ |
| `qwen3:14b` | demand-pred | 0.40 ❌ |
| `granite4.1:30b` | demand-pred | 0.40 ❌ |
| `gpt-oss:120b` | credit-risk | 0.42 but contract 0.80 / demand 0.40 ❌ (40 min wall) |
| `gemma4:e4b` | demand-pred | 0.40 ❌ |
| `qwen3:30b-instruct` | credit-risk | 0.08 ❌ (weak on credit) |
| `qwen3:4b-instruct` | contract-review | 0.50 ❌ (very fast 40s, demand+credit pass) |

#### F14d — Zero-result categories (not worth re-running)

- **"Does not support tools" (Ollama API 400):** `gemma3:*`, `gemma3n:*`,
  `phi4-reasoning`, `phi4-mini-reasoning`, `olmo-3:7b`, `llama3.x:*`,
  `qwen2.5:*`, `qwen2.5-coder:*` Ollama variants. Hard failures.
- **Doesn't emit tool calls (NoPredictToolUseError, stop_reason='stop'):**
  most LMS reasoning-distilled variants (`qwen3.5-27b-claude-*`,
  `qwopus3.5-27b-v3`, `mlx-qwen3.5-4b-claude-*`), `zai-org/glm-4.7-flash`,
  `crow-4b-opus-4.6-distill-heretic`. Models trained on conversational
  reasoning don't reliably emit OpenAI-format tool calls.
- **OOM at all context sizes (laptop):** `qwen/qwen3-32b`,
  `qwen/qwen3-coder-30b`, `qwen/qwen3.6-27b`, `qwen2.5-coder-32b-instruct`,
  most 26B+ models on laptop VRAM.
- **OOM total (desktop):** `gpt-oss:120b` runs but underperforms
  (35.5 GiB working set, 40 min wall, 2/3 result).

#### F14e — Recommendations by class

**Best small/fast for laptop (≤10B, <5 min wall):**
1. `qwen/qwen3-4b-2507` (4B) — laptop, 152s, 1.00 / 0.42 / 0.91. Best
   speed/quality on laptop. Recommended laptop default.
2. `qwen/qwen3-1.7b` (1.7B) — laptop, 826s. Smallest 3/3 in any sweep —
   useful for headless/edge prototype where 4B doesn't fit.

**Best general-purpose for desktop (32k+ context, 14B–32B class):**
1. `granite-4.1-8b` — desktop LMS, 33s, 1.00 / 0.50 / 0.91. **Fastest 3/3
   model in any sweep.** Recommended desktop default.
2. `mistralai/ministral-3-14b-reasoning` — desktop LMS, 47s, 1.00 / 0.50
   / 0.91. Best 14B-class.
3. `qwen/qwen3.5-9b` — desktop LMS via **Anthropic API** (F14g), 52s,
   0.60 / 0.42 / 0.89. Only passes via `/v1/messages`; 0/3 on OpenAI
   path. Use `--anthropic-base-url http://<host>:1234`.
4. `qwen2.5-coder-32b-instruct` — desktop LMS, 98s, 1.00 / 0.50 / 0.89.
   Best 32B-class for codegen affinity (cf F13's qwen2.5-coder:32b
   Ollama-side 1.00-but-degenerate; LMS run is non-degenerate).

**Best Ollama-side for desktop (with `/no_think` auto-injection from F14i):**
1. `qwen3-coder:30b` — desktop Ollama, 82s, 0.60 / 0.42 / 0.89.
   Fastest desktop Ollama 3/3 by 4×. Replaces `qwen3:8b` (373s) as
   the recommended Ollama desktop default.
2. `qwen3:8b` — desktop Ollama, 373s. Still useful as the 8B-class
   reference (smaller VRAM than 30B coder).

**Best for hybrid (NL-gen frontier + agent local):**
1. `mychen76/qwen3_cline_roocode:14b` — desktop Ollama, 629s, 0.60 / 0.67
   / 1.00. Highest credit-risk score of any local model. Tool-tuned
   variant of qwen3-14b.

**Avoid (deterministic failures, large wall, or no improvement track):**
- `gpt-oss:120b` — 35.5 GiB, 40 min wall, 2/3 result. Cost/perf bad.
- `qwen3:30b-instruct` Ollama — 0.08 credit (very weak). Avoid for
  credit-risk class problems.
- All `gemma3:*` / `gemma3n:*` / `phi4-reasoning:*` Ollama variants —
  Ollama API rejects (no tool-call support exposed).

#### F14f — Workarounds for Ollama "does not support tools" 400 (open follow-up)

13 of the 20 Ollama zero-results in F14c (`gemma3:*`, `gemma3n:*`,
`phi4-reasoning:*`, `olmo-3:7b`, `llama3.1:8b`, `llama3.2:3b`,
`qwen2.5:*`, `qwen2.5-coder:7b/32b`, `tom_himanen/deepseek-r1-roo-cline-tools:14b`,
`fomenks/devstral-small_cline_roocode-64k`,
`granite3.3:8b`, `granite4.1:3b`, `granite4:3b`,
`kwangsuklee/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-GGUF`)
fail with:

```
BadRequestError: 400 - {'error': {'message':
  'registry.ollama.ai/library/<model> does not support tools', ...}}
```

The same underlying weights often pass on LMS (e.g. `gemma-4-31b` 3/3
LMS but the gemma3 line all reject Ollama-side). The 400 is server-side
gating on the model's `Modelfile` `TEMPLATE` block — not a hard
capability limit.

**Three known workarounds, ordered by effort:**

1. **`POST /api/chat` with manual tool-call parsing.** Ollama's `/v1`
   endpoint enforces a tool-template-present check; the native
   `/api/chat` endpoint doesn't and just returns the assistant text. We
   parse tool-call-shaped JSON out of that text. This is what LiteLLM
   already does when proxying — `ollama_chat/<model>` in F13's hybrid
   setup avoids this gate end-to-end. Cost: ~50 LOC adding a
   `predict_one_ollama_native` branch in `agent_solver.py` when the
   base URL is recognized as Ollama.

2. **Edit the Modelfile to add a tool template.** `ollama show <model>
   --modelfile` → append a `TEMPLATE` block with `{{- if .ToolCalls }}`
   handling → `ollama create <model>-tools -f Modelfile`. Most rejected
   models pass after this. Per-model manual work; the new variants
   compete for VRAM.

3. **Pull the LMS GGUF version into Ollama directly.**
   `ollama pull hf.co/<repo>:<quant>` where the GGUF has the tool
   template baked in (LMS-compatible builds usually do). Cleanest when
   the repo allows it — not all HF repos are pull-able.

**Decision:** not blocking the A4.4 sweep — LMS covers most models we'd
want anyway, and the 4 Ollama 3/3 passers (`qwen3:8b`,
`mychen76/qwen3_cline_roocode:14b`, `qwen3.5:35b-a3b`, `Qwq:32b`)
already have `tool_calls` in their Modelfile. Revisit option 1 if
Ollama-only fine-tunes (e.g. `qwen3:4b-instruct`'s 0.83 credit-risk
near-miss) become operationally interesting. Tracked here so future
sweeps don't re-discover the gate.

#### F14g — LMS Anthropic-API retry recovers tool-shy models (2026-05-07)

23 LMS models that hit `NoPredictToolUseError` on the OpenAI path were
retried via LMS's Anthropic-format `/v1/messages` endpoint
(`--anthropic-base-url http://<lms-host>:1234`). Same model weights,
same 32k context, same workflows — only the chat-completion API format
differs.

**Net new desktop pass:**

| model | OpenAI baseline | Anthropic retry | wall |
|---|---|---|---:|
| **`qwen/qwen3.5-9b`** | 0/3 (`stop_reason='stop'`) | **3/3 pass** (0.60 / 0.42 / 0.89) | 52s |

**Partial recoveries (0/3 → 2/3):**

| model | host | demand | credit | contract | wall |
|---|---|---:|---:|---:|---:|
| `qwen/qwen3.6-27b` | desktop | 0.40 ❌ | 0.50 | 0.91 | 123s |
| `google/gemma-4-26b-a4b` | desktop | 0.40 ❌ | 0.42 | 1.00 | 71s |
| `google/gemma-3-12b` | laptop | 1.00 | 0.33 ❌ | 0.83 | 393s |

**Why this works:** Anthropic-format tool use uses `tool_use` content
blocks with structured `input` rather than the OpenAI `tool_calls`
JSON-string-in-arguments shape. Models trained on conversational/Claude
flavored data (Qwen 3.5/3.6, Gemma-4) emit Anthropic-shaped tool calls
more reliably; the OpenAI form is what was tripping
`NoPredictToolUseError`. LMS converts Anthropic input/output to its
internal format on both endpoints, so the underlying inference is
identical.

**Models that did NOT recover under Anthropic API** (still tool-blind
or tool-format-malformed): `deepseek-r1-0528-qwen3-8b`, `gemma-3-1b`,
`phi-4-mini-reasoning`, `qwen2.5-14b-instruct`, `qwen/qwen3-4b-thinking-2507`,
`nvidia/nemotron-3-nano-{4b,omni}`, `omnicoder-9b`, `qwopus3.5-{27b,9b}-v3`,
`qwen3.5-27b-claude-4.6-opus-reasoning-distilled`,
`unsloth/qwen3.6-35b-a3b`, `zai-org/glm-4.7-flash`,
`hugging-quants/llama-3.2-3b-instruct` (calls tool but emits
`value="false"` string instead of bool).

**Cross-host scaling:** `qwen/qwen3.5-9b` is **9× slower on laptop
(486s, 1/3) than desktop (52s, 3/3)**. The Anthropic-API speedup is a
desktop-class signal only — laptop falls back to 8k context due to
VRAM pressure and gets thrashing-bound. Don't promote
laptop/Anthropic recoveries unless the wall is acceptable for the
tier.

**Run a retry sweep:** `temp/retry_lms_anthropic.sh {laptop|desktop}`
loads each model via the LMS REST `/api/v1/models/load` endpoint and
runs the smoketest with `--anthropic-base-url http://<host>:1234`.

#### F14h — Laptop Ollama (10k max-tokens) — 0 / 15 (2026-05-07)

Sweep ran on `localhost:11434` against the laptop's Ollama catalog
(22 model tags; 1 embedding-only filtered, 6 qwen3.x/qwen3.5.x skipped
mid-run after consistent thinking-mode hangs — see workaround note
below). 15 actually completed; **none passed 3/3**, but two reached
2/3 with notably high single-metric scores worth highlighting.

**2/3 passers (laptop Ollama):**

| model | host | demand | credit | contract | wall |
|---|---|---:|---:|---:|---:|
| **`nemotron-3-nano:4b`** | laptop | 0.40 ❌ | **0.75** | **0.91** | 892s |
| `gemma4:e2b` | laptop | 0.40 ❌ | 0.42 | 0.89 | 639s |

`nemotron-3-nano:4b` has the **highest credit-risk balanced-accuracy of
any local 4B-class model in any sweep** (0.75 vs typical 0.42-0.50)
and 0.91 contract-f1. Demand-recall just below the 0.50 threshold at
0.40. A prompt-tightening or `--max-tokens` increase could plausibly
flip demand and make this a competitive laptop pick.

**1/3 passers (laptop Ollama):**

| model | host | passing workflow | wall |
|---|---|---:|---:|
| `gemma4:e4b` | laptop | contract 0.91 | 287s |
| `lfm2:24b` | laptop | credit 0.50 | 51s |
| `rnj-1:latest` | laptop | credit 0.67 | 331s |

**0/3 — tool-reject 400s (Ollama gate, see F14f):** `gemma3:4b`,
`gemma3:12b`, `gemma4:latest`, `granite3.3:8b`, `granite4:3b`,
`llama3.2:1b`, `llama3.2:3b`, `phi4-mini:latest`, `qwen2.5:14b`,
`qwen2.5-coder:14b`. Same family-level gate observed on desktop —
`gemma3:*` and `llama3.x:*` are hard rejections without a Modelfile
TEMPLATE patch.

**Skipped — qwen3.x / qwen3.5.x thinking-mode hang:** `qwen3.5:4b`,
`qwen3.5:9b`, `qwen3.5:latest`, `qwen3:14b`, `qwen3:30b-a3b`,
`qwen3:8b`. Each stalled at 17-20 minutes wall with no workflow
output written and `expires_at` decaying (Ollama saw no tokens for
3-4 min). See **F14h-hang** below for root cause and proposed fix.

##### F14h-hang — qwen3.x / qwen3.5.x thinking trace consumes max_tokens

Reproducer: `qwen3.5:9b` started at 12:22, killed at 12:39. jsonl
size held at 138 bytes (the startup banner) the entire time. Ollama
runner accumulated 45.9s CPU; Metal GPU was active per `expires_at`
but no tokens flowed back to the smoketest.

**Root cause** (Ollama #14502, charmbracelet/crush #2457, Qwen3 docs):
qwen3 / qwen3.5 ships with **thinking mode ON by default**. The
model emits a long `<think>...</think>` reasoning trace before any
tool call. With our `--max-tokens 10000` cap, the thinking trace
consumes the entire budget — model never reaches the
`predict_label` tool call, request hits `max_tokens` silently,
client waits on stream that never produces structured output.

Same model family on **desktop Ollama (localhost:11434)** behaves
differently — `qwen3:8b` passed 3/3 in 373s and `qwen3.5:35b-a3b`
passed 3/3 in 668s. Likely the desktop's pulled tags use a
different Modelfile TEMPLATE (thinking opt-in vs default-on) or
have been re-pulled at a different time. Tag identity isn't a
guarantee of identical config across hosts.

**Three documented mitigations:**

1. **Inject `/no_think` into the system or first user prompt** — Qwen3
   soft-switch, turn-by-turn disable. ~1 line in `agent_solver.py`
   when the model id matches `^qwen3` (or always — `/no_think` is a
   no-op on non-qwen3 models).
2. **Pass `extra_body={"chat_template_kwargs":{"enable_thinking": False}}`**
   on the OpenAI client call. Proper API-level disable. ~5 LOC.
3. **Pull a non-thinking instruct variant** (e.g. `qwen3:4b-instruct`,
   `qwen3:30b-instruct` are both already in the desktop catalog and
   behaved well — neither stalled, both got 2/3).

**Decision:** local-Ollama-on-laptop sweep ends here at 15 models; 0
3/3 passers means there's no laptop-Ollama recommendation to add to
F14e. The qwen3.x retry with `/no_think` is tracked as a follow-up
(would unlock 6 more candidates).

**Run a sweep:** `OWNEVO_OLLAMA_HOST=http://localhost:11434 bash
apps/kernel/scripts/run_ollama_sweep.sh`. The sweep script now
auto-evicts each model between iterations via `keep_alive: 0` so
the next model doesn't co-tenant on VRAM during the prior model's
5-min keep-alive window.

#### F14i — `/no_think` auto-injection unlocks 5 desktop Ollama 3/3 passers (2026-05-07)

`agent_solver.py` now auto-appends `/no_think` to the system prompt
when the model id contains `qwen3` (commit f6b9980). The directive
suppresses Qwen3-family thinking traces that previously consumed the
entire `max_tokens` budget before any tool call landed (root cause of
F14h-hang). Only effective on Ollama builds whose Modelfile TEMPLATE
contains the `IsThinkSet` parser; verified by inspecting `/api/show`
template output (laptop's `qwen3.5:4b` template is 13 chars empty,
laptop builds of `qwen3:8b/14b` ignore the directive in practice,
desktop's same-tag `qwen3:14b` template is 1723 chars and does honor
it).

**Net new desktop Ollama 3/3 passers (all OpenAI-compat path):**

| model | demand | credit | contract | wall | prior result |
|---|---:|---:|---:|---:|---|
| `qwen3:14b` | 0.60 | 0.67 | 1.00 | 551 s | was 2/3 (demand 0.40) |
| `qwen3:30b-a3b` | 1.00 | 0.42 | 1.00 | 786 s | was 1/3 |
| `qwen3:32b` | 0.60 | 0.42 | 1.00 | 1007 s | was 2/3 (demand 0.40) |
| **`qwen3-coder:30b`** | 0.60 | 0.42 | 0.89 | **82 s** | was 2/3 (demand 0.40) |
| `qwen3-coder-next:latest` | 0.60 | 0.42 | 0.89 | 382 s | was 2/3 (demand 0.20) |

`qwen3-coder:30b` at **82 s wall** is the new fast 3/3 candidate on
the Ollama side (4× faster than the next Ollama 3/3 `qwen3:8b` at
373 s). Counts as a real iteration option alongside the LMS desktop
fast trio (`granite-4.1-8b` 33 s, `gemma-4-e4b` 34 s,
`ministral-3-14b-reasoning` 47 s).

**Models the `/no_think` patch did NOT unlock:**

- `qwen3.5:9b` (desktop) — crashed `NoPredictToolUseError stop_reason='length'`
  on credit-risk despite the directive. qwen3.5 lineage embeds
  thinking more deeply than the chat template directive can override.
- `qwen3.5:27b` (desktop) — stuck 28 min, killed; same root cause.
- `qwen3.6:*` (desktop) — skipped without testing; qwen3.6 lineage is
  the next-major qwen-thinking generation, expected to behave like
  qwen3.5.
- `qwen3:30b-a3b-instruct-2507-q4_K_M` — 2/3 (credit 0.33 ❌, demand
  0.60, contract 0.89, **73 s wall**). Faster than non-instruct
  variants (no thinking) but credit just below threshold.
- All laptop qwen3-family (covered in F14h-hang) — laptop Ollama's
  builds either lack the parser entirely (qwen3.5:4b's 13-char
  template) or fail to apply it via OpenAI-compat path (qwen3:8b/14b
  on laptop ignore even with proper template — likely
  Modelfile-build-version difference).

**Implication for F14e recommendations:** desktop now has 4 Ollama
3/3 passers (`qwen3:8b`, `mychen76/qwen3_cline_roocode:14b`,
`qwen3.5:35b-a3b`, `Qwq:32b` from F14c) plus 5 more via `/no_think`
(F14i). `qwen3-coder:30b` joins the desktop iteration shortlist.

#### F14j — Hardware-correlated quality gap on granite-4.1-8b (Apple Metal vs CUDA, 2026-05-07)

Same model file (`unsloth/granite-4.1-8b-gguf` Q4_K_S — verified
identical via `/api/v0/models/granite-4.1-8b` on both LMS instances),
same context length (32k loaded via `/api/v1/models/load`), same
prompt — but **systematic credit-risk gap of ~0.17 points** between
desktop CUDA and laptop Apple Metal:

| run | host | hardware | demand | credit | contract | wall | result |
|---|---|---|---:|---:|---:|---:|---|
| 1 | desktop LMS | RTX 3090 | 1.00 | **0.50** | 0.91 | 33 s | 3/3 |
| 2 | desktop LMS | RTX 3090 | 1.00 | **0.42** | 0.91 | 32 s | 3/3 |
| 3 | laptop LMS | Apple Metal | 0.60 | **0.33 ❌** | 0.77 | 279 s | 2/3 |
| 4 | laptop LMS | Apple Metal | 0.80 | **0.25 ❌** | 0.91 | 284 s | 2/3 |

- **Within-host variance** (run 1 vs 2 on each host): ~0.08 on credit,
  ~0.20 on demand-recall — consistent with non-zero-temperature
  sampling stochasticity.
- **Cross-host gap on credit-risk**: desktop mean **0.46**, laptop
  mean **0.29** — gap of ~0.17 points, larger than within-host noise.
- Threshold sits at 0.40, so the gap is exactly enough to flip
  desktop's reliable pass into laptop's reliable fail.

The numerical drift between llama.cpp's CUDA and Metal Q4_K_S kernels
on borderline classification cases is enough to consistently flip
predictions in one direction. **Granite-4.1-8b is desktop-only;
do not promote it as a laptop pick** despite being the canonical
fastest 3/3 (33 s on desktop LMS).

**Other late-session laptop results (today, all 2/3 with contract or
credit just below threshold):**

| backend | host | model | demand | credit | contract | wall |
|---|---|---|---:|---:|---:|---:|
| LMS | laptop | `granite-4.1-8b` | 0.60 | 0.33 ❌ | 0.77 | 279 s |
| LMS | laptop | `granite-4.1-8b` (run 2) | 0.80 | 0.25 ❌ | 0.91 | 284 s |
| Ollama | laptop | `granite4.1:8b` | 0.60 | 0.58 | 0.73 ❌ | 368 s |
| Ollama | laptop | `qwen3:4b-instruct` | 1.00 | 0.67 | 0.67 ❌ | 264 s |

The pattern is consistent: laptop runs land in the "almost 3/3" zone
where one workflow falls 0.02–0.10 below threshold. Some of this is
fixable via deterministic decoding (`temperature=0`, not yet pinned
in `agent_solver.py`) — would land as a follow-up; tracked here so
future-you knows the laptop scores carry sampling noise on top of
the hardware-kernel drift.

**Decision:** keep desktop-tier recommendations as-is; tag
granite-4.1-8b in F14e as "desktop CUDA only" not "any 8B-class".
Laptop tier remains the F14b 3/3 picks (`qwen/qwen3-4b-2507` 152 s,
`qwen/qwen3-1.7b` 826 s).

#### F14k — F14j re-test: laptop credit-risk gap does not reproduce; treat as boundary noise (2026-05-07 evening)

Re-running `unsloth/granite-4.1-8b` on laptop LMS twice today, plus
adding two sibling Q4_K_M / FP8 quants for comparison, weakens F14j's
"systematic ~0.17 Metal-vs-CUDA gap" claim. Credit-risk on laptop now
clears the 0.40 gate twice in a row instead of failing twice in a row.

| run | host | model | quant | demand | credit | contract | wall | result |
|---|---|---|---|---:|---:|---:|---:|---|
| F14j-3 | laptop LMS | `unsloth/granite-4.1-8b` | Q4_K_S | 0.60 | **0.33 ❌** | 0.77 | 279 s | 2/3 |
| F14j-4 | laptop LMS | `unsloth/granite-4.1-8b` | Q4_K_S | 0.80 | **0.25 ❌** | 0.91 | 284 s | 2/3 |
| F14k-1 | laptop LMS | `unsloth/granite-4.1-8b` | Q4_K_S | 1.00 | **0.50** | 0.91 | 314 s | **3/3** |
| F14k-2 | laptop LMS | `unsloth/granite-4.1-8b` | Q4_K_S | 1.00 | **0.50** | 0.73 ❌ | 281 s | 2/3 |
| F14k-3 | laptop LMS | `lmstudio-community/granite-4.1-8b` | Q4_K_M | 0.40 ❌ | **0.58** | 0.80 | 334 s | 2/3 |
| F14k-4 | laptop LMS | `granite-4.1-8b-fp8` (ibm-granite) | FP8 | — | — | — | — | **load fail** |

- **Laptop credit-risk across 4 unsloth Q4_K_S trials:** 0.33, 0.25,
  0.50, 0.50 — mean ≈ 0.40, sitting exactly on the gate threshold.
  Variance ~0.25 across trials. Not the "consistent failure" F14j
  reported.
- **Q4_K_M sibling outperforms Q4_K_S on credit-risk** (0.58) but
  underperforms on demand-pred recall (0.40 vs 1.00 for Q4_K_S).
  Different quant, different failure mode — not pure quant ranking.
- **FP8 (`torchSafetensors`) is unloadable in LM Studio** — no
  runtime registered for that model format. Infrastructure block,
  not a quality result.
- **Desktop verify run today was invalid:** LMS loader couldn't
  push unsloth/granite-4.1-8b into VRAM at any context (32k → 16k
  → 8k all returned empty), fell through to a stale ctx=4096
  instance, demand-pred prompt then hit `n_keep: 4152 >= n_ctx:
  4096`. Probably another model squatting on RTX 3090 from an
  earlier sweep.

**Revised conclusion:** F14j's "Apple Metal vs CUDA Q4_K_S kernel
drift" hypothesis is **suspect, not falsified**. Laptop credit-risk
on Q4_K_S clusters around the 0.40 gate boundary across 4 trials
(2 sub-gate, 2 at-gate); the F14j sample of 2 fails happened to land
on the low side. Hardware drift may still contribute, but the gap is
inside the per-trial noise, not above it.

**Practical impact on recommendations:**

- Stop calling granite-4.1-8b "desktop-only" in `apps/kernel/README.md`
  and `CLAUDE.md`. The honest framing is "passes 3/3 reliably on
  desktop CUDA; on laptop Apple Metal it sits on the credit-risk
  gate boundary — sometimes 3/3, sometimes 2/3."
- For laptop iteration, prefer `qwen/qwen3-4b-2507` (F14b, stable
  3/3 at 152s). Granite on laptop is a coin flip.
- Determinism follow-up (TODO-24, `temperature=0`) would tighten
  this — current variance is largely sampling stochasticity.

---

#### F15 — qwen3-coder:30b BL.3 lift outcome: Stage D +14.9% non-reproducible (2026-05-08)

**The arc:** during the overnight 2026-05-08 session, qwen3-coder:30b
on Ollama OpenAI produced what looked like the first measured free
local-model lift on real M5 — `val_score 0.330346 → 0.379663 = +14.9%`
on Stage D iter 4, reproduced 3× across 3 independent DBs (Stage D /
30-day v1 / 30-day v2). Closed TODO-19's headline goal at the time.
Re-tested cleanly during the W6 30-day replay (`ownevo_30day_v5`,
2026-05-08 afternoon) and the result **did not reproduce**.

**v5 setup (re-test):** identical to Stage D — Ollama OpenAI,
`qwen3-coder:30b`, `/no_think` auto-injection on (PR #61, the
load-bearing fix from F14i mirrored into the BL.3 OpenAI runner),
PR #67 conversation compaction merged, 48k context. Conditions A+C
(D omitted because `--judge-base-url` isn't wired for cross-format
loop+judge pairing yet).

**v5 result:** killed at 7 iterations after **F6 / `M5SandboxError`
hit 7 of 7 attempts**. Same proposal failure pattern as
`qwen3-coder-30b` on LMS Anthropic (TODO-20: 14/14 deterministic
`_long_frame` failures). The agent generates well-formed feature
diffs (`lag_7`, `month`, `is_weekend`, `lag_60`, etc.) but every one
crashes the M5 pipeline with `status=error`.

**Verdict — F6 is a `qwen3-coder-30b` codegen property, not an
LMS-Anthropic-transport property.** The earlier hypothesis (F6 was
specific to the LMS Anthropic path because the same model on Ollama
OpenAI succeeded in Stage D) is falsified. Stage D's iter-4 lift
was a **lucky outlier** in a low-throughput re-run sequence — 7
sequential `run_improvement_loop.py` invocations is too small a
sample to distinguish "model finds the lift" from "iter 4 happened
to be the one where the model didn't pattern-match on the buggy
lag/rolling class."

**What this means for the local-model lift story on real M5:**

- The **+14.9% claim should be retracted** as "reproducible free
  local-model lift" — it isn't. The Stage D DB still shows the
  number (it's a real audit-logged event), but the substrate is
  not the cause; sample-size variance is.
- Of the local models tested on the BL.3 multi-turn loop against
  real M5, **none currently produces reliable lift**:
  - `qwen3-coder-30b` (LMS Anthropic): F6 14/14
  - `qwen3-coder:30b` (Ollama OpenAI): F6 7/7 (this finding)
  - `devstral-small-2:latest` (Ollama): runnable Python, but
    `run_pipeline` validation rejects every diff (TODO-21 closed)
  - `granite4.1:8b`: em-dashes in code → SyntaxError
  - `qwen2.5-coder:32b`: doesn't trigger tool calls
- The only confirmed lift driver remains **Sonnet 4.6 cloud** (B4.2
  / B4.3 / Stage C compound lift / v6 30-day +23.2% / v7 30-day on
  v2 +0.62%). v6 + v7 contrast makes the loop's actual capability
  ceiling visible: ~textbook-ML-recovery, not novel ML.

**Updates downstream:**

- `CLAUDE.md` already softened the qwen3-coder claim to "produced
  +14.9% in TODO-19 (3× reproduced), but a subsequent W6 v5 run
  hit F6/M5SandboxError 7/7 — generalizability is uncertain
  pending F6 root-cause investigation."
- Internal replay notes record the retraction.

**Open question (TODO):** F6 root-cause investigation — same prompt
to qwen3-coder-30b via both transports, capture both responses,
diff the agent diffs and the failing pipeline code. Tells us whether
F6 is task-shape (M5-specific) or model-property (qwen3-coder
codegen-fundamental). Relevant if the model is ever revisited.

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

| Model | A4.4 gate (LMS OpenAI-compat, 32k ctx) | Loop status (LMS Anthropic streaming) |
|---|---|---|
| `qwen/qwen3-coder-30b` | not swept (16k ctx fallback) | end-to-end PASS via Anthropic streaming (val_score 0.4642 on synthetic) |
| `granite-4.1-8b` | 3/3 (1.00 / 0.50 / 0.91, 33s) | ⚠️ read-loop stall via Anthropic (F4) |
| `ibm/granite-3.2-8b` | 3/3 (1.00 / 0.50 / 0.83, 43s) | ⚠️ read-loop stall via Anthropic (F4) |
| `mistralai/ministral-3-14b-reasoning` | 3/3 (1.00 / 0.50 / 0.91, 47s) | not yet smoked |
| `qwen/qwen3-32b` | 3/3 (0.60 / 0.42 / 0.83, 96s) | not yet smoked |
| `qwen2.5-coder-32b-instruct` | 3/3 (1.00 / 0.50 / 0.89, 98s, 16k ctx) | not yet smoked |
| `google/gemma-4-31b` | 3/3 (0.60 / 0.42 / 1.00, 229s) | not yet smoked |
| `google/gemma-4-e4b` | 3/3 (0.60 / 0.42 / 0.89, 34s) | not yet smoked |
| `ibm/granite-4-h-tiny` | ❌ 2/3 (contract 0.62) | not yet smoked |
| `microsoft/phi-4` | ❌ 2/3 (credit 0.17) | not yet smoked |
| `mistralai/magistral-small` | ❌ 2/3 (contract 0.33) | not yet smoked |
| `liquid/lfm2-24b-a2b` | ❌ 2/3 (contract borderline, run variance) | not yet smoked |
| `qwen/qwen3-30b-a3b` | ❌ 2/3 (credit 0.33, run variance) | not yet smoked |
| `openai/gpt-oss-20b` | ❌ 2/3 (credit variance) | ⚠️ partial via Anthropic — no write_skill |
| `qwen/qwen3.6-35b-a3b` | ❌ 1/3 (demand only) | ❌ adapter rejection via Anthropic |
| `qwen/qwen3.6-27b` | ❌ 0.0s (load failed at all ctx) | ❌ adapter rejection via Anthropic |
| `mistralai/devstral-small-2-2512` | ❌ 1/3 (demand 0.40) | ❌ adapter rejection via Anthropic |
| `zai-org/glm-4.7-flash` | ❌ 0.0s (NoPredictToolUse) | ⚠️ partial via Anthropic — read-loop stall |

---

## How to run an A4.4 sweep (forced-tool gate, OpenAI-compat)

Two sweep scripts cover the two backends. Both call
`scripts/nl_gen_smoketest.py --workflow all --from-fixtures` per model
and write a markdown summary table. Run sequential — never simultaneously
on the same GPU host.

**Two distinct knobs (don't conflate):**

| knob | what it controls | LMS sweep default | Ollama sweep default |
|---|---|---|---|
| `context_length` | **input** context window the model is loaded with | **32k** (via `POST /api/v1/models/load`, fallback 16k → 8k) | server-side `OLLAMA_CONTEXT_LENGTH=65536` (per-request `num_ctx` overrides unreliable, see F1) |
| `max_tokens` | **output** generation cap per API call | 8k (path default in `agent_solver.py`) | **10k** (script passes `--max-tokens 10000`) |

LMS context is set at *load* time (via REST). Ollama output cap is set
at *request* time (via the OpenAI `max_tokens` field). Reasoning models
(Qwq:32b, qwen3-thinking) need a 10k+ **output** budget because they
emit a long preamble before the tool call; that's why Ollama gets the
boost. LMS gets the input-context boost because demand-prediction
trajectories are ~4.8k tokens and LMS would otherwise load at the 4k
default.

### LM Studio (`scripts/run_lmstudio_sweep.sh`)

```bash
# All models on the host, 32k input context + 8k output max.
OWNEVO_LMSTUDIO_HOST=http://localhost:1234 \
  bash apps/kernel/scripts/run_lmstudio_sweep.sh

# Restrict to one model:
OWNEVO_LMSTUDIO_HOST=http://localhost:1234 \
  bash apps/kernel/scripts/run_lmstudio_sweep.sh "qwen/qwen3-4b-2507"

# Override the input context (the value tried first; still falls back
# to 16k/8k if VRAM rejects):
LMS_CONTEXT_LENGTH=65536 \
OWNEVO_LMSTUDIO_HOST=http://localhost:1234 \
  bash apps/kernel/scripts/run_lmstudio_sweep.sh
```

The script:
- Queries `/v1/models` for the catalog, filters embed/whisper/vl/asr.
- Per model: `POST /api/v1/models/load` with `context_length=$LMS_CTX`,
  falling back through `[LMS_CTX, 16384, 8192]` on empty/error response.
- Routes completions to the returned `instance_id`.
- `POST /api/v1/models/unload` after each model to free VRAM.
- Writes per-model JSONL + a summary table to
  `temp/lmstudio_sweep/<timestamp>/`.

### Ollama (`scripts/run_ollama_sweep.sh`)

```bash
# All text-capable models, 10k output max-tokens.
OWNEVO_OLLAMA_HOST=http://localhost:11434 \
  bash apps/kernel/scripts/run_ollama_sweep.sh

# Restrict to one model:
OWNEVO_OLLAMA_HOST=http://localhost:11434 \
  bash apps/kernel/scripts/run_ollama_sweep.sh "Qwq:32b"

# Bump the output cap (e.g. for slow thinking models that need >10k):
OLLAMA_SWEEP_MAX_TOKENS=20000 \
OWNEVO_OLLAMA_HOST=http://localhost:11434 \
  bash apps/kernel/scripts/run_ollama_sweep.sh "Qwq:32b"
```

`OLLAMA_SWEEP_MAX_TOKENS` (default `10000`) is plumbed into the
smoketest as `--max-tokens N`, which overrides
`DEFAULT_MAX_TOKENS_OPENAI = 8000` in `agent_solver.py`. The Ollama
**input** context is configured daemon-side (`OLLAMA_CONTEXT_LENGTH`
+ `OLLAMA_NUM_PARALLEL=1` per F1), not in this script.

### Direct smoketest invocation (single model, no sweep wrapper)

When iterating on a specific model + override:

```bash
uv run --directory apps/kernel --extra agent \
  python scripts/nl_gen_smoketest.py \
    --workflow all --from-fixtures \
    --model "qwen/qwen3-4b-2507" \
    --openai-base-url http://localhost:1234/v1 \
    --max-tokens 10000 \
    --include-outcomes
```

Flags worth knowing:
- `--max-tokens N` — per-call **output** token cap (auto-selects 1k
  Anthropic / 8k OpenAI when omitted; sweeps explicitly set 10k for
  Ollama).
- `--max-tokens-per-workflow N` — A4.5 cumulative input+output budget
  cap; aborts via `TokenBudgetExceededError` when crossed.
- `--openai-base-url URL` — switches the agent solver to `AsyncOpenAI`
  (Ollama / LMS direct). Omit for the default Anthropic path.

For LMS, **input context is not a smoketest flag** — it's set at
load-time via the LMS REST `/api/v1/models/load` endpoint (or `lms load
<model> --context-length N` on the host). The smoketest then talks to
whatever instance LMS routes to.

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

---

## AA Intelligence Index — τ³ retail sweep candidates (2026-05-13)

Source: [Artificial Analysis Intelligence Index v4.0](https://artificialanalysis.ai/models/open-source/small?models=qwen3-6-27b%2Cqwen3-6-35b-a3b%2Cgemma-4-31b%2Cqwen3-6-27b-non-reasoning%2Cqwen3-5-9b%2Cgemma-4-31b-non-reasoning%2Cqwen3-6-35b-a3b-non-reasoning%2Cgemma-4-26b-a4b%2Cqwen3-5-35b-a3b-non-reasoning%2Cexaone-4-5-33b%2Cnemotron-cascade-2-30b-a3b%2Capriel-v1-6-15b-thinker%2Cqwen3-5-9b-non-reasoning%2Cgemma-4-26b-a4b-non-reasoning%2Cgpt-oss-20b%2Cnvidia-nemotron-3-nano-30b-a3b-reasoning%2Cdevstral-small-2%2Cgemma-4-e4b%2Cexaone-4-0-32b-reasoning%2Cministral-3-14b%2Cgemma-4-e2b%2Cnvidia-nemotron-nano-9b-v2-reasoning%2Cgranite-4-1-30b%2Czaya1-8b%2Cgranite-4-1-8b%2Clfm2-24b-a2b%2Cphi-4%2Clfm2-8b-a1b#intelligence-evaluations)

Index v4.0 aggregates 10 evals: GDPval-AA, τ²-Bench Telecom, Terminal-Bench Hard, SciCode, AA-LCR, AA-Omniscience, IFBench, Humanity's Last Exam, GPQA Diamond, CritPt. Lightbulb icon = reasoning mode.

| AA Intel | τ²-Tel | TBH | HLE | IFBench | Model | Reasoning | τ³ retail val_score | Notes |
|---|---|---|---|---|---|---|---|---|
| 46 | 94% | 30% | 18% | 68% | Qwen3.6 27B | ✓ | — | Not on machine |
| **43** | **95%** | **35%** | **22%** | **67%** | **Qwen3.6 35B A3B** | ✓ | **0.750** | Winner. LMS task agent + proposer |
| 39 | ~53% | 36% | 23% | 76% | Gemma 4 31B | ✓ | 🔄 Run B | Expected ~0.60-0.65 |
| 37 | 94% | 24% | 13% | 65% | Qwen3.6 27B | — | — | Non-reasoning |
| 32 | ~85% | 25% | 14% | 65% | Qwen3.5 9B | ✓ | 0.575 | LMS, ctx=65536 |
| 32 | ~53% | 35% | 11% | 72% | Gemma 4 31B | — | 🔄 Run B | Non-reasoning mode |
| 32 | ~87% | 30% | 13% | 67% | Qwen3.6 35B A3B | — | — | Non-reasoning mode |
| 31 | 44% | 26% | 20% | 71% | Gemma 4 26B A4B | ✓ | 0.00 | **Outlier** — high AA + IFBench, 0 retail. max_steps every task. MoE ~4B active insufficient for multi-turn. |
| 31 | ~86% | 14% | 13% | ~46% | Qwen3.5 35B A3B | — | — | Non-reasoning |
| 30 | **69%** | 21% | 12% | 54% | EXAONE 4.5 33B | — | — | Run G queued. High τ²-Tel for AA intel rank |
| 28 | 42% | 21% | 12% | **80%** | Nemotron Cascade 2 30B A3B | ✓ | — | Run F (LMS) + Run I (Ollama/bartowski). **#1 IFBench of all models shown** |
| 28 | **66%** | 17% | 10% | 69% | Apriel-v1.6-15B-Thinker | ✓ | — | Run H queued. 9.66 GB, no swap needed. **Punches above weight on τ²-Tel + IFBench** |
| 27 | ~85% | 11% | 9% | ~64% | Qwen3.5 9B | — | — | Non-reasoning |
| 27 | 40% | ~14% | ~11% | ~45% | Gemma 4 26B A4B | — | 0.00 | Non-reasoning. Same retail failure as reasoning mode. |
| 24 | 60% | 8% | 10% | 58% | gpt-oss-20B (high) | — | 0.300 | LMS task agent |
| 24 | 41% | 14% | 11% | ~45% | NVIDIA Nemotron 3 Nano 30B A3B | ✓ | — | Different from Cascade 2 |
| 19 | 22% | 18% | 3% | ~28% | Devstral Small 2 | — | ~0.33 partial | Full-eval-infeasible (tau2 retry depth) |
| 19 | 21% | 5% | ~4% | ~38% | Gemma 4 E4B | ✓ | — | — |
| 17 | 17% | 2% | ~4% | ~36% | EXAONE 4.0 32B | ✓ | — | — |
| 16 | 23% | 4% | 5% | ~39% | Ministral 3 14B | — | — | — |
| 15 | 21% | ~4% | 5% | ~36% | Gemma 4 E2B | ✓ | 0.00 | Retail-weak (~2B active) |
| 15 | 21% | ~11% | ~4% | ~44% | NVIDIA Nemotron Nano 9B V2 | ✓ | — | — |
| 15 | 31% | 11% | ~4% | 46% | Granite 4.1 30B | — | — | — |
| 14 | 28% | 4% | ~4% | ~44% | ZAYA1-8B | — | — | — |
| 12 | 27% | ~2% | ~4% | ~38% | Granite 4.1 8B | — | — | — |
| 10 | 0% | 0% | 5% | ~31% | LFM2 24B A2B | — | — | — |
| 10 | ~11% | 0% | ~4% | 24% | Phi-4 | — | — | — |
| 7 | 0% | 0% | ~4% | ~26% | LFM2 8B A1B | — | — | — |

_τ²-Tel = τ²-Bench Telecom (agentic tool use, same benchmark family as τ³ retail). TBH = Terminal-Bench Hard (agentic coding). HLE = Humanity's Last Exam (reasoning & knowledge). IFBench = instruction following. Source: [AA Intelligence Index v4.0, 2026-05-13](https://artificialanalysis.ai/models/open-source/small?models=qwen3-6-27b%2Cqwen3-6-35b-a3b%2Cgemma-4-31b%2Cqwen3-6-27b-non-reasoning%2Cqwen3-5-9b%2Cgemma-4-31b-non-reasoning%2Cqwen3-6-35b-a3b-non-reasoning%2Cgemma-4-26b-a4b%2Cqwen3-5-35b-a3b-non-reasoning%2Cexaone-4-5-33b%2Cnemotron-cascade-2-30b-a3b%2Capriel-v1-6-15b-thinker%2Cqwen3-5-9b-non-reasoning%2Cgemma-4-26b-a4b-non-reasoning%2Cgpt-oss-20b%2Cnvidia-nemotron-3-nano-30b-a3b-reasoning%2Cdevstral-small-2%2Cgemma-4-e4b%2Cexaone-4-0-32b-reasoning%2Cministral-3-14b%2Cgemma-4-e2b%2Cnvidia-nemotron-nano-9b-v2-reasoning%2Cgranite-4-1-30b%2Czaya1-8b%2Cgranite-4-1-8b%2Clfm2-24b-a2b%2Cphi-4%2Clfm2-8b-a1b#intelligence-evaluations)._

**Correlation & prediction notes:**
- AA Intel composite predicts τ³ retail reasonably (43→0.75, 32→0.575, 24→0.30). Notable outlier: Gemma 4 26B A4B (index=31, IFBench=71%, retail=0.00) — fails retail due to max_steps, not reasoning or instruction quality.
- **τ²-Bench Telecom is the best direct predictor** (same benchmark family). EXAONE 4.5 33B (69%) and Apriel (66%) rank higher than their AA index suggests.
- **IFBench standout: Nemotron Cascade 2 30B A3B = 80%** — highest of all models, validating the hypothesis that it may show better instruction-following adaptability as a task agent than its AA index (28) implies.
- **Apriel at 69% IFBench and 66% τ²-Tel is exceptional for 15B / 9.66 GB** — no swap needed, high ceiling relative to its size.
- Devstral Small 2: τ²-Tel=22%, IFBench~28%, consistent with ~0.33 retail.
- qwen3.6-35b-a3b: 95% telecom vs 75% retail — ~20pp domain gap expected.

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
