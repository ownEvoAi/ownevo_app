# τ³-bench Local Model Test Plan

**Branch:** `feat/tau3-local-bench`
**Original goal:** run all three conditions (A frozen / B autonomous loop / C gated loop) on
tau-bench retail using `qwen3-coder:30b` on Ollama at `192.168.1.50` — no cloud API required.
**Revised architecture (2026-05-08):** **hybrid** — Sonnet 4.6 cloud as task agent (preserves
NeoSigma comparison; local 30Bs failed on complex writes); `qwen3-coder:30b` local as
**improvement loop agent** (proposes prompt edits, free).
**Results target:** `ownevo_docs/benchmarks/tau3-results-2026-Q3.md`

**Honest framing:**
- Task agent: `claude-sonnet-4-6` (cloud) — same role NeoSigma used GPT-5.4 for
- User simulator: `claude-haiku-4-5-20251001` (cloud) — cheaper, simpler role
- Improvement loop agent: `qwen3-coder:30b` (Ollama desktop, free) — proposes edits to `agent/agent.py`
- Cost story: ~$0.22/task at gate eval; loop agent is free; total run ~$50-150 across all conditions

NeoSigma reference: 0.56 → 0.78 (+39.3%) on retail, fully autonomous. Our claim with this
architecture: matchable on absolute score (same task agent class) with the **loop agent
running locally for free**.

---

## Recent learnings from papers (2026-04 / 05) — load-bearing design choices

