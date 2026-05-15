# Local-Model Testing Guide

How to evaluate local LLM backends (Ollama, LM Studio) for the ownEvo
improvement loop and the single-turn classification gate. The findings
table is cumulative; update it whenever a sweep adds or invalidates a
data point.

There are two distinct tracks:

| Track | What it measures | Script |
|---|---|---|
| **Multi-turn improvement loop** | Can a model drive `read_skill → write_skill → run_pipeline` for many iterations on real data and produce a measurable lift? | `scripts/run_improvement_loop.py` |
| **Single-turn classification gate** | Can a model emit a forced-tool `predict_label(value: bool)` call reliably? | `scripts/nl_gen_smoketest.py --from-fixtures` |

The two are orthogonal: a model that passes the gate may still stall in
the loop, and vice versa. Treat them as separate qualifications.

---

## Why local models

The improvement loop is the heart of ownEvo. A hosted frontier model
(Claude / GPT-4) works for proof-of-concept, but does not cover three
cases that matter for production deployments:

- **Cost.** Long-running replays (30 days of M5 iterations, multi-week τ³
  sweeps) accrete quickly on cloud APIs.
- **Air-gap.** Regulated buyers require fully on-prem inference for the
  improvement loop and for the eval-case generator that mirrors their
  production traces.
- **Reproducibility.** Local inference makes a customer's failure mode
  bit-reproducible for the approval reviewer.

The MVP needs a credible "best local model on the loop" claim, backed
by a sweep that is reproducible from this file.

---

## Backend quick reference

### Ollama — OpenAI-compatible (`/v1/chat/completions`)

Configure the daemon for single-user agent workloads:

| Env var | Recommended | Why |
|---|---|---|
| `OLLAMA_CONTEXT_LENGTH` | `65536` | Daemon-level default. Per-request `num_ctx` overrides are unreliable; pass `--ollama-num-ctx 65536` to the runner for belt-and-braces. |
| `OLLAMA_NUM_PARALLEL` | `1` | Higher values split the daemon context across slots; a single agent ends up with `CONTEXT_LENGTH / NUM_PARALLEL` tokens. |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Prevents VRAM contention; matches "one model loaded at a time" discipline for sweeps. |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | Saves 0.5–1 GB VRAM with neutral throughput. |
| `OLLAMA_FLASH_ATTENTION` | `1` | Neutral throughput; enables KV quantization. |

**Unload pattern:** a no-op `generate` with `keep_alive=0` evicts a
loaded model immediately:

```bash
curl -s "http://$OWNEVO_LLM_HOST:11434/api/generate" \
  -d '{"model":"<id>","keep_alive":0,"prompt":"","stream":false}' >/dev/null
```

**No prompt caching** on Ollama's `/v1` surface — each turn re-sends
the full conversation. Wall-time cost grows with conversation length.

### LM Studio — Anthropic streaming (`/v1/messages`) AND OpenAI (`/v1/chat/completions`)

LM Studio is the productive default for multi-turn agent loops because
its Anthropic shim caches the system prompt and conversation prefix —
`cache_read` is typically 80–90% of input on long runs.

| Surface | When to use |
|---|---|
| `--api-format anthropic` (LMS `/v1/messages`) | Multi-turn loop. Heavy `cache_read`; per-turn cost dominated by output. |
| `--api-format openai` (LMS `/v1/chat/completions`) | Single-turn classification gate. No `cache_read`, but no Ollama-style context splitting either. |

**Load with explicit context length** (verified via REST):

```bash
curl -s "http://$OWNEVO_LLM_HOST:1234/api/v1/models/load" \
  -d '{"model":"<id>","context_length":32768,"flash_attention":true,"echo_load_config":true}'
# returns {"instance_id":"<id>",...}
```

The returned `instance_id` must be used as the `model` field on
subsequent `/v1/chat/completions` calls — passing the original model
id routes to whatever instance LMS auto-loaded with default context.
`run_lmstudio_sweep.sh` handles this automatically with a 32k → 16k →
8k fallback ladder for VRAM-tight loads.

**Adapter rejection failure mode:** LMS's Anthropic shim occasionally
fails on certain models' tool-call format mid-stream
(`APIStatusError: Failed to generate a valid tool call.`). Observed on
Mistral-family models with `[TOOL_CALLS]` native format and some Qwen
variants. Workaround: route via direct Ollama.

