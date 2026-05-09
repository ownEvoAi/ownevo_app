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
| **P1 — Condition A baseline** | Sonnet 4.6 on retail test split (40 tasks) → **val_score_A = 0.8000** | ✅ done | $9.27, 16 min |
| **P1.5 — Kernel migration** | Pull tau2 into `apps/kernel`, build native `TauBenchRunner` (`BenchmarkRunner` Protocol), register tau3-retail-v1 workflow + skill, ingest failure clusters, retire auto-harness dependency. M1-M10 substeps. | ☐ before P2 (must) | ~3-5 days CC |
| **P2 — Condition B autonomous loop** | qwen3-coder:30b local as loop agent; edits `agent/agent.py`; gates with NeoSigma's `gating.py`; **15-20 iterations** (matches Meta-Harness 20+ and NeoSigma 18) | ☐ | ~$45-90, ~5-10 hr |
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

**Status:** ✅ — `val_score_A = 0.8000`  
**Depends on:** sanity-C ✅ — completed end-to-end via auto-harness fork

### Result

| Metric | Value |
|---|---|
| **val_score_A** | **0.8000** (32 pass / 8 fail-or-error of 40) |
| Pass / fail / infra-err breakdown | 32 / 4 / 4 |
| Total cost | $9.27 ($0.23 / task average) |
| Wall time | ~16 min at concurrency=3 |
| Trace dir | `tau2_data/simulations/20260509_000808_retail_custom_agent_claude-sonnet-4-6_user_simulator_claude-haiku-4-5-20251001/` |

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
| **M1** | Add `tau3` extra to `apps/kernel/pyproject.toml`. Verify `uv sync --extra tau3` installs tau2. | XS — 15 min | tau2 import + version pinned |
| **M2a** | Build `apps/kernel/sandbox/Dockerfile.tau3` (python:3.12-slim + pinned tau2 + LiteLLM + kernel + retail/airline/telecom data). Build via Buildx with GHA cache `m5-sandbox`-style. | S — half day | `make sandbox-image-tau3` produces `ownevo-sandbox-tau3:0.1.0` |
| **M2b** | Extend `LocalDockerSandbox` to accept `SandboxProfile` (image, network, mem, timeout, extra_volumes). Implement `EGRESS_ALLOWLIST` network mode (Docker bridge + iptables OUTPUT chain or HTTP_PROXY env). | S — half day | M5 profile keeps current behavior; τ³ profile reaches Anthropic API but blocks arbitrary egress |
| **M2c** | Build `tau2_patches.py` consolidating the 4 monkey-patches (NL_ASSERTIONS + ENV_INTERFACE on both `tau2.config` and consuming modules). Bake into `Dockerfile.tau3` so they apply at sandbox boot. | XS — 30 min | One import patches everything; no per-call patching needed |
| **M3** | Build `TauBenchRunner` wrapping `tau2.run_domain`. Implements `BenchmarkRunner` Protocol. Takes `skill_override_dir` (M5 pattern). Runs INSIDE `LocalDockerSandbox` with the τ³ profile — invokes `tau2.run.run_domain` via the sandbox's stdin/stdout marshal pattern (mirrors `SandboxedM5BenchmarkRunner` from PR #11c). | M — 1 day | Substitutable for `M5BenchmarkRunner` in test fixtures; sandboxed run produces same val_score as auto-harness P1 ±5pp |
| **M4** | Build `tau3_retail_v1` baseline skill. Wrap auto-harness template content in SKILL_FORMAT frontmatter (id, kind=code, schema_version, retention). | XS — 30 min | `read_skill` tool returns it cleanly |
| **M5** | `scripts/tau3_register.py`: one-time CLI to register `tau3-retail-v1` workflow + initial skill version + 5-10 seed eval cases (sample of retail tasks expressed as `EvalCase` rows). | S — 2-3 hr | Workflow + skill + eval cases visible in DB |
| **M6** | `scripts/tau3_baseline.py`: run condition A frozen baseline via sandboxed `TauBenchRunner` + `persist_gate_run`. | S — 2-3 hr | iterations row + audit chain entries appear; matches auto-harness P1 val_score ±5pp (sanity check) |
| **M7** | `failure_analyzer.py`: parse sub-0.5 reward sims; extract `text_signature` (failed-tool-call signature, premature-termination signature, etc.). Mirrors `m5_failure_analyzer.py`. **Preserves full message history** (Meta-Harness +15.4pp ablation). | M — half day | Failure clusters from baseline run land in `failure_clusters` table |
| **M8** | `ingest.py`: backfill helper to read existing `tau2_data/simulations/<run_dir>/results.json` files and create iteration rows retroactively. | S — 2-3 hr | Existing 7 trace dirs from sanity runs become DB rows |
| **M9** | Extend `run_improvement_loop.py` with `--workflow tau3-retail` branch. Loop agent (qwen3-coder:30b on Ollama desktop) reads current skill + recent failure clusters via `analyze_failures`, proposes new skill version via `write_skill`. Gate runs sandboxed `TauBenchRunner` against test split. | M — 1 day | One end-to-end loop iteration on tau3 produces a valid `iterations` row |
| **M10** | Web UI: register tau3-retail workflow in the workspace nav. The W7-shipped detail pages (Health, Failures, Audit, Skills, Traces) work as-is once `workflow_id` is set. | S — 2-3 hr | `/workspaces/acme/workflows/tau3-retail-v1` renders the lift chart, audit chain, failure clusters, skill diff |

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