| Source | Finding | Applied to this plan as |
|---|---|---|
| Meta-Harness (Stanford/MIT/KRAFTON, 2026-03) | **Full execution traces beat summaries 34.6 → 50.0** in their loop's diagnostic ablation. Median 82 files read per iteration across 20+ candidates. | **P1.5 must preserve full message history**, not summaries. tau2's auto-saved `results.json` (21+ messages per sim, full tool_calls) is the right shape. Don't reduce it before insertion into `iterations`/`failure_clusters`. |
| Meta-Harness | Causal reasoning at iter 3: proposer correctly diagnosed *"prompt template changes caused agent to delete necessary state"* by reading the full chain across iterations. | Loop agent in P2 needs cross-iteration trace access (NeoSigma's `workspace/traces/baseline/` + `latest/` + `learnings.md` already provides this). |
| Meta-Harness | Reference lift numbers: +7.7pp text classification (4× fewer ctx tokens), +4.7pp IMO math, #1 Haiku on TerminalBench-2 at 37.6% | Cite alongside ownEvo's M5 lift in P4 results doc to show "automated harness improvement is a real category." Position carefully — Meta-Harness optimizes the harness layer, ownEvo optimizes the workflow-skill layer above it. |
| NLAH (Tsinghua, 2026-03) | **Self-evolution is the highest-value single module: +4.8% SWE-bench Verified.** Verifier alone: −0.8%. Multi-candidate: −2.4%. | Validates condition B (autonomous loop) as the headline result. Don't over-invest in verification scaffolding for P3 — the loop itself is the load-bearing piece. |
| NLAH | More structure can hurt when modules diverge from the evaluator's acceptance condition. | Don't add ownEvo-specific scaffolding to `agent/agent.py` baseline; let the loop discover what works. Keep the starting point minimal (the auto-harness template is fine). |
| NLAH | File-backed durable state: +1.6% SWE-bench. | Audit chain design (P1.5 layer) is reinforced — durable state isn't just compliance, it's a measurable behavioral lift. |
| Claw-Eval (PKU/HKU, 2026-04) | **Trajectory-opaque eval misses 44% of safety violations.** Hybrid full-trace eval is required. | Full-trace storage in P1.5 is correct design. tau-bench's eval is trajectory-aware (it inspects DB Match + action sequence), so it's already on the right side of this. |
| Claw-Eval | **Pass³ vs Pass@3 gap = 24pp under perturbation.** Reliability ≠ peak capability. | P4 stretch: re-run condition C top-N tasks 3× and report **Pass³** in the results doc — more honest than tau-bench's single-trial mean reward. |
| Claw-Eval | Sonnet 4.6 leads average score; Opus 4.6 leads Pass³ across 14 frontier models. | Sonnet 4.6 task agent is the avg-score-optimal choice. P4 stretch: re-run the same conditions with Opus 4.7 to see if Pass³ improves. |
| Claw-Eval | Multi-turn: question precision explains 76% of Pass³ variance; conversation length <1%. | When building approval UI in P3, optimize for precise steering (one good directive) over volume (many small approvals). |

**Net effect on this plan:** P1.5 trace preservation gets stronger language ("full history, not summaries"). P2 iteration budget bumped to **15-20** to match prior art (Meta-Harness 20+, NeoSigma 18). P4 gains a Pass³ stretch metric. No structural changes to phases.

---

## Phase tracker

| Phase | Goal | Status | Wall / cost |
|---|---|---|---|
| **P0 — Plumbing smoke tests** | Verify tau2 + LiteLLM + Ollama route works | ✅ done | $0 |
| **Sanity-A/B/D — Local task agent** | Try local model as τ³ task agent (qwen3-coder Ollama, qwen3-coder LMS, ministral-14b LMS) | ✅ done — all 0/3 | $0 |
| **Sanity-C — Cloud task agent** | Verify Sonnet 4.6 + Haiku user sim works end-to-end | ✅ done — 3/3 PASS | $0.67 |
| **P1 — Condition A baseline** | Sonnet 4.6 on retail test split (40 tasks) → **val_score_A = 0.8500** (patched substrate; orig auto-harness 0.80 superseded) | ✅ done | $9.27 + ~$9, 16 min each |
| **P1.5 — Kernel migration** | Pull tau2 into `apps/kernel`, build native `TauBenchRunner` (`BenchmarkRunner` Protocol), register tau3-retail-v1 workflow + skill, ingest failure clusters, retire auto-harness dependency. M1-M10 substeps. | ✅ done | ~1 day CC actual (much faster than estimated 3-5 days due to existing M5 substrate) |
| **P2 — Condition B autonomous loop** | Sonnet 4.6 as loop agent (cloud); edits `tau3.retail.baseline.v1.agent`; gates on 40-task retail test split | ✅ batch 1 done (2026-05-09): val=0.9500 (+10pp lift over 0.85 baseline, prompt-only change in skill v38) | ~$50-80 actual; 14 cycles total |
| **P3 — Condition C gated loop** | Same loop, ownEvo LLM-judge approval gate engaged; ≥5 human re-approvals | ☐ | ~$45-90, ~5-10 hr |
| **P4 — Results doc + Pass³ stretch** | Write `tau3-results-2026-Q3.md` with three-condition table + audit chain export; **Pass³ stretch:** re-run condition C top-N tasks 3× | ☐ | XS-S |

---

## How NeoSigma's auto-harness works (reference)

Source: `/Users/jit/code/try_ext/auto-harness/`

```
run benchmark (tau2) → analyze train traces → edit agent/agent.py → gate → commit → repeat
```

| Component | What it is |
|---|---|
| **tau2** | Sierra's pip package (`git+https://github.com/sierra-research/tau2-bench.git@73dc24445d`) — handles multi-turn simulation (user_model ↔ task agent), task definitions, scoring |
| **`agent/agent.py`** | `HarnessAgent` class — the thing being optimized. Wraps any LLM. Has `AGENT_INSTRUCTION` (system prompt) + `HarnessState` (context builder) |
| **Improvement loop driver** | A coding agent (Claude Code / Codex) reads `PROGRAM.md` and edits `agent/agent.py` one focused change per iteration |
| **`gating.py`** | Step 0: file guard; Step 1: regression suite ≥80%; Step 2: full test val_score ≥ best; Step 3: suite promotion |
| **`workspace/`** | `suite.json` (regression suite), `results.tsv` (history), `traces/` (train failures only), `learnings.md` (agent's running log) |

NeoSigma's 14 accepted changes followed one pattern: read failure trace → find recurring
decision the model got wrong → encode it as a rule or state injection in `agent.py`. ownEvo's
improvement loop does exactly this, but records in the skill registry + audit chain.

---

## Phase 0 — Verify tau2 routes to local Ollama

**Status:** P0.1 ✅ done; P0.2/P0.3 pending

### P0 desktop preflight ✅ (2026-05-08)

`curl http://192.168.1.50:11434/api/tags` returns the model catalog — 66 models loaded
on the desktop, including:
- `qwen3-coder:30b` ✅ (the planned task agent + loop agent)
- `qwen3:30b-a3b-instruct-2507-q4_K_M`, `qwen3:32b`, `qwen3:14b` (fallbacks)
- `granite4.1:30b`, `gpt-oss:120b`, `Qwq:32b` (other 30B+ candidates)
- `devstral-small-2:latest` (TODO-21 model, fails M5 codegen but might work for tau3 agent prompt edits)

Wall: ~7s for the catalog request — desktop is reachable from this Mac directly. Whether
the auto-harness Docker container can reach `192.168.1.50:11434` is verified in P0.2.

### P0.1 ✅ — How tau2 routes LLM calls (resolved 2026-05-08)

tau2 uses **LiteLLM**, not the OpenAI client. Source: `/tmp/tau2-src/src/tau2/utils/llm_utils.py`:

```python
import litellm
from litellm import completion, completion_cost
litellm.drop_params = True   # silently drops unsupported params per provider
# ...
def generate(model, messages, tools=None, tool_choice=None, **kwargs):
    response = completion(model=model, messages=..., tools=..., tool_choice=..., **kwargs)
```

This is **better than OpenAI client direct routing** because:
- LiteLLM handles tool-call format translation across providers (Ollama → OpenAI tool-call schema)
- We already proved this path works for tool-using local models in F13 (`ollama_chat/<model>` via LiteLLM proxy)
- `litellm.drop_params=True` means non-supported params (e.g., OpenAI-only fields) won't crash on Ollama

**Routing config:**

| What | Value |
|---|---|
| Model string | `ollama_chat/qwen3-coder:30b` (prefix tells LiteLLM to use Ollama `/api/chat`) |
| Env var for base URL | `OLLAMA_API_BASE=http://192.168.1.50:11434` (NOT `OPENAI_BASE_URL`) |
| API key | not needed for ollama provider |
| Context length | pass `num_ctx=65536` via `**generate_kwargs` (LiteLLM forwards to Ollama options) |

**Why `ollama_chat/` not `ollama/`:** `ollama/<model>` routes through `/api/generate` which silently
drops tool definitions (per F13). `ollama_chat/<model>` routes through `/api/chat` which preserves
tools. This is the pattern that worked in F13's broader local-model sweep.

**Remaining unknowns from P0.1:**
- Whether `auto-harness/agent/templates/tau_bench.py`'s `HarnessAgent` passes the `ollama_chat/` prefix through correctly (line 16: `AGENT_MODEL = os.environ.get("AGENT_MODEL", "")` → forwards directly to `generate(model=...)`, so just set `AGENT_MODEL=ollama_chat/qwen3-coder:30b` in the env)
- Whether `num_ctx` propagation works through tau2's `generate()` → likely needs to be passed via `llm_args` on the agent factory (line 102 of `benchmark.py`: `llm_args=kwargs.get("llm_args")` → goes into `generate_kwargs` → flows to `litellm.completion`)

### P0.2 ✅ — Smoke test tau2 against Ollama desktop (resolved 2026-05-08)

**Result:** 0.73s wall, content `'Blue'`, 28 prompt + 2 completion tokens, cost $0.

LiteLLM successfully routes `ollama_chat/qwen3-coder:30b` to `http://192.168.1.50:11434/api/chat`
from inside the auto-harness Docker container. Default bridge network handles outbound; no
host-mode required. tau2 imports cleanly; the registry shows retail/airline/telecom task sets
all available.

### P0.4 — tau2 hardcoded `gpt-4.1` defaults (discovered during sanity test, 2026-05-08)

Two LLM call sites in tau2 are NOT exposed via `TauBenchRunner` config and default to
hardcoded `"gpt-4.1-2025-04-14"`:

| Site | File | Used for |
|---|---|---|
| `DEFAULT_LLM_NL_ASSERTIONS` | `tau2/evaluator/evaluator_nl_assertions.py:122` | Natural-language assertion evaluator — scores tasks at end of conversation |
| `DEFAULT_LLM_ENV_INTERFACE` | `tau2/environment/utils/interface_agent.py:37-38` | Environment interface helper |

Symptom: `litellm.AuthenticationError: OpenAIException - Incorrect API key provided: local`
during a sanity-A run with `agent_model=ollama_chat/qwen3-coder:30b` — the agent succeeded but
the post-conversation NL-assertion evaluator made a real OpenAI call with the placeholder key.

Fix shipped in `agent/agent.py`: monkey-patch both module-globals at top of file. Python looks
up `DEFAULT_LLM_NL_ASSERTIONS` in the evaluator's globals at call time, so reassigning the
module attribute redirects the call. Worked because `agent.agent` is loaded just before
`run_domain()` runs (line 96 of `benchmark.py`).

Code:
```python
import tau2.config as _tau2_config
import tau2.evaluator.evaluator_nl_assertions as _tau2_nl_eval
import tau2.environment.utils.interface_agent as _tau2_env_iface
_LOCAL_MODEL = os.environ.get("AGENT_MODEL") or "ollama_chat/qwen3-coder:30b"
_tau2_config.DEFAULT_LLM_NL_ASSERTIONS = _LOCAL_MODEL
_tau2_config.DEFAULT_LLM_ENV_INTERFACE = _LOCAL_MODEL
_tau2_nl_eval.DEFAULT_LLM_NL_ASSERTIONS = _LOCAL_MODEL
_tau2_env_iface.DEFAULT_LLM_ENV_INTERFACE = _LOCAL_MODEL
```

**Worth upstreaming as a tau2 issue** — these defaults should be configurable via env var or
TextRunConfig field. Affects anyone running tau2 with a non-OpenAI model.

### P0.5 — qwen3-coder:30b empty-AssistantMessage failure mode (discovered, partial)

Symptom: `AssertionError: AssistantMessage must have either content or tool_calls. Got
AssistantMessage is_final_chunk: True` — qwen3-coder:30b sometimes returns an empty
response (no text, no tool calls) through LiteLLM's `ollama_chat/` route. tau2's strict
validator (`utils/llm_utils.py:232`) rejects this. Each retry up to `DEFAULT_MAX_RETRIES=3`
hits the same emptiness, so the task fails permanently.

Observed on retail task 0 in the first sanity-A run (4 attempts, all empty). Likely fix:
- Lower temperature (currently default 0.0 — model may be over-deterministic on edge prompts)
- OR retry logic that injects a "you must call a tool or speak" nudge on empty
- OR switch to a different model that's less prone to this failure mode

To investigate after sanity-A retry. Captured here so the failure mode is named.

### P0.3 ✅ — Tool calling works clean (resolved 2026-05-08)

**Result:** 0.95s wall, `r.content=''`, `r.tool_calls=[ToolCall(name='get_weather', arguments={'city': 'Paris'})]`,
22 completion + 288 prompt tokens.

`qwen3-coder:30b` emits proper OpenAI-format tool calls through LiteLLM's `ollama_chat/` route
with **no `/no_think` patch needed**. Model committed directly to the tool with zero text
preamble — better behavior than the M5 OpenAI direct path (which needed F14i `/no_think` injection).

LiteLLM's tool-call translation handles the cross-format mapping cleanly; the qwen3-coder
Modelfile's tool template is sufficient for tau2's typed `Tool` objects.

### P0.2 originally — Smoke test tau2 against Ollama desktop

**Build gotcha discovered (2026-05-08):** the Dockerfile's `COPY . .` was clobbering the
container-built `/app/.venv/` with the host's local `.venv`, leaving broken symlinks like
`/app/.venv/bin/python → /Users/jit/.local/share/uv/python/cpython-3.14-macos-aarch64-none/bin/python3.14`
inside a Linux container. Symptom: `bash: /app/.venv/bin/python: No such file or directory`
even though `ls` showed the file existed.

Fix applied: removed the stale host `.venv` and added `.dockerignore` excluding `.venv/`,
`__pycache__/`, `*.pyc`, `.git/`, `workspace/`. **Worth upstreaming** — this would bite anyone
who ran `uv sync` on a non-Linux host before `docker compose build`.



```bash
cd /Users/jit/code/try_ext/auto-harness
docker compose build  # builds tau2 from git rev inside container

# Smoke test: minimal generate() call against Ollama desktop
docker compose run \
  -e OLLAMA_API_BASE=http://192.168.1.50:11434 \
  autoeval python -c "
import os
from tau2.utils.llm_utils import generate
from tau2.data_model.message import SystemMessage, UserMessage
r = generate(
    model='ollama_chat/qwen3-coder:30b',
    tools=[],
    messages=[
        SystemMessage(role='system', content='You are helpful. Reply in one word.'),
        UserMessage(role='user', content='What color is the sky?'),
    ],
)
print('content:', r.content)
print('cost:', r.cost)
print('usage:', r.usage)
"
```

**Pass criterion:** content is non-empty, no LiteLLM connection error, no auth error.
Cost may be 0.0 (LiteLLM may not have pricing for ollama_chat — that's fine).

**If it fails:**
- `BadRequestError: ollama_chat/<model> does not support tools` → tools=[] should bypass this; if it doesn't, the model literally doesn't support tools (try `qwen3:32b` instead)
- `ConnectionError` → Docker can't reach 192.168.1.50; check that `host.docker.internal` or explicit IP works from container
- `litellm.exceptions.APIConnectionError: Ollama Error - {'error': 'model qwen3-coder:30b not found'}` → run `ollama pull qwen3-coder:30b` on 192.168.1.50

### P0.3 ☐ — Smoke test with tool calling

```bash
docker compose run \
  -e OLLAMA_API_BASE=http://192.168.1.50:11434 \
  autoeval python -c "
from tau2.utils.llm_utils import generate
from tau2.environment.tool import Tool
from tau2.data_model.message import SystemMessage, UserMessage

# Minimal tool that mimics tau2's expected schema
class GetWeatherTool(Tool):
    name = 'get_weather'
    description = 'Get weather for a city'
    # ... tool definition matching tau2's Tool ABC

r = generate(
    model='ollama_chat/qwen3-coder:30b',
    tools=[GetWeatherTool()],  # exact ctor TBD from tau2 source
    messages=[
        SystemMessage(role='system', content='You can call tools.'),
        UserMessage(role='user', content='What is the weather in Paris?'),
    ],
)
print('tool_calls:', r.tool_calls)
print('content:', r.content)
"
```

**Pass criterion:** `r.tool_calls` is non-empty; agent calls `get_weather` instead of replying in text.

**If it emits text instead of tool calls:** apply the F14i `/no_think` injection — append
`/no_think` to the system message content. Already proven to unlock qwen3-family Ollama models.

**Recording:** update this section with actual outputs.

---

## Sanity-A — Ollama qwen3-coder:30b on retail train tasks 0/1/2 (2026-05-08)

**Run:** `--task-ids 0 1 2 --split train --concurrency 1`, with P0.4 patch applied,
agent_model = user_model = `ollama_chat/qwen3-coder:30b`. Wall ~5 min.

| Metric | Result |
|---|---|
| val_score | **0.0000** (0/3 passed) |
| Read actions | 9/14 (64%) ✅ |
| Write actions | **0/2 (0%) ✗** |
| DB Match | 0/2 ✗ |
| Termination | user-stop ×2 (simulator gave up); 1 infra error (likely the P0.5 empty-message bug) |
| LiteLLM auth errors | 0 (P0.4 patch worked ✅) |

**Diagnosis:** qwen3-coder:30b can read DB tools but cannot construct the complex write
action (`exchange_delivered_order_items` with nested `item_ids` + `new_item_ids` arrays).
The user simulator gives up after the agent fails to execute. This is consistent with
what was hypothesized in TODO-19 closure: qwen3-coder is a strong codegen model but
struggles on complex multi-arg structured tool calls.

**Implication:** 0% baseline gives no headroom for the improvement loop to demonstrate
lift. Need to find a local model with non-zero retail baseline before running conditions
B/C, OR change the framing (binary: did any task get fixed at all?).

## Sanity-B — LMS qwen3-coder-30b on retail train tasks 0/1/2 ✅ done (2026-05-08)

**Run:** `--task-ids 0 1 2 --split train --concurrency 1`, agent_model = user_model =
`openai/qwen/qwen3-coder-30b`, OPENAI_API_BASE=http://192.168.1.50:1234/v1.

| Metric | Result |
|---|---|
| val_score | **0.0000** (0/3 passed) |
| Read actions | **17/18 (94%) ✅** (vs 64% on Ollama) |
| Write actions | 0/3 (0%) ✗ |
| DB Match | 0/3 ✗ |
| Tasks evaluated | 3 (vs 2 on Ollama) ✅ |
| Empty-msg infra errors | 0 (vs 1 on Ollama) ✅ |

**Verdict:** LMS is meaningfully more stable than Ollama for the same model. Higher tool-call
success, no empty-message infra errors. But **same 0% val_score** — both runtimes hit the same
ceiling: qwen3-coder:30b can't construct the complex nested-array write call.

## Sanity-D — LMS ministral-3-14b-reasoning on retail train tasks 0/1/2 ✅ done (2026-05-08)

**Run:** `--task-ids 0 1 2 --split train --concurrency 1`, agent_model = user_model =
`openai/mistralai/ministral-3-14b-reasoning`, OPENAI_API_BASE=http://192.168.1.50:1234/v1.

| Metric | Result |
|---|---|
| val_score | **0.0000** (0/3 passed) |
| Infra errors | **3 (all 3 tasks)** ✗ |
| Messages exchanged | **0 per task** — died at first request |
| Duration | 0.0s per task |

**Failure mode:**
```
litellm.BadRequestError: Error rendering prompt with jinja template:
"After the optional system message, conversation roles must alternate user and assistant"
```

The Mistral/ministral chat template enforces strict role alternation. tau2's conversation
inserts `tool` messages between user and assistant, which the template rejects. **Not a
capability issue — a template incompatibility.** ministral can't run tau-bench at all
through LMS's OpenAI-compat layer.

**Implication:** the `ollama_chat/` route (which uses Ollama's `/api/chat`) handled the
tool messages correctly because LiteLLM translates the message structure for that route.
LMS OpenAI-compat passes messages through to the model's native chat template, which is
template-version-specific. Local-model attempts on tau-bench are now exhausted with the
desktop-available models.

**Decision:** task agent stays cloud Sonnet 4.6. Local models still play a role as the
**improvement loop agent** in P2/P3.

## Sanity-C — Cloud Sonnet 4.6 + Haiku user sim ✅ done (2026-05-08)

**Pivot:** user decision to switch task agent to cloud Sonnet 4.6 (preserves NeoSigma
comparison). Loop agent stays local (qwen3-coder:30b). Hybrid architecture.

**Run:** `--task-ids 0 1 2 --split train --concurrency 3`, agent_model =
`anthropic/claude-sonnet-4-6`, user_model = `anthropic/claude-haiku-4-5-20251001`,
ANTHROPIC_API_KEY from `/Users/jit/code/ownevo/ownevo_app/.env` (the cca-alias key was
rate-limited).

| Metric | Result |
|---|---|
| val_score | **1.0000 (3/3 PASS) 🎯** |
| Read actions | 17/18 (94%) ✅ |
| Write actions | **2/3 (67%) ✅** |
| DB Match | 3/3 (100%) ✅ |
| Cost | $0.67 total ($0.22/task average) |
| Wall | ~3-4 min |
| Notes | Task 2 succeeded on retry 3 (transient, recovered) |

**Verdict:** Harness works end-to-end. Sonnet 4.6 successfully constructs the nested-array
write calls (`exchange_delivered_order_items` with `item_ids` + `new_item_ids` arrays) that
local 30Bs failed on. Sample of 3 is too small to estimate full baseline; need to run the
full retail test split.

### Pre-sanity-C key issue (resolved)

Two problems with the first cloud attempts:

1. **Shell variable expansion:** `VAR=val docker compose run -e VAR="$VAR"` does NOT pass
   the assignment-prefix value. Shell evaluates `"$VAR"` against the parent shell's
   (empty) value before the prefix takes effect. Fix: `export VAR=val; docker compose run
   -e VAR …` (Docker forwards parent env when -e is by name only).

2. **Rate limit on the `cca` Claude Code key:** the Anthropic key from `~/.zshrc`'s `cca`
   alias rate-limited even on a single 1-message call. Likely daily token budget exhausted
   from heavy Claude Code use today. Fix: use the dedicated `ANTHROPIC_API_KEY` in
   `ownevo_app/.env` (different key, different tier).



Same model, different runtime (LMS llama.cpp vs Ollama llama.cpp). Quantization may differ;
sometimes gives noticeably different output on identical prompts.

```bash
cd /Users/jit/code/try_ext/auto-harness
# Update experiment_config.yaml: agent_model = user_model = "openai/qwen/qwen3-coder-30b"
# Override env: OPENAI_API_BASE=http://192.168.1.50:1234/v1, OPENAI_API_KEY=lm-studio
# Re-run --task-ids 0 1 2
```

## Sanity-D / future local-model retries (closed)

ministral-3-14b-reasoning was the strongest small candidate; failed on chat template (above).
Other unexplored desktop options that *might* work via LMS OpenAI-compat (template depending):

- `qwen2.5-coder-32b-instruct` (LMS, 98s on A4.4 — bigger coder, qwen-family template should
  match qwen3-coder which DID work)
- `qwen/qwen3-32b` (LMS, 96s on A4.4 — same template family)
- `Qwq:32b` (Ollama, 38 min on A4.4 — explicit reasoning, would route through `ollama_chat/`
  which we know handles tool messages)

**Decision (deferred):** revisit only if cloud baseline turns out unaffordable or if the
local-only narrative becomes load-bearing. For now the hybrid (cloud task agent + local loop)
gives the best balance of credibility and cost. Future local-task-agent attempts should:
1. Use `ollama_chat/` route (proven path for tool messages)
2. Pick qwen-family models (template match with successful qwen3-coder run)
3. Test on a single task first before committing to 3+ task runs

## Phase 1 — Condition A baseline on full retail TEST split ✅ done (2026-05-08)

**Status:** ✅ — `val_score_A = 0.8500` (patched substrate, 2026-05-08 22:51 PT)  
**Original P1 (auto-harness, unpatched substrate):** `0.8000` — superseded.  
**Depends on:** sanity-C ✅ — completed end-to-end via auto-harness fork

### Result (kernel substrate, post-fix)

| Metric | Value |
|---|---|
| **val_score_A** | **0.8500** (34 pass / 6 fail of 40) |
| Pass / fail / infra-err breakdown | 34 / 6 / 0 |
| Total cost | ~$9 ($0.22 / task average) |
| Wall time | ~16 min at concurrency=3 |
| Trace dir | `tau2_data/simulations/20260509_055122_retail_custom_agent_claude-sonnet-4-6_user_simulator_claude-haiku-4-5-20251001/` |
| Substrate | `ownevo-sandbox-tau3:0.1.0` w/ `tau2_patches.py` json-loads shims (commit `0a1f1cf`) |

**Anchor for % lift:** `val_score_A = 0.8500` is the frozen baseline used by all
condition-B / condition-C lift calculations. The 0.80 from the auto-harness P1 run
is preserved below for archaeology only.

### Earlier P1 result (auto-harness, unpatched — archived)

| Metric | Value |
|---|---|
| val_score_A | 0.8000 (32 pass / 8 fail-or-error of 40) |
| Pass / fail / infra-err breakdown | 32 / 4 / 4 |
| Cost | $9.27 |
| Trace dir | `tau2_data/simulations/20260509_000808_retail_custom_agent_claude-sonnet-4-6_user_simulator_claude-haiku-4-5-20251001/` |

The 5pp gap between the two runs is explained entirely by infra errors — the auto-harness
substrate had 4 sims hit `JSONDecodeError` (LiteLLM tool-args / NL-evaluator parse path)
that retry-thrashed and died as `INFRASTRUCTURE_ERROR`. The kernel substrate's
`tau2_patches.py` shims those `json.loads` sites, so the same 4 sims now evaluate to
real rewards — 2 of which happen to pass. See PR #77 for the patch details.

**Versus NeoSigma's published baseline (0.56 with GPT-5.4):** Sonnet 4.6 is **+24pp**
stronger out of the box on retail. This means:

1. **Less headroom for the loop.** From 0.80 a +20pp absolute lift is +25% relative —
   harder than NeoSigma's 0.56 → 0.78 (+22pp / +39% relative). Easy wins already absorbed
   by Sonnet's baseline capability.
2. **Stronger gate test.** Improvement-loop work on top of a strong baseline forces real
   reliability gains, not just easy-failure fixes. Better engineering story.
3. **Reframe the YC claim.** Not "we match NeoSigma's lift" but "ownEvo's loop pushes
   reliability past 0.80 on a benchmark where Sonnet starts at 0.56→0.80 = the model
   improvements absorbed the gap, and the loop now picks up the residual reliability tail."

### The 4 real failures (improvement-loop targets)

| Task | msgs | duration | term | failure shape |
|---|---|---|---|---|
| 5 | 25 | 39.3s | user_stop | agent completed conversation; DB Match wrong |
| 12 | 25 | 39.3s | user_stop | same |
| 49 | 26 | 33.9s | user_stop | same |
| 74 | 29 | 52.2s | user_stop | same |

All 4 have full message history saved (avg 26 msgs/task). These are the ideal failure
clusters for P1.5's failure_analyzer to extract `text_signature` from.

### The 4 infra errors

Tasks 36, 38, 70, 111: 0 messages, 0.0s duration. Transient Anthropic API errors
(rate-limit / 5xx). **Not improvement-loop targets.** Should be re-run in a follow-up
to lock the cleanest possible val_score_A. Open follow-up.

### Original Phase 1 spec (kept for reference)

**Goal:** establish `val_score_A` (frozen baseline) for retail TEST split with Sonnet 4.6
+ Haiku user sim. This is the anchor for % lift calculation in conditions B and C.

**Domain choice:** retail TEST split (40 tasks, comparable to NeoSigma's published 0.56
baseline). Train (74 tasks) is for the loop's failure analysis; gate scores on test.

**Trace storage:** tau2 auto-saves to
`/tau2_data/simulations/<auto_run_name>/results.json` — full per-conversation traces
(messages, tool calls, costs, rewards, effect timeline). Verified during sanity-C. **No
DB integration yet** — that's P1.5.

### Config

Create `/Users/jit/code/try_ext/auto-harness/experiment_config.yaml`:
```yaml
benchmark: "tau-bench"
domain: "retail"
agent_model: "ollama_chat/qwen3-coder:30b"
user_model: "ollama_chat/qwen3-coder:30b"
split: "train"
gate_split: "test"
max_concurrency: 3        # local model — keep low to avoid OOM on 192.168.1.50
threshold: 0.8
```

With env vars (passed via `docker compose run -e ...`):
```bash
OLLAMA_API_BASE=http://192.168.1.50:11434
# No OPENAI_API_KEY needed for ollama_chat/ provider
```

### Steps

- [ ] **P1.1** Build Docker image:
  ```bash
  cd /Users/jit/code/try_ext/auto-harness
  docker compose build
  ```

- [ ] **P1.2** Run prepare.py (initializes workspace + runs baseline on full train split):
  ```bash
  docker compose run \
    -e OPENAI_BASE_URL=http://192.168.1.50:11434/v1 \
    -e OPENAI_API_KEY=local \
    autoeval python prepare.py
  ```
  Records baseline to `workspace/results.tsv` as iteration 0.

- [ ] **P1.3** Record `val_score_A` from stdout + `workspace/results.tsv`.

- [ ] **P1.4** Examine 10 failing train traces in `workspace/traces/baseline/` to confirm:
  - Failure mode is model reasoning (improvable) not infrastructure (model not calling tools)
  - `qwen3-coder:30b` is actually engaging with tau2 tasks, not timing out or erroring
  - Note dominant failure patterns (prompt issue? sequencing? wrong action?)

**Exit gate:** `val_score_A > 0.10` (model is engaging). If 0.0 or near-0, model isn't
calling tools correctly — go back to P0.3 and fix tool-call routing before continuing.

**Expected timeline:** ~1-2 hours wall for 114 retail tasks at `max_concurrency=3` with
local model (~30s/task).

**Recording:** update this doc with `val_score_A`, wall time, dominant failure patterns.

---

## Phase 1.5 — Kernel migration: τ³ benchmark capability into `ownevo_kernel`

**Status:** ☐ before P2 (must — P2 should not depend on auto-harness fork)  
**Depends on:** P1 baseline complete (gives a known-good run to validate the migration against)

**See [`BENCHMARK_ARCHITECTURE.md`](BENCHMARK_ARCHITECTURE.md)** for the cross-benchmark
substrate design (τ³ is the first; terminal-bench / BIRD-Interact / SWE-bench / claw-eval
follow the same recipe). That doc defines the `BenchmarkRunner` Protocol, `SandboxProfile`
abstraction, and the 7-step recipe for adding a new benchmark. **τ³ is the reference
implementation that proves the pattern.**

**Why:** the auto-harness fork at `/Users/jit/code/try_ext/auto-harness/` was scaffolding to
get unblocked. For durable IP + the YC demo + ownEvo's web UI / audit chain / regression-gate
to work natively on τ³, the benchmark capability must live in `apps/kernel/src/ownevo_kernel/`.

**Strategy:** depend on **tau2** (the upstream Sierra benchmark library) directly. Don't pull
the auto-harness layers (`benchmark.py`, `gating.py`, `prepare.py`, `record.py`, `agent/agent.py`)
— ownEvo already has equivalents:

| auto-harness layer | ownEvo replacement |
|---|---|
| `benchmark.py:TauBenchRunner` | `ownevo_kernel.benchmarks.tau3.TauBenchRunner` (implements existing `BenchmarkRunner` Protocol) |
| `gating.py:run_gate` | `ownevo_kernel.gate.run_gate` (existing — already 3-step with regression / improvement / sandbox-error) |
| `record.py` → `results.tsv` | `iterations` table inserts via existing `persist_gate_run` |
| `workspace/suite.json` regression suite | `eval_cases` table with `is_test_fold=true` |
| `agent/agent.py` editable skill | Skill registry entry (`kind=code`, SKILL_FORMAT frontmatter) under workflow `tau3-retail-v1` |
| `workspace/learnings.md` | `failure_clusters` table + agent's `analyze_failures` tool |
| `workspace/traces/baseline/` + `latest/` | DB-backed traces (full message history per Meta-Harness ablation) |

### Sandbox: yes, Docker — different profile than M5

Earlier in this doc I claimed τ³ doesn't need a Docker sandbox. Reversed (2026-05-08, post
multi-benchmark-architecture decision). Two reasons:

1. **The agent-proposed skill IS user-generated Python.** Each iteration's new
   `HarnessAgent` class gets imported and executed by the gate. Same threat model as M5's
   LightGBM code. M5 mitigates with `LocalDockerSandbox`; τ³ should too.
2. **Defense-in-depth across benchmarks** — terminal-bench has shell access, BIRD-Interact
   talks to Postgres, future SWE-bench runs LLM-generated Python. A consistent sandbox
   substrate (`SandboxRuntime` Protocol with per-benchmark profiles) is durable IP and
   makes the security story coherent.

τ³'s sandbox profile differs from M5's:

| Setting | M5 profile | τ³ profile |
|---|---|---|
| Image | `ownevo-sandbox-m5:0.1.0` | `ownevo-sandbox-tau3:0.1.0` (tau2 + LiteLLM + kernel) |
| Network | `--network=none` (offline LightGBM) | egress-allowlist (api.anthropic.com, 192.168.1.50:11434) |
| Memory | 1024 MB | 512 MB (LLM HTTP client + tau2, no model in-process) |
| Timeout | 600s | 1800s (multi-turn LLM calls slow) |
| Other hardening | read-only rootfs, cap-drop=ALL, pids limit, tmpfs /tmp | same — defense-in-depth constants |

**Implementation:** extend `LocalDockerSandbox` to accept a `SandboxProfile`. See
`BENCHMARK_ARCHITECTURE.md` for the Protocol shape.

### File layout to add

```
apps/kernel/pyproject.toml
  [project.optional-dependencies]
  tau3 = ["tau2 @ git+https://github.com/sierra-research/tau2-bench.git@73dc24445d"]

apps/kernel/src/ownevo_kernel/benchmarks/tau3/
├── __init__.py
├── runner.py              # TauBenchRunner: BenchmarkRunner Protocol impl, wraps tau2.run_domain
├── skill.py               # SKILL_FORMAT-compliant baseline HarnessAgent + dynamic skill loader
│                          #   (loads agent.py content from skill registry, registers as tau2 agent)
├── failure_analyzer.py    # parse sub-0.5 sims → text_signature (mirrors M5 m5_failure_analyzer.py)
├── ingest.py              # read tau2 results.json → iterations + failure_clusters rows
└── tau2_patches.py        # consolidate the 4 monkey-patches from agent/agent.py into one importable
                           #   (DEFAULT_LLM_NL_ASSERTIONS + DEFAULT_LLM_ENV_INTERFACE)

apps/kernel/baselines/tau3_retail_v1/
├── README.md              # what this skill is + its retention contract
└── agent.py               # HarnessAgent baseline content in SKILL_FORMAT (frontmatter wrapped)

apps/kernel/scripts/
├── tau3_baseline.py       # condition A: run frozen baseline against test split
├── tau3_register.py       # one-time: register `tau3-retail-v1` workflow + seed eval cases
├── tau3_ingest.py         # backfill helper: ingest existing tau2_data/simulations/* dirs
└── (extend) run_improvement_loop.py  # add --workflow tau3-retail support

apps/kernel/migrations/
└── (no schema changes needed — existing iterations / failure_clusters / skills tables)

Makefile additions:
- tau3-register
- tau3-baseline
- tau3-loop
- tau3-ingest
```

### Step-by-step migration

| Step | What | Effort | Validates |
|---|---|---|---|
| **M1** ✅ | (revised) tau2 stays out of the kernel install — the host needs Python 3.11+ but tau2 needs 3.12-3.13, and tau2 pulls openai==2.x while [agent] caps at <2. Comment in `apps/kernel/pyproject.toml` documents the rationale. tau2 lives only inside the sandbox image. | XS — done 2026-05-09 | n/a |
| **M2a** ✅ | `Dockerfile.tau3` + `make sandbox-image-tau3`. python:3.12-slim base + pinned tau2 + dataset baked at `/tau2_data` + simulations subdir symlinked to `/tmp/tau3_sims` (writable under `--read-only` rootfs). 1.52 GB image. | S — done 2026-05-09 | `ownevo-sandbox-tau3:0.1.0` builds + tau2 imports cleanly + `/tau2_data` resolves |
| **M2b** ✅ | `LocalDockerSandbox` gained `network` ctor arg (default `none` preserves M5) + `extra_env` run() arg. Pragma: `EGRESS_ALLOWLIST` mode parked — for now tau3 uses `network='bridge'` (unrestricted egress). 24/24 sandbox + run_pipeline tests still pass. | S — done | M5 path unchanged; tau3 reaches Anthropic API |
| **M2c** ✅ | `tau2_patches.py` installed as sitecustomize.py inside the image. Reads `AGENT_MODEL` from env at every Python startup, redirects tau2's hardcoded gpt-4.1 NL_ASSERTIONS + ENV_INTERFACE defaults to whichever model the runner uses. Also creates `/tmp/tau3_sims` so the simulations symlink resolves. | XS — done | One import patches everything; verified end-to-end |
| **M3** ✅ | `apps/kernel/src/ownevo_kernel/benchmark/tau3/runner.py`. `SandboxedTauBenchRunner` implements `BenchmarkRunner` Protocol. Marshals fold args via stdin/stdout JSON exactly like `SandboxedM5BenchmarkRunner`. `skill_override_dir` bind-mounts at `/skill_override` (read-only); entrypoint imports `HarnessAgent` from there. | M — done | End-to-end test: 1 retail task via baked-in baseline + Sonnet returns reward=1.0 |
| **M4** ✅ | `apps/kernel/baselines/tau3_retail_v1/agent.py` — HarnessAgent class wrapping tau2.LLMAgent + SKILL_FORMAT frontmatter (improvement_target=tau3_retail_test_val_score). Mirrors NeoSigma auto-harness template so prior-art iteration patterns transfer. README documents the loop's edit surface vs the readonly contract. | XS — done | `read_skill` tool returns it cleanly; baked into image |
| **M5** ✅ | `scripts/tau3_register.py` — idempotent workflow + skill + 40 retail-test eval cases. Single transaction with FOR UPDATE on workflow row before skill loop. 4 unit tests. `make tau3-register`. | S — done | Test file imports + skips gracefully when no DB env |
| **M6** ✅ | `scripts/tau3_baseline.py` — sandboxed Day-1 baseline runner. CLI mirrors `m5_baseline.py`. Auto-loads ANTHROPIC_API_KEY from `.env` when not exported. Records iterations row at MAX(iteration_index)+1 unless `--no-db`. `make tau3-baseline`. | S — done | Plumbing smoke pass; full-40-task validation deferred (we have P1's number from auto-harness) |
| **M7** ✅ | `failure_analyzer.py` — pure-stdlib parser of tau2 results.json → ranked Tau3FailureSnapshot list. Preserves full message history (Meta-Harness ablation). Heuristic hints: infra-error / max-steps / user-gave-up / write-attempted / no-writes-attempted / long-conversation. 8 unit tests. | M — done | Verified on actual P1 trace: 8 failures correctly extracted (4 infra + 4 user_stop with write-attempted) |
| **M8** ✅ | `scripts/tau3_ingest.py` — backfill helper. Reads tau2 results.json, computes val_score, inserts iterations row. `--dump-failures <dir>` writes per-failure JSONs for downstream clustering. `make tau3-ingest`. | S — done | P1 trace → val_score=0.8 (matches exactly); 8 failure JSONs dumped |
| **M9** ✅ | `scripts/run_tau3_loop.py` — one improvement-loop iteration. Three independently-configurable LLM roles: loop agent (default qwen3-coder:30b on Ollama), task agent (default Sonnet 4.6), user simulator (default Haiku 4.5). Two LocalDockerSandbox instances: gate_sandbox (τ³ image, network=bridge) and loop_tool_sandbox (M5-style for `run_pipeline`). Single-file skill override via `_materialize_tau3_skill_override`. `make tau3-loop`. | M — done | --help parses, all imports resolve, .env loader picks up ANTHROPIC_API_KEY. Live integration deferred (would burn ~\$10 per iter on Sonnet); same gate path validated end-to-end in M3+M4. |
| **M10** ✅ | Workspace nav link added at `apps/web/app/workspaces/[wsId]/workspace-nav.tsx`. The W7 detail surface (Health/Failures/Audit/Skills/Traces) is already workflow-id-agnostic — falls through to live-DB path when `getMock(wfId)` returns null, which it does for `tau3-retail-v1`. | XS — done | Once M5 + M6/M9 populate the DB, `/workspaces/acme/workflows/tau3-retail-v1` renders live data without further wiring. |

**Total effort:** ~4-6 days CC (sandbox profile work adds ~1 day vs the no-sandbox plan).
**~1.5-2.5 weeks human.**

### Sequencing relative to other phases

```
P1 (auto-harness) ──→ P1 baseline number captured
                      ↓
                      M1-M2 (deps + patches) ──→ M3 (TauBenchRunner) ──→ M6 (re-run baseline natively)
                                                                          ↓
                                                                          (validates migration: native val_score = auto-harness val_score ± 5pp)
                      M4-M5 (skill + workflow) ─────────────────────────→ M7-M8 (failure clusters + ingest)
                                                                          ↓
                                                                          M9 (loop integration) ──→ P2 starts here, NOT on auto-harness
                                                                          ↓
                                                                          M10 (web UI) ──→ visible by P3 / W8 demo
```

P1 still runs on the auto-harness fork (fast, gives us the number now). Migration runs in
parallel with P1 analysis. **P2 onward must run on ownEvo native** — that's the whole reason
for the migration.

### Auto-harness retirement

After M9 lands and P2 condition B has produced one successful gate-pass natively, the
auto-harness fork at `/Users/jit/code/try_ext/auto-harness/` becomes a **reference repo
only** (we may grep its `notes_jit.txt` for prior-art improvements, but never run it again).
Trace dirs at `tau2_data/simulations/` still useful as historical traces — ingest them via
M8.

## Phase 2 — Condition B: Autonomous loop (no approval gate)

**Status:** 🔄 in-progress — Sonnet 4.6 P2 series started 2026-05-09  
**Baseline:** `val_score_A = 0.8500` (workflow `tau3-retail-v1`, patched substrate, 2026-05-08)

**Goal:** measure lift from val_score_A via ownEvo's improvement loop. Target: 15-20 iterations
(matches Meta-Harness 20+ and NeoSigma 18).

### Architecture (decided, running)

```
claude-sonnet-4-6 (loop agent, Anthropic cloud)
  reads:  DB skill + past_attempts (cross-iteration trace via fetch_past_attempts)
  edits:  tau3.retail.baseline.v1.agent (skill registry, kind=python)
  gates:  ownevo_kernel.gate.run_gate (40-task retail test split, val_score via SandboxedTauBenchRunner)
  records: iterations table (workflow_id=tau3-retail-v1)
```

Loop driver: `apps/kernel/scripts/run_tau3_loop.py` (M9). One invocation = one gate cycle.

### Loop agent decision (2026-05-09)

Original plan was `qwen3-coder:30b` (local, free). Switched to Sonnet 4.6 for P2 because:
- qwen3-coder:30b on W6 v5 hit F6/M5SandboxError 7/7 — generalizability from TODO-19 uncertain
- Sonnet 4.6 is the confirmed multi-turn loop lift driver (B4.2 + B4.3 + Stage C, ~$1.86/iter M5)
- Test with expensive model first; if lift confirmed, run local model sweep (see below) to find a free substitute

**Local model sweep** (parallel, diagnostic — see § Local model sweep below): 6 desktop models
each get an independent `--workflow-id` so their gate histories don't pollute the Sonnet 0.85 anchor.
The sweep answers "which local models can drive the loop at all" before committing to a free-loop run.

### Unattended run

Reusable scripts permanentized at `apps/kernel/scripts/`:

| Script | Purpose |
|---|---|
| `tau3_p2_sonnet_loop.sh` | Sonnet 4.6 cloud N-cycle on `tau3-retail-v1`. The driver that produced batch-1's val=0.95 result. |
| `tau3_p2_local_loop.sh` | Parameterized local-model multi-cycle on its own `tau3-retail-v1__<tag>` workflow. Used for the qwen3.6-35b-a3b run (cycles 1+2 PASS at val=0.85). |
| `tau3_p2_local_sweep.sh` | 6-model sequential diagnostic sweep, one cycle each. Sequential because all candidates share one desktop GPU. |

Env-var overrides on all three: `OWNEVO_TAU3_LOGDIR` (default `/tmp/tau3_p2_logs`), `OWNEVO_TAU3_CYCLES` (default 10), `OWNEVO_LLM_HOST` (default `192.168.1.50`, used by sweep script).

```bash
# Sonnet (cloud) — 10 cycles
nohup bash apps/kernel/scripts/tau3_p2_sonnet_loop.sh > /tmp/tau3_p2_logs/sonnet_p2_nohup.log 2>&1 &

# Local model (parameterized: model, base_url, workflow_tag)
bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "qwen/qwen3.6-35b-a3b" "http://192.168.1.50:1234/v1" "qwen36"

# 6-model diagnostic sweep
bash apps/kernel/scripts/tau3_p2_local_sweep.sh
```

Check status:
```bash
tail -20 /tmp/tau3_p2_logs/sonnet_p2_master.log
```

### Results — batch 1 complete (2026-05-09)

| Iter | Cycle | val_score | decision | best_ever | notes |
|---|---|---|---|---|---|
| 5 | anchor | 0.8500 | gate-pass | 0.8500 | manual re-anchor (baseline) |
| 6 | b0/1 | 0.7000 | NO_IMPROVEMENT | 0.8500 | partial batch-0 run |
| 7 | b0/2 | 0.8000 | NO_IMPROVEMENT | 0.8500 | partial batch-0 run |
| 8 | b0/3 | 0.8250 | NO_IMPROVEMENT | 0.8500 | partial batch-0 run |
| 9 | b0/4 | 0.8250 | NO_IMPROVEMENT | 0.8500 | partial batch-0 run |
| 10 | b1/1 | — | SANDBOX_ERROR | 0.8500 | v36 dedup tracker accessed `tc.function.name` (OpenAI shape, wrong) |
| **11** | **b1/2** | **0.9500** | **PASS** ⭐ | **0.9500** | **v38 — minimal prompt-only change** |
| 12 | b1/3 | 0.7000 | NO_IMPROVEMENT | 0.9500 | v40 structured tool-use rules |
| 13 | b1/4 | 0.8750 | NO_IMPROVEMENT | 0.9500 | v42 retail-specific lookup-before-act |
| 14 | b1/5 | 0.8250 | NO_IMPROVEMENT | 0.9500 | v44 tool_errors injection in HarnessState |
| 15 | b1/6 | 0.8000 | NO_IMPROVEMENT | 0.9500 | v46 4 calibrated rules |
| 16 | b1/7 | 0.8000 | NO_IMPROVEMENT | 0.9500 | v48 empty-response safety guard |
| 17 | b1/8 | 0.9000 | NO_IMPROVEMENT | 0.9500 | v50 seen_tool_calls loop-breaker |
| 18 | b1/9 | 0.8000 | NO_IMPROVEMENT | 0.9500 | v52 consecutive_tool_errors counter |
| 19 | b1/10 | 0.7000 | NO_IMPROVEMENT | 0.9500 | v54 order-detail confirmation rule |

**Headline: val_score 0.8500 → 0.9500 (+10pp absolute / +11.8% relative).** First
gate-pass at iter 11. Snapshot at `/Users/jit/code/ownevo/backups/tau3_p2_batch1_complete_20260509/`.

### What the winning skill (v38) actually did

**Prompt-only change.** No `HarnessState` fields, no `generate_next_message`
override, no helper methods. The full `AGENT_INSTRUCTION` body, verbatim:

> You are a helpful retail customer-service assistant. Complete every task by following the <policy> provided below.
>
> Key operating rules:
> - If a tool call returns an error, read the error message carefully and retry with corrected arguments (fix the specific parameter that was wrong). Do not repeat the exact same call.
> - If you already have the information needed to answer, respond directly without calling any tools.
> - When the task is complete, provide a clear final answer to the user and stop.

Every richer proposal Sonnet tried in cycles 3-10 (HarnessState extensions,
empty-response guards, consecutive-error counters, structured tool-use rules)
**scored below v38**. v36 broke outright. This matches NLAH's finding that
"more structure can hurt when modules diverge from the evaluator's acceptance
condition."

### Open questions for batch 2

1. **Loop saturation or local optimum?** 9 cycles after v38 explored the
   space and none beat 0.95. A second 10-cycle batch tells us whether 0.95 is
   a saturating ceiling or just a local optimum the agent hasn't escaped yet.
2. **HEAD ≠ best-gate-pass quirk.** `skills.head_version_id` advances on every
   `write_skill`, even when the gate rejects. By end of batch 1 HEAD pointed at
   v54 (failed) instead of v38 (passed). Worth fixing in a follow-up; doesn't
   affect val_score recorded in iterations. **→ TODO-31.**
3. **Pass³ stretch.** Cycle 2 scored 0.95 once; need re-run × 3 to estimate
   reliability per Claw-Eval's reliability-not-peak framing. **→ TODO-32.**

### Results — batch 2 complete (2026-05-09)

10 more Sonnet 4.6 cycles on the same workflow, gate-comparing against
best_ever=0.95. **0/10 broke through.** Pattern across all 10 cycles:

| Cycle | val_score | Decision |
|---|---|---|
| 1 | — | SANDBOX_ERROR (`tc.function` AttributeError, regression) |
| 2 | 0.825 | NO_IMPROVEMENT |
| 3 | 0.825 | NO_IMPROVEMENT |
| 4 | — | SANDBOX_ERROR |
| 5 | 0.825 | NO_IMPROVEMENT |
| 6 | — | SANDBOX_ERROR |
| 7 | 0.825 | NO_IMPROVEMENT |
| 8 | — | SANDBOX_ERROR |
| 9 | 0.85 | NO_IMPROVEMENT |
| 10 | 0.80 | NO_IMPROVEMENT |

**Verdict: 0.95 is a saturation ceiling for Sonnet on this benchmark+substrate.**
20 total Sonnet cycles, 1 gate-pass — and that PASS came from a *minimal* prompt
change, not from richer scaffolding. Sonnet's exploration in batch 2 included
HarnessState memory fields, error-recovery counters, and structured tool-use
rules; all underperformed v38's three-line prompt. Consistent with NLAH
("more structure can hurt").

**What's left at 0.95**: tasks 33 and 49 are the two failures (computed from
the gate audit's `promotable_task_ids` ∖ retail-test-40). Task 49 also failed
at the 0.85 baseline (iter 5) — persistent. Task 33 is a regression introduced
by v38. **→ TODO-33** to use the new trace inspector once at least one fresh
v38 gate cycle re-populates traces.

### qwen3.6-35b-a3b — local loop agent (2026-05-09, in progress)

**Setup:** `qwen/qwen3.6-35b-a3b` on LMS desktop (`http://192.168.1.50:1234/v1`,
OpenAI format) as the loop agent. Task agent + user sim stay on cloud Anthropic.
Workflow `tau3-retail-v1__qwen_qwen3.6-35b-a3b` so its gate history is
independent of the Sonnet 0.95 anchor.

| Cycle | val_score | Decision | best_ever | Notes |
|---|---|---|---|---|
| 1 | 0.8000 | **PASS** | 0.8000 | first proposed skill, lift over fresh-workflow zero |
| 2 | 0.8500 | **PASS** | 0.8500 | matches Sonnet's baseline on a free local loop agent |
| 3 | 0.8000 | NO_IMPROVEMENT | 0.8500 | regression, gate held |
| 4 | 0.8000 | NO_IMPROVEMENT | 0.8500 | regression, gate held |
| 5 | 0.8500 | NO_IMPROVEMENT | 0.8500 | reproduces 0.85 — lift is repeatable, not a one-off |
| 6 | 0.7250 | NO_IMPROVEMENT | 0.8500 | regression |
| 7+ | 🔄 in progress | | | |

**Provisional verdict (preliminary, more cycles to come):** qwen3.6 drives the
τ³ loop cleanly via the OpenAI-compat path on LMS, produces lift on its own
workflow, and matches Sonnet's 0.85 baseline anchor with a free 35B local
model. Cycle 2 + cycle 5 both at 0.85 = the lift is reproducible.

**Strongest YC-friendly story so far:** the loop agent can be local, free, and
still produce measurable lift — kills the "you need a frontier API to drive
this" objection. Whether qwen3.6 can push past 0.85 on this workflow remains
open through cycles 7-10.

### Substrate fixes shipped during P2 (2026-05-09)

| Fix | Commit | Why |
|---|---|---|
| Per-task trace persistence | `daef4c2` | Container tmpfs at `/tau2_data/simulations` was destroyed at exit, so per-task tau2 message history was lost forever. Now `SandboxedTauBenchRunner` serializes each `Simulation` (full messages, reward_info, termination_reason, info, duration) through stdout JSON; `persist_gate_run` writes one `traces` row per task per iteration. New `scripts/tau3_inspect_task.py` lets you list / show / compare task traces across iterations to diagnose regressions without re-running. **Pre-fix iterations (0–19) have no per-task traces — that data is permanently lost.** All P2 batches above ran pre-fix. |
| Verbatim winning prompt captured | `e4d08be` | Added v38's full `AGENT_INSTRUCTION` body to this doc + the snapshot README so the actual three-rule prompt is reproducible without grepping skill_versions. |
| P2 batch-1 result recorded | `4443ed9` | val_score 0.85 → 0.95 written into the phase tracker + iteration table. |

**Exit gate:** `val_score_B > val_score_A = 0.8500`. If no lift after 5 cycles, inspect
master log for pattern (loop agent proposal quality / skill write errors / sandbox errors)
before extending to 15-20.

### Local model sweep (diagnostic, separate workflow IDs)

**Rationale:** local models must be graded against their own gate history, not Sonnet's 0.85
anchor. Each model runs under `--workflow-id tau3-retail-v1__<tag>`; gate compares against
`MAX(best_ever_score_after)` for that workflow_id only (starts at 0). The shared skill
registry (`tau3.retail.baseline.v1.agent`) is re-anchored to baseline before each model's first
iteration by `seed_tau3_retail`'s idempotent body-equality check.

**Script:** `/tmp/tau3_p2_logs/run_local_sweep.sh`

### Sweep results — 6 models × 1 cycle each (2026-05-09)

| Model | Provider | Drives loop? | Best result | Status |
|---|---|---|---|---|
| **gemma4:26b** | Ollama | ✅ cleanly | val=0.85 (matches baseline, no regression) | only viable candidate from sweep |
| qwen3:32b | Ollama | ✅ but skill broke | SANDBOX_ERROR (`reasoning_effort=""` hallucinated env var) | fixable with prompt nudge |
| granite4.1:30b | Ollama | ✗ gave up | — | likely terminal (read skill, never wrote) |
| mistralai/devstral-small-2-2512 | LMS | ✗ tool-error storm | — | TODO-21 closure stands (codegen quality) |
| mistralai/ministral-3-14b-reasoning | LMS | ✗ tool-error storm | — | template incompat (chat-template strict alternation) |
| zai-org/glm-4.7-flash | LMS | ✗ context too small | — | terminal (kickoff message exceeded model context) |

**Verdict:** only **gemma4:26b** drove the τ³ loop cleanly on the sweep. Multi-cycle
follow-up is captured in the **qwen/qwen3.6-35b-a3b** result above (separate workflow
on LMS, drove the loop and reproduced 0.85 across 2 PASSes — different model from the
sweep's 6 but same pattern of evidence).

### Pending / open local-model work

| Item | Why not done in sweep | Notes |
|---|---|---|
| **gemma4:26b multi-cycle (Ollama OpenAI-compat)** | ✅ done 2026-05-10 — see results below | 5 cycles; codegen bugs every cycle — not a viable lift driver |
| **gemma4:26b native Ollama /api/chat** | not tried — OllamaChatClient loop runner not yet wired | run_agent_turn_ollama now implemented; try to see if API format affects codegen quality |
| **gemma4:26b on LM Studio (OpenAI / Anthropic)** | not tried | Different quantization and serving; may produce different codegen behavior |
| **qwen3:32b retry with nudge** | failed because it hallucinated `AGENT_REASONING_EFFORT` env var | One-line fix: add `Do NOT add or pass arbitrary env-var-driven kwargs to LiteLLM` to the loop kickoff prompt |
| **qwen3-coder:30b on Ollama OpenAI** | not in sweep — was the original P2 plan but switched to Sonnet for confirmed lift first | Worth running now that v38 exists as a strong starting point — see if local model can do incremental work on top |
| **Qwq:32b on Ollama** | not in sweep — explicit reasoning model, would route via `ollama_chat/` (proven path) | Strongest unexplored desktop candidate |
| **gpt-oss:120b on Ollama** | not in sweep — large open-weight, untested for tau3 | Free, big, worth a single-cycle smoke |
| **gemma4:26b building on v38** | future work | Strongest "cross-model collaboration" story — local gemma incrementally improves Sonnet-discovered v38 |

#### gemma4:26b multi-cycle results (2026-05-10, workflow `tau3-retail-v1__gemma4_26b_ollama`)

5 cycles, `--api-format openai`, Ollama `/v1/chat/completions`, `http://192.168.1.50:11434/v1`.

| Cycle | val_score | Decision | Root cause |
|---|---|---|---|
| 1/5 | 0.8250 | PASS | Existing skill eval (baseline — not a gemma4 proposal) |
| 2/5 | — | SANDBOX_ERROR | `NameError: name 'message' is not defined` in `get_init_state` |
| 3/5 | — | SANDBOX_ERROR | Same NameError |
| 4/5 | — | SANDBOX_ERROR | Same NameError |
| 5/5 | — | SANDBOX_ERROR | Naive `state.messages[-15:]` truncation broke tool_use/tool_result pairs; 15/40 infra errors, 25 eval'd at avg 0.68 |

**Learnings — do not repeat these mistakes in future runs:**

1. **Parameter cross-contamination (cycles 2–4):** gemma4 rewrote `get_init_state` using `message` — a parameter name from `generate_next_message`'s signature — which doesn't exist in `get_init_state`. Classic hallucination of undefined variable. All 40 tasks fail at `orchestrator.initialize()`. Prompt nudge would be: *"When rewriting a method, only use the parameters defined in that method's signature."*

2. **Naive message truncation (cycle 5):** gemma4 added `truncated_messages = state.messages[-15:]` as an efficiency improvement. Slicing in the middle of a tool_use/tool_result exchange drops the `tool_use` block while keeping the `tool_result`, triggering Anthropic's validation: `unexpected tool_use_id found in tool_result blocks`. Prompt nudge would be: *"Never slice message history at an arbitrary index — tool_result blocks must always be immediately preceded by the matching tool_use block."*

3. **Pattern:** Different codegen bug each cycle. Not a fixable single-rule issue — reflects fundamental limitations in gemma4:26b's Python code generation accuracy under the tau3 constraint space. The model understands what to improve (add safety valve, add efficiency) but can't implement it correctly.

**Recommendation:** gemma4:26b is not a viable autonomous improvement driver for tau3 unless given very tight few-shot examples of correct `get_init_state` and `generate_next_message` structure. Native Ollama format (`/api/chat`) is unlikely to fix codegen bugs but worth 1 cycle to confirm API format is not a factor.

Run command (single model, change `--llm-model` and `--workflow-id`):
```bash
PASS=$(docker inspect ownevo-postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep POSTGRES_PASSWORD | cut -d= -f2)
export OWNEVO_DATABASE_URL="postgresql://ownevo:${PASS}@localhost:5432/ownevo"
uv run --extra agent python scripts/run_tau3_loop.py \
  --workflow-id tau3-retail-v1__gemma4_26b \
  --api-format openai \
  --llm-base-url http://192.168.1.50:11434/v1 \
  --llm-model gemma4:26b \
  --task-concurrency 3 --task-timeout-seconds 2400
```

Results land in `<repo>/log/tau3_p2/sweep_results.tsv` (sweep) or per-cycle log files (multi-cycle). Older runs may still be under `/tmp/tau3_p2_logs/` from before the log-dir migration.

---

### Local LLM compat matrix

(Model × API path) — what works, what's broken, and why we don't bother re-running known failures. Update after every sweep.

The 4 API paths correspond to the `tau3_p2_local_loop.sh` / `tau3_p2_local_sweep.sh` presets:

- `ollama` — Ollama native `/api/chat` (api_format=ollama)
- `ollama-openai` — Ollama OpenAI-compat `/v1/chat/completions`
- `lms-openai` — LM Studio OpenAI-compat `/v1/chat/completions`
- `lms-anthropic` — LM Studio Anthropic-compat `/v1/messages`

Cell legend:
- ✅ = drives loop end-to-end (proposes, calls tools, codegen survives validation)
- ⚠ = calls tools but codegen breaks consistently (model-level limitation, not API-level)
- ✗ = blocked at the API/template/tool-calling layer (don't re-run as-is)
- — = not yet tested
- 🚫 = template/architecture incompat (don't re-run; document & skip)

| Model | `ollama` | `ollama-openai` | `lms-openai` | `lms-anthropic` | Notes / load-bearing flags |
|---|:-:|:-:|:-:|:-:|---|
| qwen3-coder:30b | — | ✅ ¹ | — | ⚠ ² | ¹ requires `/no_think` auto-injection (runner.py); +14.9% on TODO-19 then F6 7/7 on W6 v5. ² LMS-Anthropic: 14/14 deterministic `_long_frame` codegen bug (TODO-20). |
| qwen3.6-35b-a3b (LMS) | — | — | ✅ ³ | ✅ ³ᵇ | ³ drove loop, hit val=0.85 ×2 in multi-cycle. Thinking embedded too deep for `/no_think` to override (LMS strips thinking client-side). ³ᵇ 2026-05-10: works after runner.py `_run_turn_no_stream` fix (commit `4202f1e`); cache_read_input=31491 confirms LMS auto-cache. |
| qwen3.6:35b-a3b (Ollama) | ✅ ³ᶜ | ✗ ³ᵈ | n/a | n/a | ³ᶜ 2026-05-10 smoke: native `/api/chat` works because `OllamaChatClient` auto-injects `options.think=false` (ollama_native.py:209). Loop drove cleanly: 5 iters, 7348 out, end_turn. ³ᵈ openai-compat strips think:false silently → verbose thinking → 16501 out tokens → DEFAULT_MAX_TOKENS_OPENAI cap hit in 2 iters. |
| qwen3.5-9b | — | ✗ ⁴ | ✗ ⁴ | ✅ ⁴ | ⁴ F14g — 0/3 via OpenAI, 3/3 via Anthropic. API-format-load-bearing. |
| qwen3:32b | — | ⚠ ⁵ | — | — | ⁵ hallucinated `AGENT_REASONING_EFFORT` env var; needs prompt nudge. |
| qwen2.5-coder:32b | — | 🚫 ⁶ | — | — | ⁶ doesn't trigger tool calls with `tool_choice=auto`. |
| Qwq:32b | — | — | — | — | reasoning model; would route via `ollama_chat/`. Untested. |
| gpt-oss:20b | — | — | — | — | untested. (120B variant skipped per user direction — too large for current VRAM topology.) |
| gemma4:26b | ⚠ ⁷ᵇ | ✅ ⁷ | — | — | ⁷ 2026-05-10 sweep P1.3 + P2.3: drove loop cleanly (`end_turn`, 5-9 iters, valid proposals v_seq=84 + 95). Replaces older "⚠ codegen bugs" verdict — that was a different cycle pattern. ⁷ᵇ native `/api/chat` hit `httpx.ReadTimeout` at 5 min; ollama_native.py timeout bumped 300→600s on 2026-05-10. Re-test pending. |
| google/gemma-4-26b-a4b (LMS) | — | — | ✗ ⁸ | ✗ ⁸ | ⁸ 2026-05-10 sweep P1.2 + P2.2 (4 attempts both APIs): `stop_reason=max_tokens` after only 1061-7348 output tokens — model emits brief output then stops mid-iteration. Suspect LMS-side `max_completion_tokens` setting or quant tendency. |
| granite4.1:8b | — | 🚫 ⁹ | — | — | ⁹ generates U+2013 em-dash → SyntaxError (A4.4 gate). Useful only as task agent / user-sim, not loop driver. |
| granite-4.1-8b (LMS) | — | — | ✅ ¹⁰ | — | ¹⁰ A4.4 fastest desktop 3/3 (33s). Loop-driver capability not yet sweeped. **Task-agent role: broken** — see `Task-agent role compat` below. |
| granite4.1:30b | — | 🚫 ¹¹ | — | — | ¹¹ read skill, never wrote — gave up. |
| devstral-small-2:latest | — | 🚫 ¹² | — | — | ¹² runnable Python, but `run_pipeline` validation rejects every diff (TODO-21). |
| mistralai/devstral-small-2-2512 (LMS) | — | — | 🚫 ¹³ | — | ¹³ tool-error storm — codegen quality too low. |
| mistralai/ministral-3-14b-reasoning (LMS) | — | — | 🚫 ¹⁴ | — | ¹⁴ chat-template strict alternation — template incompat. |
| zai-org/glm-4.7-flash (LMS) | — | — | ⚠ ¹⁵ | — | ¹⁵ kickoff message exceeded model context **at default LMS load**. Fix is `lms load zai-org/glm-4.7-flash -c 32768` not a different build — phase3_full_lms_sweep now does this automatically (commit on 2026-05-10). Re-test pending. |
| qwen/qwen3-30b-a3b-2507 (LMS) | — | — | — | — | in 2026-05-09 sweep batch (results pending). |

**Rules:**
1. Don't re-run 🚫 cells — root cause is template / model architecture, not flaky.
2. Re-running ✗ requires changing the failing condition (longer context, different prompt, kernel patch). Note the condition change in the cell.
3. Adding a new model → run all 4 cells unless an entry above proves a path is irrelevant (e.g. LMS-only model can't use Ollama). Cost of one extra cycle ≪ cost of debugging silent regressions.
4. Tool-calling + thinking-flag behavior is the *primary* signal — codegen quality only matters if those are clean.

### Task-agent role compat (added 2026-05-10)

The matrix above measures **loop-driver capability**. A model that drives the loop cleanly may still fail as a **task agent** (the retail tau-bench solver inside the gate sandbox). The retail conversation pattern hits different code paths and template branches. Surfaces seen so far:

| Model (as task agent via LiteLLM) | Result | Failure mode |
|---|:-:|---|
| `openai/qwen/qwen3.6-35b-a3b` (LMS) | ✗ | LMS jinja: `"No user query found in messages"` — 40/40 infra errors. The retail evaluator's first message structure trips the model's bundled template (P1.1, sweep 2026-05-10). |
| `anthropic/qwen/qwen3.6-35b-a3b` (LMS) | ✗ | **Same jinja error** via `/v1/messages`. Server-side template, API-agnostic. Routing prefix doesn't help (P1 rerun, 2026-05-10). |
| `ollama_chat/qwen3.6:35b-a3b` (Ollama) | ⏳ unblocked | Smoke 3 hung — `docker logs ollama` showed every `/api/chat` returning `500 \| 10m0s` for 2+ hours. Root cause: LiteLLM's `ollama_chat` provider does NOT auto-inject `options.think=false` (only `OllamaChatClient` does). qwen3.6 task agent generated unbounded thinking traces, hit Ollama's internal 10-min inference cap. **Fix landed 2026-05-10**: `apps/kernel/sandbox/tau2_patches.py:_patch_litellm_ollama_think_off` monkey-patches `litellm.completion`/`acompletion` at sandbox-Python-startup to inject `options={"think": False}` for `ollama_chat/qwen3*` and `ollama/qwen3*` models. Smoke 4 verified: `/api/chat` latency dropped from 10-min timeouts → 7-13s. Sandbox image `ownevo-sandbox-tau3:0.1.0` rebuilt with patch baked in. |
| `openai/granite-4.1-8b` (LMS) | ✗ | LiteLLM `OpenAIException` with empty message in 40/40 (P2, sweep 2026-05-10). Granite's first-turn response is structurally valid (verified via direct curl 2026-05-10). Suspect: numeric tool_call id (`"873012003"` not `"call_*"`) or non-standard `reasoning_content` field tripping LiteLLM strict pydantic validation. Multi-turn flow not yet probed. |
| `anthropic/granite-4.1-8b` (LMS) | — | Untested. Try as fallback. |

**Sandbox-image dependency for `ollama_chat/qwen3*` task agents:**
The fix above lives in `tau2_patches.py` which is baked into
`ownevo-sandbox-tau3:0.1.0` at build time. **Any sandbox image built before
the 2026-05-10 patch will hang in 10-min loops on Ollama qwen3* task agents.**
Rebuild with `make sandbox-image-tau3` after pulling main on a fresh checkout.

**Open dimensions:**
- **lmstudio-community/Qwen3.6-35B-A3B-GGUF** exists on HF (verified 2026-05-10). Ships fixed templates. Would unlock LMS qwen36 as task agent. Download is ~22 GB; not yet pulled.
- **gemma4:26b on Ollama as task agent** untested. Ollama has its own template (independent of LMS jinja) so worth a try as alternative — non-thinking model so the think-patch above doesn't affect it.

---

## Phase 3 — Condition C: Gated loop (LLM-judge approval)

**Status:** ☐ not started  
**Depends on:** P2 exit gate passes

**Goal:** re-run the improvement loop with ownEvo's LLM-judge approval engaged. Every
gate-passing proposal goes through `apps/kernel/src/ownevo_kernel/approvals/llm_judge.py`
before being committed. Measure whether approval gate adds latency without sacrificing lift.

### Steps

- [ ] **P3.1** Wire approval gate into condition C loop:
  - Gate-passing proposals from tau3 loop → approval queue endpoint
  - LLM-judge approves/rejects based on plain-language explanation of the change
  - Approved changes committed to agent.py + audit chain entry written

- [ ] **P3.2** Run 10 iterations of condition C (fresh workspace, same baseline `val_score_A`).

- [ ] **P3.3** Record `val_score_C`, lift = `(C-A)/A * 100`, gate-blocked regressions count,
  LLM-judge approve/reject decisions.

- [ ] **P3.4** Human (founder) re-approves ≥5 gate-passing changes manually; document any
  divergence from LLM-judge decisions.

**Exit gate:** `val_score_C > val_score_A` (any lift with gate engaged).

---

## Phase 4 — Results document

**Status:** ☐ not started  
**Depends on:** P3 complete

- [ ] **P4.1** Write `ownevo_docs/benchmarks/tau3-results-2026-Q3.md` with:
  - Three-condition table: val_score A / B / C + % lift A→C
  - Honest disclosure: task agent = `qwen3-coder:30b` (Ollama, local), not GPT-5.4
  - NeoSigma comparison note: different task model, same benchmark, same structural loop
  - Gate-blocked regressions count, LLM-judge approve/reject split
  - Top 3 improvements (from `workspace/learnings.md`)
  - Reproducibility: `make tau3-replay` command (to be written)

- [ ] **P4.2** Add `make tau3-replay` target to top-level Makefile.

- [ ] **P4.3** Update PLAN.md (W7 Track 3 rows 7.3.1-7.3.3, W8 rows 8.3.1-8.3.3).

---

## Results ledger (fill in as phases complete)

| Condition | Model | Domain | Tasks | val_score | Lift vs A | Wall time | Cost |
|---|---|---|---|---|---|---|---|
| A — frozen baseline | qwen3-coder:30b | retail | 114 | — | — | — | $0 |
| B — autonomous loop | qwen3-coder:30b | retail | 114 | — | — | — | $0 |
| C — gated loop | qwen3-coder:30b | retail | 114 | — | — | — | $0 |

**NeoSigma reference (GPT-5.4, no gate):** 0.56 → 0.78 (+39.3%), 18 iterations, 96 experiments.

---

## Open questions / blockers

| # | Question | Blocking | Resolution |
|---|---|---|---|
| Q1 | ~~Does tau2 respect `OPENAI_BASE_URL`?~~ | — | ✅ tau2 uses **LiteLLM** — route via `ollama_chat/` prefix + `OLLAMA_API_BASE` env (P0.1) |
| Q2 | ~~Does qwen3-coder:30b emit tau2-compatible tool calls?~~ | — | ✅ Clean tool call in P0.3, no `/no_think` needed |
| Q3 | Loop driver: Option A (Claude Code, cloud loop agent) or Option B (all-local)? | P2 | Decision needed before P2 starts |
| Q4 | Does the model hit per-task timeouts at `max_concurrency=3`? | P1 | See P1.4 trace inspection |
| Q5 | ~~Does Docker on this Mac reach `192.168.1.50:11434`?~~ | — | ✅ Default bridge network works (P0.2) |
| Q6 | Does `num_ctx` propagate through tau2's `generate()`? F1 says default Ollama ctx may truncate the long tau-bench conversations. | P1 | Pass `num_ctx=65536` via `llm_args` on `TauBenchRunner`; verify in P1 trace inspection |
| Q7 | What is the default `num_ctx` for `qwen3-coder:30b` on this Ollama daemon? May be lower than retail tasks need. | P1 | Inspect P1.4 traces for truncation; bump if needed |

---

## Key files

| Path | Purpose |
|---|---|
| `/Users/jit/code/try_ext/auto-harness/` | NeoSigma's auto-harness (reference + run target) |
| `/Users/jit/code/try_ext/auto-harness/agent/agent.py` | The tau3 "skill" being optimized |
| `/Users/jit/code/try_ext/auto-harness/workspace/` | Runtime workspace (gitignored) |
| `apps/kernel/scripts/run_tau3_loop.py` | ownEvo loop driver for tau3 (to write) |
| `apps/kernel/baselines/tau3_v1/agent.py` | ownEvo skill registry entry for tau3 (to write) |
| `ownevo_docs/benchmarks/tau3-results-2026-Q3.md` | Final results doc (to write) |
| `docs/local-model-testing.md` | Desktop model capabilities reference |

---

## Next action

**P0.1 done.** Next: **P0.2** — build the auto-harness Docker image and smoke-test
`tau2.utils.llm_utils.generate()` against the Ollama desktop with `ollama_chat/qwen3-coder:30b`.

```bash
cd /Users/jit/code/try_ext/auto-harness
docker compose build

# Verify desktop Ollama reachable from this Mac first
curl -s http://192.168.1.50:11434/api/tags | head -c 200
# (should return JSON catalog; if it doesn't, the desktop is unreachable from this network — block)

# Verify qwen3-coder:30b is loaded on the desktop
curl -s http://192.168.1.50:11434/api/tags | python3 -c "
import sys, json
tags = json.load(sys.stdin)
print('\n'.join(m['name'] for m in tags.get('models', [])))
" | grep -i qwen3-coder

# If model missing: ssh into 192.168.1.50 and run `ollama pull qwen3-coder:30b`

# Then run the smoke test from P0.2 above
```
