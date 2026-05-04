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
- **Unload via REST does not work** in current versions: `keep_alive=0`,
  `/v1/internal/model/unload`, `/api/v0/models/<id>/unload`, and
  `/v0/models/unload` all return success but do nothing. The local
  `lms unload <id>` CLI works; webui works. Sweep accepts that LMS
  may keep multiple models loaded across runs (LRU eviction handles
  VRAM exhaustion).

---

## Sweep methodology

Three phases. **All runs sequential, only one model active at a time.**

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

### F5 — qwen3-coder-30b is the only LMS-Anthropic model that reliably drives the loop (10 runs, 1 PASS)

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