**REST unload (LMS 0.4.0+):**

```bash
curl -s "http://$OWNEVO_LLM_HOST:1234/api/v1/models/unload" \
  -d '{"instance_id":"<id>"}'
```

The older `/v0/*` endpoints 404 or no-op silently. The CLI
(`lms load <model> --context-length 65536`) is run on the LMS host
directly.

---

## Confirmed lift drivers (multi-turn improvement loop)

The set of models that have driven the loop end-to-end with a
gate-passing lift on real M5 data:

| Model | Backend | Notes |
|---|---|---|
| **Claude Sonnet 4.6** (cloud) | Anthropic | Reliable; ~$0.30/iteration on the 7-iter M5 replay. Reference upper bound. |
| **`qwen3-coder:30b`** (Ollama, OpenAI format) | Ollama `/v1` | Produced +14.9% lift in one Stage D session (3× reproduced); a later 30-day run hit a deterministic codegen bug 7/7. Generalisability uncertain pending root-cause investigation. Requires `/no_think` auto-injection. |

**Treat any single-driver lift claim as uncertain** until reproduced on
a different DB / different seed. The substrate enforces monotonicity at
the gate, so a successful gate-pass is meaningful; whether a given
model is the *cause* needs replication.

### Models that drive the loop but don't yield lift

| Model | Backend | Failure mode |
|---|---|---|
| `qwen3-coder-30b` | LMS Anthropic | Deterministic `_long_frame` length-mismatch in generated feature code (14/14 attempts). |
| `devstral-small-2:latest` | Ollama | `run_pipeline` validation rejects every diff. |
| `granite4.1:8b` | Ollama | Generates em-dashes (U+2013) in Python → SyntaxError. |
| `qwen2.5-coder:32b` | Ollama | Does not trigger tool calls with `tool_choice=auto`. |

### Configuration that is load-bearing

- **`/no_think` injection** is required for the qwen3-coder family on
  Ollama OpenAI. The runner injects it automatically when the model id
  contains `qwen3`. The `qwen3.5` / `qwen3.6` family embeds thinking
  more deeply than the directive can override; the qwen3-base and
  qwen3-coder branches are unlocked.
- **API format is load-bearing.** `qwen/qwen3.5-9b` is 0/3 on the gate
  via OpenAI but 3/3 via Anthropic `/v1/messages` — the same weights,
  different transport, different result.

---

## Single-turn classification gate (forced-tool)

Forced-tool-use `predict_label(value: bool)` over three canonical
fixtures (demand, credit, contract). A model passes if it returns the
correct boolean on all three fixtures. Run via:

```bash
make nl-gen-smoketest WORKFLOW=all
```

**Top picks:**

| Model | Backend | 3/3 wall time | Notes |
|---|---|---|---|
| `granite-4.1-8b` | LMS OpenAI | ~33 s | Fastest. On laptop Apple Metal sits on the credit-risk boundary — sometimes 3/3, sometimes 2/3. |
| `qwen/qwen3-4b-2507` | LMS OpenAI | ~152 s | Most stable on Apple Metal. Preferred over granite for laptop iteration. |
| `qwen3-coder:30b` | Ollama OpenAI | ~82 s | Fastest desktop Ollama. Requires `/no_think` injection. |
| `mistralai/ministral-3-14b-reasoning` | LMS OpenAI | ~47 s | Stable 3/3. |
| `qwen/qwen3-32b` | LMS OpenAI | ~96 s | Stable 3/3. |
| `qwen2.5-coder-32b-instruct` | LMS OpenAI | ~98 s | 16k ctx fallback. |
| `google/gemma-4-31b` | LMS OpenAI | ~229 s | Stable but slow. |

19+ models pass 3/3 in total across desktop LMS, laptop LMS, and
desktop Ollama; the table above is the working short list, not the
full set.

**Models that fail the gate:**

| Model | Failure |
|---|---|
| `ibm/granite-4-h-tiny` | 2/3 (contract 0.62) |
| `microsoft/phi-4` | 2/3 (credit 0.17) |
| `mistralai/magistral-small` | 2/3 (contract 0.33) |
| `qwen/qwen3.6-27b` | Load failed at every context length |
| `mistralai/devstral-small-2-2512` | 1/3 (demand 0.40) |
| `zai-org/glm-4.7-flash` | NoPredictToolUse |

---

## Sweep methodology