**Status:** ☐ not started  
**Depends on:** P1 exit gate passes

**Goal:** run ownEvo's improvement loop with `qwen3-coder:30b` as the loop agent, editing
`agent/agent.py`, gated by NeoSigma's `gating.py`. Target: 10-15 iterations, measure
lift from `val_score_A`.

### Architecture for condition B

```
qwen3-coder:30b (loop agent, Ollama 192.168.1.50)
  reads:  workspace/traces/latest/  (train failure traces)
  edits:  agent/agent.py            (system prompt + context builder)
  gates:  python gating.py          (NeoSigma's 3-step gate)
  records: workspace/results.tsv + ownEvo DB (iterations table)
```

Loop driver options (pick one):
- **Option A (faster):** Use Claude Code directly: `claude "Read PROGRAM.md and start the optimization loop"` inside the auto-harness Docker container. Claude Code uses Sonnet 4.6 cloud as the *loop agent* (proposes edits), qwen3-coder:30b is *only the task agent*.
- **Option B (all-local):** Wire ownEvo's `run_improvement_loop.py` adapted for tau3 skill format. Loop agent = qwen3-coder:30b on Ollama.

**Decision needed before starting P2:** Option A is faster but uses cloud for the loop agent.
Option B is fully local but requires building the tau3 skill adapter first (estimated 2-4h).

### Steps (Option A — Claude Code as loop driver)

- [ ] **P2.1** Verify Claude Code can access the Docker container's workspace (mount check).

- [ ] **P2.2** Run 10 iterations:
  ```bash
  # Inside auto-harness dir, with Docker workspace mounted
  claude "Read PROGRAM.md and start the optimization loop. Baseline is already recorded
  (iteration 0). Start from step 2 (analyze failures). Run 10 iterations then stop and
  summarize findings in workspace/learnings.md."
  ```

- [ ] **P2.3** After each gate-passing iteration, also record in ownEvo DB:
  ```bash
  # Map results.tsv iteration → ownevo iterations table
  # Script: scripts/tau3_record_iteration.py (to be written)
  ```

- [ ] **P2.4** Record `val_score_B` (best score after 10 iterations), lift = `(B-A)/A * 100`.

### Steps (Option B — all-local ownEvo loop)

- [ ] **P2A.1** Write `apps/kernel/scripts/run_tau3_loop.py`:
  - Wraps `run_improvement_loop.py` mechanics for tau3 skill format
  - Loop agent: qwen3-coder:30b on Ollama OpenAI (`--api-format openai --llm-base-url http://192.168.1.50:11434/v1`)
  - Reads failure traces from `workspace/traces/latest/`
  - Proposes edits to `agent/agent.py` (as a "skill" in SKILL_FORMAT kind=code)
  - Calls `gating.py` (NeoSigma's) for Step 1/2; also gates against ownEvo eval cases
  - Records to ownEvo DB

- [ ] **P2A.2** Define tau3 "skill" in SKILL_FORMAT:
  ```
  apps/kernel/baselines/tau3_v1/agent.py   ← initial HarnessAgent (from auto-harness template)
  ```
  Register as `skill_id=tau3-retail.v1`, `kind=code`.

- [ ] **P2A.3** Run loop: `make tau3-loop ITERS=10`

**Exit gate:** `val_score_B > val_score_A` (any lift). If no lift after 10 iterations,
examine learnings.md — failure modes may not be promptable with qwen3-coder:30b.

**Recording:** update this doc with `val_score_B`, iterations run, accepted/rejected counts,
top 3 changes that improved the score.

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