Three phases. **All runs sequential, one model loaded at a time.**

### Phase 0 — pre-flight probes (~90 s/model)

Triage the candidate list before paying for the full sandboxed-loop
run. Catches API-level rejection, missing tool calls, em-dash /
smart-quote regressions in codegen.

```
scripts/probe_tool_calling.py     single-turn read_skill call (~30 s)
scripts/probe_skill_quality.py    1-line skill modification (~60 s)
scripts/sweep_probes.py           batch driver over a <backend> <model> list
```

`sweep_probes.py` writes JSONL + markdown summary, resumable via
`--skip-completed`. Per-probe timeouts (120 s tool-calling, 240 s
skill-quality) bound a hung model.

### Phase 1 — sandboxed-loop smoke (~10 min/model)

Run the improvement loop against a scratch DB with the synthetic M5
fixture. Pass = at least one successful `write_skill` + a gate-passing
val_score.

### Phase 2 — real M5 replay (~30 min – 4 hr/model)

Only after Phase 1 passes. Run the 7-iter or 30-day replay against
real M5 data to measure lift.

---

## Running a classification-gate sweep (forced-tool)

Two sweep scripts cover the two backends. Both call
`scripts/nl_gen_smoketest.py --workflow all --from-fixtures` per model
and write a markdown summary table. Run sequential — never simultaneously
on the same GPU host.

**Two distinct knobs (do not conflate):**

| Knob | Controls | LMS default | Ollama default |
|---|---|---|---|
| `context_length` | **Input** context window at load time | 32k (via `POST /api/v1/models/load`, fallback 16k → 8k) | Daemon-level `OLLAMA_CONTEXT_LENGTH=65536` |
| `max_tokens` | **Output** generation cap per API call | 8k (default in `agent_solver.py`) | 10k (`--max-tokens 10000`) |

Reasoning models (`Qwq:32b`, qwen3-thinking) need the 10k+ output
budget because they emit a long preamble before the tool call. LMS
gets the input-context boost because demand-prediction trajectories
are ~4.8k tokens and LMS would otherwise load at the 4k default.

### LM Studio

```bash
# All models on the host, 32k input context + 8k output max
OWNEVO_LMSTUDIO_HOST=http://localhost:1234 \
  bash apps/kernel/scripts/run_lmstudio_sweep.sh

# Restrict to one model
OWNEVO_LMSTUDIO_HOST=http://localhost:1234 \
  bash apps/kernel/scripts/run_lmstudio_sweep.sh "qwen/qwen3-4b-2507"

# Override input context (still falls back to 16k/8k if VRAM rejects)
LMS_CONTEXT_LENGTH=65536 \
OWNEVO_LMSTUDIO_HOST=http://localhost:1234 \
  bash apps/kernel/scripts/run_lmstudio_sweep.sh
```

### Ollama

```bash
# All text-capable models, 10k output max-tokens
OWNEVO_OLLAMA_HOST=http://localhost:11434 \
  bash apps/kernel/scripts/run_ollama_sweep.sh

# Restrict to one model
OWNEVO_OLLAMA_HOST=http://localhost:11434 \
  bash apps/kernel/scripts/run_ollama_sweep.sh "qwen3-coder:30b"

# Bump output cap (e.g. for thinking models needing >10k)
OWNEVO_MAX_TOKENS=20000 \
OWNEVO_OLLAMA_HOST=http://localhost:11434 \
  bash apps/kernel/scripts/run_ollama_sweep.sh
```

---

## Running a single-model loop smoke (Phase 1)

Set `OWNEVO_LLM_HOST` to your local LLM server (or pass `--llm-base-url`
explicitly). Logs and per-run state go under `.temp/runlogs/<run_id>/`,
which is gitignored.

```bash
RUN_ID="$(date +%Y%m%d-%H%M%S)-<backend>-<slug>-phase1"
RUN_DIR=".temp/runlogs/$RUN_ID"
mkdir -p "$RUN_DIR"

# 1. VRAM pre-flight — abort if more than one model is loaded
ollama_loaded=$(curl -s "http://$OWNEVO_LLM_HOST:11434/api/ps" | jq '.models | length')
lms_loaded=$(curl -s "http://$OWNEVO_LLM_HOST:1234/api/v0/models" \
  | jq '[.data[] | select(.state == "loaded")] | length')
total=$((ollama_loaded + lms_loaded))
test "$total" -le 1 || { echo "ABORT: $total total models loaded"; exit 9; }

# 2. Scratch DB
SLUG="<slug>"
docker exec ownevo-postgres psql -U ownevo -d postgres \
  -c "DROP DATABASE IF EXISTS ownevo_smoke_phase1_$SLUG;"
docker exec ownevo-postgres psql -U ownevo -d postgres \
  -c "CREATE DATABASE ownevo_smoke_phase1_$SLUG;"

# 3. Run the loop — pick ONE of the two backend variants

# 3a) LMS Anthropic streaming (productive default for multi-turn)
OWNEVO_LLM_MODEL="<lms-model-id>" \
OWNEVO_M5_DIR=/tmp/m5_synth_smoke \
OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:54330/ownevo_smoke_phase1_$SLUG \
timeout 1200 uv run --project apps/kernel \
  python apps/kernel/scripts/run_improvement_loop.py \
  --workflow-id "m5-bootstrap-phase1-$SLUG" \
  --api-format anthropic \
  --llm-base-url "http://$OWNEVO_LLM_HOST:1234" \
  2>&1 | tee "$RUN_DIR/loop.log"

# 3b) Ollama OpenAI (pass --ollama-num-ctx 65536 explicitly)
OWNEVO_LLM_MODEL="<ollama-model-name>" \
OWNEVO_M5_DIR=/tmp/m5_synth_smoke \
OWNEVO_DATABASE_URL=postgresql://ownevo:ownevo@localhost:54330/ownevo_smoke_phase1_$SLUG \
timeout 1200 uv run --project apps/kernel \
  python apps/kernel/scripts/run_improvement_loop.py \
  --workflow-id "m5-bootstrap-phase1-$SLUG" \
  --api-format openai \
  --llm-base-url "http://$OWNEVO_LLM_HOST:11434/v1" \
  --ollama-num-ctx 65536 \
  2>&1 | tee "$RUN_DIR/loop.log"

# 4. Unload (Ollama)
curl -s "http://$OWNEVO_LLM_HOST:11434/api/generate" \
  -d '{"model":"<ollama-model-name>","keep_alive":0,"prompt":"","stream":false}' >/dev/null
```

### Per-run summary shape

Extract the following at the end of each run so sweeps are
post-hoc comparable:

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

---

## Model-selection signals from public benchmarks

When picking a candidate for a new sweep, public benchmark scores
correlate well enough to be useful — particularly for τ³-style
multi-turn tool use.

[Artificial Analysis Intelligence Index v4.0](https://artificialanalysis.ai/models/open-source/small)
aggregates 10 evals. The two most predictive signals for ownEvo's
target benchmarks:

- **τ²-Bench Telecom** — best direct predictor for τ³-bench retail
  (same benchmark family). Models scoring 60–95% on τ²-Tel correlate
  with 0.5–0.75 retail val_score.
- **IFBench** — instruction following. Useful for proposer-side
  candidates because a proposer that ignores the "one change per
  iteration" rule wastes gate runs.

The AA index composite is a reasonable first filter (cutoff around
index ≥ 24 for τ³-retail viability), with one notable outlier so far:
Gemma 4 26B A4B scores index=31 / IFBench=71% but produces 0.00 on τ³
retail — it hits `max_steps` on every task. The hypothesis is that an
MoE model with ~4B active parameters is insufficient for the multi-turn
state tracking the retail benchmark requires.

---

## Known gaps

- **Strict SKILL_FORMAT validation on `write_skill`.** Malformed agent
  output currently surfaces as a `sandbox-error` after the gate runs.
  A parse step at insert time would catch it as a clean `tool_call_result`
  error and save a gate cycle.
- **Postgres-state snapshot in the per-run summary.** Today the run
  directory captures stdio + the LLM-loaded model state. Pulling the
  `iterations` row + audit entries from the scratch DB into
  `summary.json` would make sweeps fully reproducible without
  re-running the loop.
- **Sandbox image rebuild after baseline patches.** The Docker image
  (`ownevo-sandbox-m5:0.1.0`) bakes the baseline at build time, so
  baseline-side fixes do not take effect in the sandboxed path until
  `make sandbox-image-m5` is re-run.
- **LMS REST auto-unload from a remote sweep script.** The `lms` CLI
  works on the LMS host but is not reachable remotely; a small SSH
  wrapper would close the loop.
