# τ³-bench Local Model Test Plan

**Branch:** `feat/ollama-loop-runner` — local-only end-to-end τ³ retail (loop + task agent + user sim).
**Reference doc target:** `ownevo_docs/benchmarks/tau3-results-2026-Q3.md` (TBD).

## Current winners (production config, as of 2026-05-12)

**Proposer:** `qwen/qwen3.6-35b-a3b` on **LMS**, anthropic /v1/messages, **froggeric v13 template**, ctx=65536.
**Task agent + user simulator:** **same model** via `anthropic/qwen/qwen3.6-35b-a3b`.
**Wall-time / cost:** ~25-30 min per cycle, $0.
**Best val_score:** **0.8250** (Run 24 cycle 4; reproduced via 2 distinct skill patterns). Mean across 5-cycle scale-up = 0.7350.

**Alternative proposers (PASS but lower lift):**
- `glm-4.7-flash:latest` on **Ollama** (DeepSeek-2 arch) — Run 32 v2 PASS **0.6750**. Architecture diversity proven.
- `qwen/qwen3-coder-30b` LMS — Run 15 PASS but retail-weak (0.1250).
- `google/gemma-4-e4b` LMS — Run 12 PASS but weak (0.1750).

**Real task-agent ranking on retail τ³** (JIT-fallback discovery, 2026-05-12):
`qwen3.6-35b-a3b (0.75)` > `qwen3.5-9b (0.575)` > `gpt-oss-20b (0.30)` ≈ `qwen3.5-4b (0.22-0.30)`. **Bigger > smaller.** The earlier "4B > 9B > 35B inverse-scaling" claim (Runs 21/22 at 0.825/0.725) was invalidated when LMS JIT was discovered to silently route the invalid identifier `anthropic/qwen/qwen3.5-4b` to the loaded model (qwen3.6-35b-a3b). See § "Task-agent role compat" for the full record.

**Key infra knobs:**
- LMS: JIT loading **disabled**; v13 chat template applied to qwen3.5/3.6 family; ctx=65536.
- Ollama: `NUM_PARALLEL=1`, `KV_CACHE_TYPE=q8_0`, `FLASH_ATTENTION=1`, `MAX_LOADED_MODELS=1`, `CONTEXT_LENGTH=32768`, `GPU_COUNT=2`.
- Concurrency defaults in wrapper: LMS=4, Ollama=2 (override `OWNEVO_TAU3_CONCURRENCY`).

NeoSigma reference: 0.56 → 0.78 (+39.3%) on retail, fully autonomous, cloud GPT-5.4. ownEvo result on this branch: 0.75 baseline → 0.825 (+10pp), fully local, $0 per run.

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
| **Sanity-A/B/D — Local task agent (cloud-free attempt)** | Try qwen3-coder/ministral as τ³ task agent | ✅ done — all 0/3 (none cleared); retired for cloud baseline | $0 |
| **Sanity-C — Cloud task agent (baseline)** | Sonnet 4.6 + Haiku user sim end-to-end | ✅ done — 3/3 PASS | $0.67 |
| **P1 — Cloud Sonnet baseline** | Sonnet 4.6 on retail test split → **val_score = 0.8500** | ✅ done | $9.27 + ~$9, 16 min each |
| **P1.5 — Kernel migration** | tau2 into `apps/kernel`, native `TauBenchRunner`, tau3-retail-v1 workflow + skill | ✅ done | ~1 day |
| **P2 — Cloud autonomous loop** | Sonnet 4.6 as loop agent + Sonnet task agent (cloud); 14 cycles | ✅ done 2026-05-09: val=**0.9500** (+10pp over 0.85) | ~$50-80, 14 cycles |
| **P2-LOCAL — All-local autonomous loop (this branch's headline)** | qwen3.6-35b-a3b LMS as loop+task+user-sim, retail test split, 40 tasks | ✅ done 2026-05-12: val=**0.8250** (+10pp over 0.75 baseline); 5-cycle mean 0.7350; ceiling reached via 2 distinct skills | $0, ~25-30 min/cycle |
| **P3 — Gated loop (LLM-judge approval)** | LLM-judge approves/rejects each gate-passing proposal | ☐ deferred — post-merge | TBD |
| **P4 — Results doc + Pass³ stretch** | `tau3-results-2026-Q3.md` + Pass³ re-runs | ☐ deferred — post-merge | XS-S |

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

## Archived phase notes (compressed 2026-05-12 for merge — full text in git history)

**Phase 0 + Sanity-A/B/C/D (2026-05-08).** Plumbing verified. tau2 routes LLM calls through LiteLLM (`ollama_chat/` prefix + `OLLAMA_API_BASE` env, or `openai/` + `OPENAI_API_BASE`, or `anthropic/` + `ANTHROPIC_API_BASE` for LMS Anthropic-compat). Cloud Sonnet 4.6 + Haiku user-sim cleared 3/3 retail train tasks ($0.67). All-local first attempts (Sanity-A/B/D — `qwen3-coder:30b` Ollama, `qwen3-coder:30b` LMS, `mistralai/ministral-3-14b-reasoning` LMS) all 0/3 — retired in favor of cloud baseline first, all-local proven later (see P2-LOCAL).

**Phase 1 — Condition A cloud baseline (2026-05-08).** Sonnet 4.6 on retail test split (40 tasks, kernel substrate, post-tau2 patches) → val_score = **0.85**. Earlier auto-harness 0.80 superseded. Wall-time ~16 min, ~$9.27 baseline + ~$9 per gate eval.

**Phase 1.5 — Kernel migration (2026-05-09).** tau2 pulled into `apps/kernel/baselines/tau3_v1/`. Native `TauBenchRunner` implements the `BenchmarkRunner` Protocol. Workflow `tau3-retail-v1` + skill `tau3.retail.baseline.v1.agent` registered. Failure-cluster ingestion wired. Docker sandbox `ownevo-sandbox-tau3:0.1.0` is baked with the tau2 patches (notably `tau2_patches.py:_patch_litellm_ollama_think_off`). Auto-harness dependency retired.

**Phase 2 — Condition B cloud autonomous loop (2026-05-09).** Sonnet 4.6 as both loop driver and task agent. 14 cycles total. **Batch 1 best: skill v38 — val_score = 0.9500 (+10pp over 0.85 baseline).** The winning change was prompt-only: *"only use parameters defined in this method's signature; never slice message history mid tool_use/tool_result pair."* Cost ~$50-80. This is the lift we then attempted to reproduce all-locally (see P2-LOCAL below — achieved 0.825 vs 0.95, +10pp over local baseline 0.75).

**P2-LOCAL — All-local autonomous loop (this branch's headline, 2026-05-09 → 05-12).** 38+ runs across LMS / Ollama, 6 confirmed PASSes, 0.825 record. Detailed run log lives in `STATUS.md`; the load-bearing model-selection findings live in § "Local LLM compat matrix" + § "Task-agent role compat" below. JIT-fallback discovery on 2026-05-12 invalidated the early "inverse scaling 4B > 9B > 35B" claim — see § "Task-agent role compat" for the corrected ranking.

**Codegen-quality lessons for local proposers (2026-05-10, gemma4:26b multi-cycle):**
1. **Parameter cross-contamination** — gemma4 rewrote `get_init_state` using `message` (a param from `generate_next_message`, not defined in this method). NameError on every task. Prompt nudge: *"When rewriting a method, only use parameters defined in that method's signature."*
2. **Naive message truncation** — `state.messages[-15:]` sliced mid tool_use/tool_result pair → Anthropic `unexpected tool_use_id` validation. Prompt nudge: *"Never slice message history at an arbitrary index — tool_result blocks must immediately follow their matching tool_use block."*
3. **Pattern** — different codegen bug each cycle. Not a single fixable rule. Most local 8B-30B proposers fail this bar; **validator chain** shipped 2026-05-12 (commits `aaa9fef` write_skill module-load check, `08f2249` class/method presence checks, `58cf93a` one-task pre-eval smoke with task_id fallback list) catches them before expensive gate eval.

---

**Concurrency defaults (wrapper `tau3_p2_local_loop.sh`, 2026-05-12):** the wrapper now picks `--task-concurrency` from the preset:

| Preset | Default | Rationale |
|---|---|---|
| `lms-openai`, `lms-anthropic` | **4** | LMS KV-cache + multi-stream tolerates 4 well |
| `ollama`, `ollama-openai` | **2** | Ollama is throughput-bound (`NUM_PARALLEL=2`); 3+ creates retry-stall |
| explicit `http://` URL | 3 | unchanged fallback |

Override with `OWNEVO_TAU3_CONCURRENCY=N`.

Results land in `<repo>/log/tau3_p2/sweep_results.tsv` (sweep) or per-cycle log files (multi-cycle). Older runs may still be under `/tmp/tau3_p2_logs/` from before the log-dir migration.

---

## Local model selection — reference data

The matrix + role-compat + all-3-roles record below are the load-bearing artifacts of this branch. Update after every sweep. Future contributors should read these three sections before queuing new runs.

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
| qwen3-coder:30b | — | ⚠ ¹ | — | ⚠ ² | ¹ requires `/no_think` auto-injection (runner.py); +14.9% on TODO-19, F6 7/7 on W6 v5. **2026-05-10 tau3-retail smoke** `qwen3coder_full_local` (all-3-roles all-Ollama): loop drove cleanly, infra mostly healthy, but task-agent quality is weak — got to 26/40 with avg reward 0.15 in ~115 min before killed. One `500 \| 10m0s` Ollama timeout at minute ~52 (think:false patch mostly holding but not 100%). Task 39 stuck on initial attempt for 54 min. **Verdict: viable as loop driver (codegen specialist, will write clean Python proposals) but POOR as retail task agent.** Use mixed: loop=qwen3-coder Ollama + task=LMS qwen3.6-35b-a3b. ² LMS-Anthropic: 14/14 deterministic `_long_frame` codegen bug (TODO-20). |
| qwen/qwen3-coder-30b (LMS) | — | — | ✅ ¹ᵇ | — | ¹ᵇ **2026-05-12 smoke** `qwen3coder_30b_lms_full_local_64k` (all-3-roles, ctx=65536): **end-to-end PASS**, val_score = **0.1250**, 40/40 evaluated, 0 infra_errors. Loop: 7 iters / 7 tool_calls / 3 tool_errors, end_turn. ~30 min wall-time. LMS KV cache solves the throughput trap that hit Ollama version (sandbox pace ~48s/task vs Ollama's 4-min/task). But **retail reward stays weak (0.13 vs Ollama's 0.15)** — confirms qwen3-coder is structurally retail-weak regardless of backend, not just an Ollama-specific quirk. Useful as proposer in mixed topology, NOT as task agent. Proposal v_seq=148. |
| qwen3.6-35b-a3b (LMS) | — | — | ✅ ³ | ✅ ³ᵇ | ³ drove loop, hit val=0.85 ×2 in multi-cycle. Thinking embedded too deep for `/no_think` to override (LMS strips thinking client-side). ³ᵇ 2026-05-10: works after runner.py `_run_turn_no_stream` fix (commit `4202f1e`); cache_read_input=31491 confirms LMS auto-cache. **Cross-quant validation (2026-05-12):** `unsloth/qwen3.6-35b-a3b -c 65536` all-3-roles smoke ran 39/40 with avg reward 0.77 — equivalent to qwen/ quant's 0.75 (well within noise). Gate-rejected by 1 task hitting 4hr per-task wall (task 101 retry pattern: 44min initial + 70min R1 + R2 starting → 14400s timeout). Confirms cross-quant generalizability of the val_score = 0.75 win. |
| qwen3.6:35b-a3b (Ollama) | ✅ ³ᶜ | ✗ ³ᵈ | n/a | n/a | ³ᶜ 2026-05-10 smoke: native `/api/chat` works because `OllamaChatClient` auto-injects `options.think=false` (ollama_native.py:209). Loop drove cleanly: 5 iters, 7348 out, end_turn. ³ᵈ openai-compat strips think:false silently → verbose thinking → 16501 out tokens → DEFAULT_MAX_TOKENS_OPENAI cap hit in 2 iters. |
| qwen3.6:27b (Ollama) | ⚠ ³ᵉ | — | n/a | n/a | ³ᵉ **Run 23 v1 (2026-05-12T02:35Z):** `httpx.ReadTimeout` — 27B DENSE model (17.4GB) needs ~5 min disk load + 3-5 tok/s generation; exceeded 600s timeout. **Fix:** `DEFAULT_TIMEOUT_SECONDS` bumped 600s → 1800s (commit `9a700f1`). **Run 23 v2 (2026-05-12T02:56Z → 03:38Z):** PASS, val_score=0.6750, 40/40, 0 infra. Loop: 5 iters, v_seq=169. Note: `think:false` REJECTED by this Ollama build (`invalid option provided option=think`) → model ran with full thinking chain (uncontrolled). First call: 9m23s (disk load + dense generation). **Proposer quality: 0.6750 < 0.8250 (MoE 35b-a3b LMS, Run 21).** Dense 27B Ollama with uncontrolled thinking is a weaker proposer than MoE 35b-a3b LMS. Confirmed viable but suboptimal. |
| qwen/qwen3.6-27b (LMS) | n/a | n/a | — ³ᶠ | — | ³ᶠ **Pending Run D (2026-05-12).** Model is local (17.48GB, dense `qwen35` arch). Froggeric chat template applied in LMS UI 2026-05-12 (same override as qwen3.6-35b-a3b). Plan: `qwen/qwen3.6-27b` as proposer (lms-openai, ctx=65536) + `anthropic/qwen/qwen3.5-4b` as task agent (proven best in Run 21). Key question: does LMS thinking-suppression close the Ollama gap (0.6750 → ~0.8250)? Architecture is dense (not MoE) — proposer quality expected to be lower than 35b-a3b MoE even with clean thinking suppression. |
| qwen3.5-9b | — | ✗ ⁴ | ✗ ⁴ | ✅ ⁴ | ⁴ F14g — 0/3 via OpenAI, 3/3 via Anthropic. API-format-load-bearing. **2026-05-11 tau3-retail mixed smokes**: `ollama_chat/qwen3.5:4B` and `ollama_chat/qwen3.5:9B` BOTH fail with `litellm.APIConnectionError "Unsupported Media Type"` (HTTP 415 from Ollama after 4 retries) → 40/40 infra → SANDBOX_ERROR. Deterministic and model-size independent; **`ollama_chat/qwen3.5:*` track CLOSED** pending upstream LiteLLM ollama_chat adapter fix. **`anthropic/qwen/qwen3.5-9b`** (LMS /v1/messages, froggeric v13 template, ctx=65536): ✅ Run 28 PASS val_score **0.5750**, 40/40 clean — real 9B (JIT disabled). **Note:** the earlier Run 21/22 attribution to `anthropic/qwen/qwen3.5-4b` (0.825/0.725) was invalidated by JIT-fallback discovery (2026-05-12) — that identifier does not exist in LMS and JIT silently served the loaded qwen3.6-35b-a3b. See § "Task-agent role compat" row for `anthropic/qwen/qwen3.5-4b` and the JIT-fallback note for the corrected ranking. **Real ranking: bigger > smaller for retail task agent.** Untested: `openai/qwen3.5:9B` via Ollama /v1 (`OPENAI_API_BASE=http://LLM_HOST:11434/v1`) — post-merge. |
| qwen3:30b-a3b | ⚠ ⁴ᵇ | — | — | — | ⁴ᵇ **2026-05-11 tau3-retail smoke** `qwen3_30b_a3b_full_local` (all-3-roles all-Ollama, native preset with `think:false` patch on both sides): same throughput trap as qwen3.6:35b-a3b. Killed at 1/40 in 25 min, reward 0.00 (N=1). Task 5 stuck 22 min on initial attempt. 17-40s per `/api/chat` call. think:false patch holds (no 500s) but per-call latency × no KV-cache-reuse × NUM_PARALLEL=2 makes wall-time unviable. qwen3 family confirmed to share the qwen3.6 family bottleneck on Ollama. **Important: failure was as TASK AGENT (throughput-bound multi-turn). As LOOP PROPOSER (single-stream), throughput is not a bottleneck — planned as Run F: Ollama native proposer + `anthropic/qwen/qwen3.5-4b` LMS task.** |
| qwen/qwen3-30b-a3b (LMS) | n/a | n/a | — ⁴ᵈ | — | ⁴ᵈ **Pending Run E.** Same MoE architecture as qwen3.6-35b-a3b winner (30B-A3B, 18.63GB local). Also `qwen/qwen3-30b-a3b-2507` variant (18.56GB). Plan: as proposer (lms-openai, ctx=65536) + `anthropic/qwen/qwen3.5-4b` task agent. Tests whether qwen3 base MoE is comparable to qwen3.6 MoE for proposer quality. Never tried as proposer. |
| qwen3:30b-instruct | ⚠ ⁴ᶜ | — | — | — | ⁴ᶜ **2026-05-11 tau3-retail smoke** `qwen3_30b_instruct_full_local` (all-3-roles all-Ollama, native preset, think:false on both sides): dense (not MoE) — fastest Ollama start so far (19/40 in 26 min, /api/chat 13-16s). But got stuck on task 49 retry R1 for 33+ min while reward stalled at 0.36 (N=22). Killed at 22/40 after ~53 min. Best Ollama reward signal aside from gpt-oss (0.36) but task 49 burning a concurrency slot indefinitely means the 4 hr per-task timeout would have to fire before completion. Same retry-stall pattern as other all-Ollama configs, just at higher reward. **As PROPOSER only (Run G): stall was task-agent side — as single-stream proposer, stalls can't happen. Good candidate to test with `anthropic/qwen/qwen3.5-4b` LMS task.** |
| qwen3:32b | — | ⚠ ⁵ | — | — | ⁵ hallucinated `AGENT_REASONING_EFFORT` env var; needs prompt nudge. |
| qwen2.5-coder:32b | — | 🚫 ⁶ | — | — | ⁶ doesn't trigger tool calls with `tool_choice=auto`. |
| Qwq:32b | — | — | — | — | reasoning model; would route via `ollama_chat/`. Untested. |
| gpt-oss:20b | — | ⚠ | — | — | **2026-05-11 smoke** `gptoss20b_full_local` (all-3-roles all-Ollama, ollama-openai preset): killed at 11/40 in ~80 min. Avg reward 0.36 (N=11) — promising signal. 0 infra errors. Per-call latency wildly variable: 18s–7m36s. gpt-oss uses `reasoning_effort` (not thinking blocks), so the `think:false` patch in `tau2_patches.py` doesn't apply. Task 5 stuck on initial attempt for 35 min (no retry). Wall-time unviable for full 40-task sweep. **Worth retrying with `reasoning_effort=low`** if we plumb that knob through the runner; otherwise treat as too slow for tau-bench. (120B variant skipped per user direction — too large for current VRAM topology.) |
| gemma4:26b | ⚠ ⁷ᵇ | ✅ ⁷ | — | — | ⁷ 2026-05-10 sweep P1.3 + P2.3: drove loop cleanly (`end_turn`, 5-9 iters, valid proposals v_seq=84 + 95) **when task agent is something else**. ⁷ᵇ native `/api/chat`: smoke `gemma4_full_local` 2026-05-10 ran fast (~14 min total) but generated a Python typo `MultiToolcalMessage` (missing "l") in cycle-1 proposal → all 40 tau2 retail tasks crashed with `NameError: name 'MultiToolcalMessage' is not defined. Did you mean: 'MultiToolMessage'?` → 40/40 infra_errors. So `gemma4:26b` is **viable as loop driver only when paired with a different task agent**; as all-3-roles all-Ollama it crashes its own proposal. The httpx.ReadTimeout was fixed (ollama_native.py 300→600s, commit `30a61a8`) and didn't surface this time. |
| google/gemma-4-26b-a4b (LMS) | — | — | ✗ ⁸ | ✗ ⁸ | ⁸ 2026-05-10 sweep P1.2 + P2.2 (4 attempts both APIs): `stop_reason=max_tokens` after only 1061-7348 output tokens — model emits brief output then stops mid-iteration. Suspect LMS-side `max_completion_tokens` setting or quant tendency. **Planned retry (Run B):** `lms load google/gemma-4-26b-a4b -c 32768` + **set `num_predict` ≥ 16384 in LMS UI for this model before loading** (same `num_predict` fix applied to other models with max_tokens cap). MoE `gemma4` architecture: 26B-A4B = ~4B active params. Context is 32K (not 65K) because task 36 only failed at 65K on qwen3.6 — gemma4 has different conversation lengths. |
| google/gemma-4-31b (LMS) | — | — | ⚠ ⁸ᵃ | — | ⁸ᵃ **2026-05-11 smoke** `gemma4_31b_full_local_64k` (all-3-roles, ctx=65536, ~2h32m): loop drove cleanly (7 iters, 0 tool_errors), avg reward 0.62 (N=36). Gate=SANDBOX_ERROR — 4/40 infra_errors on tasks 55, 56, 60, 61 (LMS HTTP 500 `"Failed to resolve model metadata for google/gemma-4-31b."` — intermittent LMS registry failure under sustained load). Dense 31B DOES avoid the MoE max_tokens cap that killed gemma-4-26b-a4b. **Planned retry (Run C):** same config, retry — failure was infra-flaky not model-quality. |
| google/gemma-4-e4b (LMS) | — | — | ✅ ⁸ᵇ | — | ⁸ᵇ **2026-05-12 smoke** `gemma4_e4b_full_local_64k` (all-3-roles, ctx=65536): **end-to-end PASS**, val_score = **0.1750**, 40/40 evaluated, 0 infra_errors. Loop: 6 iters / 5 tool_calls / 2 tool_errors, end_turn. Wall-time ~39 min. Smaller gemma (7.5B, e4b=4B active) dodges the max_tokens cap that killed gemma-4-26b-a4b. Retail reward weak (0.18) vs qwen3.6 winner (0.75) but the loop+agent path is fully clean — useful "smallest-viable" baseline. Proposal v_seq=141. |
| granite4.1:8b | — | 🚫 ⁹ | — | — | ⁹ generates U+2013 em-dash → SyntaxError (A4.4 gate). Useful only as task agent / user-sim, not loop driver. |
| granite-4.1-8b (LMS) | — | — | ⚠ ¹⁰ | — | ¹⁰ A4.4 fastest desktop 3/3 (33s). **As LOOP DRIVER: too weak** — 2026-05-11 smoke `granite_full_local_64k`: loop ran cleanly (4 iters, 3 tool_calls, 0 tool_errors, end_turn) but **did NOT emit any `write_skill` call** → "error: loop agent did not register any skill change; nothing to gate". 8B params is structurally insufficient for the meta-task of proposing a skill patch. **As TASK AGENT: viable but slow** — at ctx=16384 the LiteLLM path is clean (no infra errors after 2026-05-11 diagnosis fix). Tested in mixed run `qwen36loop_graniteagent_64k_smoke` (loop=qwen3.6 + task/user=granite-8B): 4/40 in 30 min, avg reward 0.50, ETA ~11hr — too slow per-task at concurrency=3. Need bigger granite for proposer role; need different agent or lower concurrency for task role. |
| granite4.1:30b | — | 🚫 ¹¹ | — | — | ¹¹ read skill, never wrote — gave up. |
| unsloth/granite-4.1-30b (LMS) | — | — | 🚫 ¹¹ᵇ | — | ¹¹ᵇ **2026-05-12 smoke** `granite_30b_full_local_64k` (all-3-roles, ctx=65536): loop ran 7 iters, end_turn, **emitted write_skill** (v_seq=143) — confirms granite-30B is stronger than granite-8B (which emitted 0). BUT proposal was structurally broken: `agent.py` written WITHOUT a `HarnessAgent` class → sandbox import failure → 40/40 blocked at eval setup → SANDBOX_ERROR. Same family of failure as gemma4:26b's `MultiToolcalMessage` typo. Codegen quality too low for self-driven proposer role. Mixed topology (different proposer + granite-30B as task agent) untested. |
| devstral-small-2:latest | — | 🚫 ¹² | — | — | ¹² runnable Python, but `run_pipeline` validation rejects every diff (TODO-21). |
| mistralai/devstral-small-2-2512 (LMS) | — | — | 🚫 ¹³ | — | ¹³ tool-error storm — codegen quality too low. |
| mistralai/ministral-3-14b-reasoning (LMS) | — | — | 🚫 ¹⁴ | — | ¹⁴ chat-template strict alternation — template incompat. |
| zai-org/glm-4.7-flash (LMS) | — | — | 🚫 ¹⁵ | — | ¹⁵ **Run 30 (2026-05-12T18:14Z → 18:27Z):** httpx.ReadTimeout in proposer phase first call (AsyncOpenAI 600s default trip). **Fixed in commit 8307385** (1800s timeout). **Run 31 retry (2026-05-12T18:50Z, killed at 3 min)** after web search revealed **known LMS-side bugs with glm-4.7-flash:** (a) LMS's bundled llama.cpp lacks full glm-4.7 architecture support — users told to use llama.cpp directly until LMS updates; (b) tool-call / freezing bugs with default sampling params (`--temp 0.7 --min-p 0.0 --top-p 0.80 --top-k 20 --repeat-penalty 1.05`) — works only with these removed; (c) MTP (multi-token-prediction) drops throughput 10×. **Verdict: glm-4.7-flash on LMS = blocked on upstream LMS update.** Use **Ollama** instead (full upstream glm-4.7 support; Run 32 v2 in flight using `glm-4.7-flash:latest` 19 GB on Ollama). Sources: [Unsloth glm-4.7-flash docs](https://unsloth.ai/docs/models/tutorials/glm-4.7-flash), [HF Jan-21 reupload thread](https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF/discussions/10). |
| glm-4.7-flash (Ollama) | ⚠ ¹⁵ᵇ | — | — ¹⁵ᵇ | — | ¹⁵ᵇ **Run 32 v1 (2026-05-12T18:55Z → 19:09Z, killed):** Ollama loaded glm-4.7-flash with **18% CPU / 82% GPU spill** — 19 GB model + LMS qwen3.6-35b-a3b 22 GB = 41 GB > single-GPU. Proposer 3-10× slowed by CPU-resident layer. **Run 32 v2 (2026-05-12T19:11Z, in flight)** after user reconfigured Ollama daemon: `OLLAMA_NUM_PARALLEL=1 KV_CACHE_TYPE=q8_0 FLASH_ATTENTION=1 MAX_LOADED_MODELS=1 CONTEXT_LENGTH=32768 GPU_COUNT=2` — second GPU lets glm-4.7 stay fully on-device. Tests glm-4.7-flash as proposer with qwen3.6-35b-a3b LMS task/user. |
| qwen/qwen3-30b-a3b-2507 (LMS) | — | — | — ⁴ᵈ | — | See qwen/qwen3-30b-a3b row above — same architecture, 2507 is a newer release. Either variant acceptable for Run E. |

**Rules:**
1. Don't re-run 🚫 cells — root cause is template / model architecture, not flaky.
2. Re-running ✗ requires changing the failing condition (longer context, different prompt, kernel patch). Note the condition change in the cell.
3. Adding a new model → run all 4 cells unless an entry above proves a path is irrelevant (e.g. LMS-only model can't use Ollama). Cost of one extra cycle ≪ cost of debugging silent regressions.
4. Tool-calling + thinking-flag behavior is the *primary* signal — codegen quality only matters if those are clean.

### Task-agent role compat (added 2026-05-10)

The matrix above measures **loop-driver capability**. A model that drives the loop cleanly may still fail as a **task agent** (the retail tau-bench solver inside the gate sandbox). The retail conversation pattern hits different code paths and template branches. Surfaces seen so far:

| Model (as task agent via LiteLLM) | Result | Failure mode |
|---|:-:|---|
| `openai/qwen/qwen3.6-35b-a3b` (LMS, default template) | ✗ | LMS jinja: `"No user query found in messages"` — 40/40 infra errors. The retail evaluator's first message structure trips the model's bundled template (P1.1, sweep 2026-05-10). |
| `anthropic/qwen/qwen3.6-35b-a3b` (LMS, default template) | ✗ | **Same jinja error** via `/v1/messages`. Server-side template, API-agnostic. Routing prefix doesn't help (P1 rerun, 2026-05-10). |
| `openai/qwen/qwen3.6-35b-a3b` (LMS, **froggeric chat_template-v12.jinja override** + ctx=32768) | ⚠ | **Jinja fix landed 2026-05-10.** Smoke `qwen36lms_v12template_smoke` ran 39/40 tasks cleanly: avg_reward 0.69 (N=36 mid-run, final N=39). Loop drove to `end_turn` in 9 iters. **Gate=SANDBOX_ERROR / val_score=None** because task 36 hit `BadRequestError: Context size has been exceeded` after 4 retries — gate rejects any cycle with infra_errors > 0. Need ctx ≥ 65536 to cover the long-tail retail conversation. |
| `openai/qwen/qwen3.6-35b-a3b` (LMS, v12 template + **ctx=65536**) | ✅ | **2026-05-11 smoke `qwen36lms_ctx65k_smoke` — FIRST END-TO-END LOCAL VAL_SCORE.** All 40 tasks evaluated cleanly, 0 infra_errors, **val_score = 0.7500** (gate=PASS, best_ever_after=0.7500, proposal `v_seq=133`, iteration `gate-pass`). Loop: 7 iters / 7 tool_calls / 1 tool_error, end_turn. Wall-time ~27 min. Bumping ctx 32K→65K covered the long-tail conversation that hit `Context size has been exceeded` at 32K. **Confirmed-viable path** for local-only τ³ retail. |
| `ollama_chat/qwen3.6:35b-a3b` (Ollama) | ⚠ throughput | Infra path FIXED 2026-05-10: `tau2_patches.py:_patch_litellm_ollama_think_off` monkey-patches LiteLLM to inject `options.think=false` for `ollama_chat/qwen3*` models (without it, every `/api/chat` returned `500 \| 10m0s` from unbounded thinking traces). With the patch, `/api/chat` calls succeed cleanly at 12-25s each. **But throughput is unviable as a task agent.** Rerun `qwen36ollama_rerun_postreboot` (2026-05-10, post-reboot): only **1/40 tasks complete after 30 min**, task 5 stuck on R1 for 16+ min. Extrapolates to ~18 hr per cycle. Killed at 30 min. Root cause: Ollama doesn't auto-cache KV across turns the way LMS does — every `/api/chat` reprocesses the full conversation context. Combined with `_p.sh` config `NUM_PARALLEL=2`, only 2 of 3 concurrent task slots fit on GPU. **Recommendation:** use Ollama for the LOOP role (single-stream, fewer turns), keep task agents on LMS or use non-thinking models (gemma4) on Ollama. |
| `openai/granite-4.1-8b` (LMS, **ctx=4096 default**) | ✗ | **Diagnosed 2026-05-11:** the 40/40 "OpenAIException" was NOT a LiteLLM strict-validation issue. The actual error from the cycle log: `OpenAIException - Error code: 400 - {'error': 'The number of tokens to keep from the initial prompt is greater than the context length (n_keep: 5228 >= n_ctx: 4096). Try to load the model with a larger context length...'}`. tau-bench retail system prompt is ~5228 tokens, granite's default LMS load is ctx=4096 — every call 400s instantly. Same root cause as the original qwen36 / glm-4.7-flash failures. |
| `openai/granite-4.1-8b` (LMS, **ctx=16384**) | ✅ | **Verified 2026-05-11**: `lms load granite-4.1-8b -c 16384` unblocks the path. Single-turn + multi-turn + long-system-prompt all clean through LiteLLM (`tool_calls` finish_reason, valid args, 1803 prompt_tokens consumed). Already in `tau3_p2_local_sweep.sh phase3_full_lms_sweep` (commit `b36bc86`). End-to-end retail val_score TBD pending smoke run. |
| `anthropic/granite-4.1-8b` (LMS) | — | Untested. Probably also works at ctx=16384, but openai/ path now confirmed-viable so this is just a redundancy check. |
| `ollama_chat/qwen3-coder:30b` (Ollama) | ⚠ | **2026-05-10 smoke** `qwen3coder_full_local`: weak on retail conversation. 26/40 evaluated at avg reward 0.15 (vs 0.69 for LMS qwen3.6) before killed at ~115 min. 1× `500 \| 10m0s` Ollama timeout suggests think:false patch doesn't catch 100% of qwen3-coder generations. Task 39 stuck on initial attempt for 54 min (no retry letter — long single conversation or stuck recovery from the 10m timeout). Codegen-tuned models trade conversational ability for Python quality. |
| `openai/granite-4.1-8b` (LMS, ctx=16384) **as task agent in mixed run** | 🚫 UTF-8 surrogate bug | **2026-05-12 Run 37 v2 `qwen36_loop_granite8b_task_smoke_c4_v2`**: smoke-rejected (rc=9) in ~4 min — `litellm.InternalServerError: 'utf-8' codec can't encode characters in position 310-311: surrogates not allowed`. Same family bug as `granite4.1:8b` em-dash issue. **Verdict: task-agent-unviable** on the openai/LMS path. Earlier 2026-05-11 smoke (`qwen36loop_graniteagent_64k_smoke`, c=3, 4/40 in 30 min @ avg 0.50) ran without surrogate errors at ctx=16384 — implies either codepath drift or a tokenizer-state-dependent surrogate emission. Either way: don't retry without fixing the unicode-escape sanitization before LiteLLM payload. |
| `ollama_chat/granite4.1:8b` (Ollama, NUM_PARALLEL=4 + c=4) **as task agent in mixed run** | 🚫 retail-weak | **2026-05-12 Run 38 v2 `qwen36_loop_granite8b_ollama_task_smoke_c4`** (killed at 20/40 @ avg **0.10**, 27 min): UTF-8 surrogate bug ABSENT (Modelfile template avoids the LMS bundled-jinja bug). But trajectory locked at ceiling **~0.10-0.12** — granite4.1:8b is the **WEAKEST task agent in this branch**, below gpt-oss-20b (0.30) and real qwen3.5-4b (~0.22-0.30). Granite4.1:8b also hits `Simulation terminated prematurely. too_many_errors` at conv depth ≥10 (4+ tasks in this run). **Verdict: granite family retail-unviable as task agent regardless of stack.** Skip granite4.1:3b and (lower priority) granite3.3:8b retries. Prefer MoE Ollama variants (qwen3:30b-a3b) for task-agent role going forward. Run 38 v1 (NUM_PARALLEL=1, killed at 4/40 @ 0.50) was misleading on small sample. |
| `anthropic/qwen/qwen3.5-4b` (LMS) | ❌ **INVALID IDENTIFIER** | **2026-05-12 discovery:** this identifier **does not exist** in LMS (only `qwen3.5-4b` no-prefix and `qwen/qwen3.5-9b` with prefix are valid). Run 21's 0.8250 was generated with JIT enabled, so this name silently fell back to whatever was loaded (`qwen/qwen3.6-35b-a3b`). The "inverse scaling 4B > 9B > 35B" claim is invalidated. Real `qwen3.5-4b` (loaded, JIT disabled) tested 2026-05-12T06:46Z: avg reward **0.30** at N=10 (Run 21 was ~0.80 at N=10) — real 4B is significantly worse, not better. |
| `ollama_chat/devstral-small-2:latest` (Ollama) | ⚠ retail-capable, full-eval-infeasible | **Run 39 (2026-05-12T23:02Z → 23:20Z, killed at 4/40):** Contaminated by proposer bug. Infra-viable. **Run 44 (TASK_TIMEOUT=2400s):** SANDBOX_ERROR, partial avg_reward=0.33 (N=3) at 5/40. **Run 45 (TASK_TIMEOUT=7200s, 2026-05-13T04:22Z → 06:25Z, ~2hr):** SANDBOX_ERROR again — 10/40 complete, avg_reward=0.33 (N=6). Individual tasks hit R2/R3 retries; task 27 ran ~1800s on R1, task 38 reached R3. Root cause: devstral's response quality triggers tau2 retries frequently; at c=2 a single 30+ min task blocks a slot indefinitely. **Final verdict: retail-capable at ~0.33 (3 consistent measurements: Runs 39/44/45), but full-eval-infeasible — retry depth makes TASK_TIMEOUT=7200 insufficient for 40/40 completion. Do not retry further.** Comparable to gpt-oss-20b (0.30) and real qwen3.5-4b (~0.22-0.30). |
| `ollama_chat/qwen3:30b-a3b` (Ollama) | 🚫 thinking-bound | **Run 40 (2026-05-12T23:25Z, killed at smoke ~450s):** qwen3:30b-a3b is a thinking model (qwen3moe family, 30.5B Q4_K_M). Sandbox LiteLLM path does NOT inject `think:false` for `ollama_chat/` models generically — the existing `tau2_patches.py` patch only covers qwen3* models that match the specific prefix, and qwen3:30b-a3b burns unbounded thinking tokens on every task turn (~450s/smoke task). Throughput projection: 40 tasks × 450s / c=2 ≈ 9000s (2.5 hr), far exceeds TASK_TIMEOUT=2400s. **Verdict: qwen3moe family on Ollama as task agent is thinking-bound unviable without sandbox-side `think:false` injection fix.** |
| `ollama_chat/gemma4:e2b` (Ollama) | ✗ retail-weak | **Run 41 v1 (2026-05-12T23:37Z, rc=9):** OLLAMA_API_BASE routing bug — wrapper set default to LMS port (1234) instead of Ollama port (11434) when proposer is lms-anthropic. **Fixed:** `tau3_p2_local_loop.sh` now defaults `OLLAMA_API_BASE=http://${LLM_HOST}:11434`. **Run 41 v2 (2026-05-12T23:42Z → 05-13T00:31Z):** Smoke PASSED (infra routing fix confirmed). Full eval: SANDBOX_ERROR, val_score=None. Observed avg_reward=0.00 across all 3 completed tasks (N=3). TASK_TIMEOUT=2400s budget exhausted before full 40-task eval could complete (proposer ~8 min + smoke ~2 min = 600s overhead; 40 tasks at 90-120s each at c=2 needs ~2100s but only ~1800s remained). **Verdict: gemma4:e2b infra-viable on Ollama but retail-weak (~2B active params; 0.00 reward on multi-turn retail conversations).** |
| `ollama_chat/gemma3:12b` (Ollama) | 🚫 no tool support | **Run 42 (2026-05-13T02:41Z, rc=9, ~4 min):** `litellm.APIConnectionError: Ollama_chatException - {"error":"registry.ollama.ai/library/gemma3:12b does not support tools"}`. Prior-generation gemma3 lacks tool-calling capability in Ollama's Modelfile template. Hard API rejection — no `ollama_chat/` workaround. gemma4 family (e2b, 26b, 31b) added tool support that gemma3 lacks. **Verdict: task-agent-unviable.** |
| `ollama_chat/gemma4:26b` (Ollama) | ✗ retail-weak + too slow | **Run 43 (2026-05-13T02:48Z → 03:36Z, ~48 min):** Smoke PASSED (Ollama routing confirmed working, v_seq=233). Full eval: SANDBOX_ERROR, val_score=None. Tasks took ~360-380s each at c=2 (vs 90-120s for cloud Sonnet, 90-120s for gemma4:e2b). TASK_TIMEOUT=2400s budget exhausted — only ~5-7 tasks completed before timeout. All completed tasks: avg_reward=0.00, termination reason `max_steps` (simulation hit step limit without completing task). Despite 4B active MoE params (vs ~2B for e2b), retail performance is identical. **Verdict: gemma4 family (e2b and 26b) retail-weak as task agent — 0.00 avg reward at any MoE scale tested. gemma4:26b additionally too slow for TASK_TIMEOUT=2400s.** |
| `anthropic/qwen/qwen3.5-9b` (LMS, v13 template, **ctx=65536**) | ✅ working, weak | **Run 28 (2026-05-12T17:09Z → 17:31Z):** PASS val_score=**0.5750**, 40/40 clean. With JIT disabled + explicit prior load (real 9B served, not JIT-fallback). Confirms **bigger > smaller** for retail τ³ task agent: 0.5750 (9B) vs 0.7500 (35b-a3b baseline). **Run 22's reported 0.7250 was JIT-fallback** to qwen3.6-35b-a3b (15pp gap = no way real 9B produced it). ⚠ **ctx=32768 was insufficient** — Run 27 hit 3 ctx-exceeded infra_errors; ctx=65536 fixed it. |
| `anthropic/qwen/qwen3.6-35b-a3b` (LMS, **froggeric v13 template**) | ✅ **REAL WINNER** | This is what Runs 15, 21, 22, 23v2 actually used as task agent under JIT-fallback. The v13 template + /v1/messages routing is the actual lift driver (0.7500 → 0.8250). **Run 24 scale-up (2026-05-12, 5 cycles): 0.7500/0.6750/0.7250/0.8250/0.7000, mean 0.7350.** Cycle 4 reproduced 0.8250 via a *different* skill (lookup_tracker + STOP at 8 tool calls, proposal `917d8d89`) — confirms 0.825 ceiling is reachable via multiple skill patterns. |
| `openai/openai/gpt-oss-20b` (LMS) | ⚠ weak | **Run 25 (task-agent test #6, 2026-05-12):** PASS val_score=**0.3000**, 40/40 clean, ~22 min. With qwen3.6-35b-a3b proposer. 52pp below baseline — task-agent quality is the ceiling, skill cannot lift a weak agent. |
| `openai/mistralai/devstral-small-2-2512` (LMS, default template) | ❌ jinja template incompatible | **Run 26 (task-agent test #5, 2026-05-12T03:30Z):** SANDBOX_ERROR, 40/40 infra. LMS jinja: "After the optional system message, conversation roles must alternate user and assistant roles except for tool calls and results." Devstral's bundled template can't represent tau3's tool-call/result turns. Loop side ran clean (v_seq=192). **Deferred:** needs froggeric-style template override in LMS UI. |
| `openai/qwen3.5:4B` via Ollama /v1 (`openai/` prefix, `OPENAI_API_BASE=http://LLM_HOST:11434/v1`) | — | **Planned Run H.** Different path from failed `ollama_chat/qwen3.5:4B` (HTTP 415 LiteLLM adapter bug). The `openai/` adapter via Ollama's `/v1/chat/completions` is untested — may avoid the 415 bug. Requires `OPENAI_API_BASE` env override since wrapper defaults OPENAI_API_BASE to the loop preset URL. |
| `openai/qwen3.5:9B` via Ollama /v1 | — | **Planned Run I.** Same as H, 9B variant. |

**Sandbox-image dependency for `ollama_chat/qwen3*` task agents:**
The fix above lives in `tau2_patches.py` which is baked into
`ownevo-sandbox-tau3:0.1.0` at build time. **Any sandbox image built before
the 2026-05-10 patch will hang in 10-min loops on Ollama qwen3* task agents.**
Rebuild with `make sandbox-image-tau3` after pulling main on a fresh checkout.

**Qwen3.5/3.6 thinking-loop levers (added 2026-05-10):**

When a qwen3.x run hits `stop_reason=max_tokens` without ever emitting a
tool call, or LMS jinja errors trip on `"No user query found in messages"`,
the failure modes are usually one of:

| Lever | Effect | Where to set |
|---|---|---|
| `options.think=false` | Suppresses thinking entirely. Fastest, may sacrifice proposer quality. **Currently used** via `OllamaChatClient` (loop) + `tau2_patches.py:_patch_litellm_ollama_think_off` (task agent in sandbox) | Code-level, automatic for ollama_chat/qwen3* models |
| `extra_body.preserve_thinking=true` | Keeps thinking ON but stable across turns — model doesn't restart its reasoning loop. Higher quality, slower | Not yet plumbed — would need to add `extra_body` plumbing to OllamaChatClient and tau2_patches.py |
| LMS prompt-template override | Replaces broken bundled jinja with a "self-healing" one that forces `</think>` close before tool_call. Verified template at `huggingface.co/froggeric/Qwen3.5-35B-A3B-Uncensored-FernflowerAI-MLX-8bit/blob/main/chat_template.jinja` (works on LMS 0.4.6 + qwen3.5-9b) | LM Studio UI → My Models → model → Settings → Prompt Template — paste jinja override |
| `presence_penalty=0.0`, `temperature=1.0` | Sampler tuning. Low temp (0.2-0.7) traps the model in reasoning paths; presence_penalty ≥ 1.2 causes instant looping | LMS per-model settings or LiteLLM completion kwargs |
| System-prompt close-think nudge | Append: "You MUST close your reasoning block with </think> before calling any tool." | `runner.py:_maybe_no_think_suffix` — currently appends `/no_think` (ineffective on qwen3.5/3.6). Replace with the close-tag nudge for that lineage |

### All-3-roles single-model on Ollama — confirmed unviable (2026-05-11)

After 5 attempts spanning different model families, single-model all-3-roles on Ollama consistently fails to surface a val_score on tau3-retail. Each model fails for a different reason but the bottleneck is structural:

| Model | Preset | Fail mode | Killed at |
|---|---|---|---|
| `qwen3.6:35b-a3b` | ollama (native) | Throughput trap | 1/40 in 30 min |
| `gemma4:26b` | ollama (native) | Cycle-1 proposal Python typo | 40/40 NameError |
| `qwen3-coder:30b` | ollama-openai | Loop OK, task agent weak (avg 0.15) | 26/40 at 115 min |
| `gpt-oss:20b` | ollama-openai | Per-call latency 18s-7m36s | 11/40 at 80 min |
| `qwen3:30b-a3b` | ollama (native) | Same throughput trap as qwen3.6 | 1/40 in 25 min |
| `qwen3:30b-instruct` | ollama (native) | Fast start, then 33-min single-task retry stall | 22/40 (reward 0.36) at 53 min |

**Session totals (2026-05-10/12):** 24 attempts, **6 end-to-end val_score landings**:
- Run 8: `qwen36lms_ctx65k_smoke` — **PASS val_score=0.7500** (LMS qwen3.6-35b-a3b all-3, ctx=65k, v12 template). 40/40 clean, ~27 min.
- Run 12: `gemma4_e4b_full_local_64k` — **PASS val_score=0.1750** (LMS google/gemma-4-e4b all-3, ctx=65k). 40/40 clean, ~39 min. Confirms second viable proposer family.
- Run 15: `qwen3coder_30b_lms_full_local_64k` — **PASS val_score=0.1250** (LMS qwen/qwen3-coder-30b all-3, ctx=65k). 40/40 clean, ~30 min. Third landing; confirms qwen3-coder retail-weak regardless of backend.
- Run 21: `qwen36loop_qwen35_4b_lms_anthropic_smoke` — **PASS val_score=0.8250** (qwen3.6 loop + nominal "qwen3.5-4b" task/user via v13 template). 40/40 clean, ~24 min. **Originally reported as "smaller task agent beats winner" / new record. INVALIDATED 2026-05-12: identifier `anthropic/qwen/qwen3.5-4b` does not exist in LMS; JIT-fallback silently routed task/user to the loaded qwen3.6-35b-a3b. The skill behind 0.825 IS real (memory-injection of known_facts) and reproduces with qwen3.6-35b-a3b all-3-roles — see Run 24 cycle 4 (0.8250 via different skill).**
- Run 22: `qwen36loop_qwen35_9b_lms_anthropic_smoke_v3` — **PASS val_score=0.7250** (nominal "qwen3.5-9b" task/user). 40/40 clean, ~24 min. **Also JIT-fallback (Run 28 retest at real 9B + ctx=65k landed 0.5750, not 0.725).** Original "inverse scaling 4B > 9B > 35B" framing is invalidated.
- Run 23 v2: `qwen36_27b_loop_qwen35_4b_lms_anthropic_smoke_v2` — **PASS val_score=0.6750** (qwen3.6:27b dense Ollama proposer + nominal "qwen3.5-4b" LMS task/user, in reality JIT-fallback qwen3.6-35b-a3b). 40/40 clean, ~41.5 min. **Independent finding still valid:** dense 27B Ollama proposer (0.6750) < MoE 35B-A3B LMS proposer (0.8250) for the proposer role — MoE > dense with thinking suppression.

Other attempts — abbreviated, model-selection signal only (infra details in `STATUS.md`):

**Granite (LMS, mixed + all-3):** 8B too weak as loop driver (no `write_skill`), as task agent throughput-bound (~11hr/cycle). 30B as proposer in all-3 (Run 13) emitted skill but missing `HarnessAgent` class — codegen-weak. **Granite-30B as task-agent-only not tried** (queued #24).

**qwen3-coder (LMS+Ollama):** clean as proposer + task agent end-to-end (Run 15 PASS 0.1250) but **retail-weak** — codegen-tuned models trade conversational retail ability. NOT a usable retail task agent.

**unsloth/qwen3.6-35b-a3b (Run 14):** cross-quant ≈ qwen/ quant within noise (0.77 vs 0.75 N=39); 1 task hit 4hr wall, gate-rejected. **Cross-quant generalizability CONFIRMED** — pick whichever quant fits VRAM.

**ollama_chat/qwen3.5:* (Runs 16/17):** LiteLLM adapter HTTP 415 deterministic — model-size independent. **Track CLOSED** — use `anthropic/qwen/qwen3.5-*` or `openai/qwen3.5:*` (Ollama /v1, not /api/chat) instead.

**qwen3.5 task agents (Runs 18/20/21/22 → 27/28/36):** Run 18 hit LMS jinja error ("No user query found"); user applied froggeric v13 template — fixed for both 4b and 9b. **Runs 21/22 PASS 0.825 / 0.725 were JIT-fallback to qwen3.6-35b-a3b** (Run 28 retest at real 9B + ctx=65k landed val=0.5750). **Run 36 v2 (2026-05-12, killed at 9/40 @ avg 0.2222)** locked the real qwen3.5-4b verdict — per-task latency ~3.5 min on small thinking model, trajectory matches diag smoke (10/40 @ 0.30). **Final retail task-agent ranking: qwen3.6-35b-a3b (0.75) > qwen3.5-9b (0.575) > gpt-oss-20b (0.30) ≈ qwen3.5-4b (0.22–0.30).** Bigger > smaller, "inverse scaling" invalidated. ⚠ no-prefix identifier `qwen3.5-4b` (not `qwen/qwen3.5-4b`) is the actual loaded artifact in LMS.

**gemma-4-31b dense (Run 19):** all-3-roles PASS qua model, 36/40 evaluated at avg **0.62** before LMS HTTP 500 infra-flake on 4 tasks. Dense 31B avoids the MoE max_tokens cap that bit gemma-4-26b-a4b. **Viable task agent + proposer** — gate retry queued #13.

**gemma-4-26b-a4b (Run 29):** mechanically OK as loop driver (7 iters, 14K out, end_turn — NOT the feared max_tokens cap), but proposal literally ended with `return (None, state) # Placeholder for logic below` — **planning capacity insufficient to hold a full HarnessAgent rewrite**. Same codegen-incomplete class as Run 20 (qwen3.6 `self.known_facts` uninit) and granite-30B. **Mark proposer-unviable;** as task-agent-only untested (post-merge #21).

**qwen3.6:27b Ollama dense (Run 23 v1/v2):** v1 hit 600s httpx (fix committed `9a700f1`); v2 PASS val=**0.6750**. Dense 27B + uncontrolled thinking (`think:false` rejected) < MoE 35B-A3B LMS proposer (−15pp). **MoE > dense for proposer when both forced through same template.**

**glm-4.7-flash (Run 30/31 LMS, Run 32 v1/v2 Ollama):** LMS bundled llama.cpp lacks full glm-4.7 arch support + tool-call/freeze bug under default sampling. **LMS path 🚫.** Ollama has upstream support — Run 32 v2 in flight at clean topology with `OLLAMA_GPU_COUNT=2`. First real signal pending.

**Scale-up of real winner config (Run 24):** 5 cycles of qwen3.6-35b-a3b all-3-roles via v13 template + /v1/messages: **0.7500 / 0.6750 / 0.7250 / 0.8250 🎯 / 0.7000**, mean 0.7350. Two distinct 0.825 skills (`33f6e90d` known_facts memory; `917d8d89` lookup_tracker + STOP-at-8) — **ceiling is task-agent capability, not skill design**.

**Devstral-small-2 (Run 26):** LMS jinja template can't represent tau3 tool-call/result turn structure ("conversation roles must alternate"). **Task-agent unviable** until template override applied.

**gpt-oss-20b (Run 25):** PASS 0.30 — 52pp below winner. **Weak task agent doesn't lift via skill.**

**Infra/code fixes shipped 2026-05-12:**
- ollama_native.py `DEFAULT_TIMEOUT_SECONDS 600→1800` (commit `9a700f1`).
- AsyncOpenAI + AsyncAnthropic `timeout=1800.0` (commit `8307385`).
- Wrapper per-backend concurrency defaults (LMS=4, Ollama=2) (commit `d34486e`).
- Wrapper model-swap hooks for VRAM-tight LMS proposer↔task-agent (commit `8307385`).

**JIT-fallback fix (load-bearing):** prior runs called identifiers like `anthropic/qwen/qwen3.5-4b` which **does not exist** in LMS — JIT silently served the loaded model (qwen3.6-35b-a3b). All sweeps now require JIT + auto-unload **disabled** in LMS settings; use exact loaded identifiers.
- Run 23 v2: `qwen36_27b_loop_qwen35_4b_lms_anthropic_smoke_v2` (2026-05-12T02:56Z → 03:38Z) — **PASS val_score=0.6750.** qwen3.6:27b (27B dense, Ollama native, `think:false` rejected → full thinking chain) as proposer + `anthropic/qwen/qwen3.5-4b` LMS as task/user. 40/40 evaluated, 0 infra_errors. Loop: 5 iters, 5 tool_calls, 1 tool_error, v_seq=169, 27649 in / 7860 out. ~41.5 min total (first call 9m23s disk load). **Key finding: dense 27B Ollama proposer (0.6750) < MoE 35B-A3B LMS proposer (0.8250, Run 21).** For the PROPOSER role: MoE architecture with thinking suppression (LMS strips thinking) outperforms dense with uncontrolled thinking. Proposer ranking: MoE-35b-a3b LMS > dense-27b Ollama.
- Run 23: `qwen36_27b_loop_qwen35_4b_lms_anthropic_smoke` (2026-05-12T02:35Z) — **TIMEOUT (rc=1, ~15 min).** Ollama `qwen3.6:27b` (native `/api/chat`) as proposer + `anthropic/qwen/qwen3.5-4b` LMS as task/user. Root cause: `httpx.ReadTimeout` after ~915s wall-time — qwen3.6:27b is a **27B DENSE model** (17.4GB on disk) vs qwen3.6-35b-a3b which is MoE (only 3B active params). First Ollama request required ~5 min disk load + slow dense-model generation, exceeding the 600s httpx timeout. **Fix applied:** `DEFAULT_TIMEOUT_SECONDS` bumped `600s → 1800s` in `ollama_native.py`. Retry as v2 in-flight. Log: `log/tau3_p2/qwen36_27b_loop_qwen35_4b_lms_anthropic_smoke_p2_cycle1.log`. Key finding: **MoE vs Dense distinction is load-bearing** — 35b-a3b (MoE, 3B active) works fine at 600s; 27b (Dense, 27B active) does not.
- **LMS daemon wedge incident** between runs 11 and 12: every `lms load` returned "Terminated" until `lms server stop && start` cleared it. Likely VRAM-fragmentation after many load/unload cycles.

**Root causes (in order of impact):**
1. **No KV-cache reuse across turns.** LMS reuses ~30K tokens per turn (`cache_read_input_tokens: 31491` in cycle log). Ollama reprocesses full conversation context every `/api/chat`. Per-call latency × ~30-50 turns per task makes wall-time unviable on a 40-task sweep with concurrency=3.
2. **`NUM_PARALLEL=2` in `_p.sh` config** means only 2 of 3 concurrent task slots fit on GPU at once.
3. **`think:false` patch is family-specific.** Helps qwen3.5/3.6/qwen3 families. Doesn't help `gpt-oss` (`reasoning_effort`) or `gemma4` (no thinking). Doesn't address proposer codegen quality.

**Path forward — known-viable configurations not yet exhausted:**
- **LMS qwen3.6 @ ctx=65536** — already 39/40 clean at ctx=32768; just blocked by 1 ctx-exceeded task. Expected val_score ≈ 0.69.
- **Mixed roles** — Ollama for loop (single-stream, fewer turns) + LMS for task agents (KV cache reuse). Untested.
- **Reduce concurrency=1 + Ollama** — eliminates GPU contention. Higher per-task wall-time but possibly viable for smaller sweeps.

**Open dimensions:**
- **LMS qwen36 ctx ≥ 65536** — froggeric v12 template at ctx=32768 still saw 1/40 task hit `Context size has been exceeded`. Retry with `lms load qwen/qwen3.6-35b-a3b -c 65536` to cover the long-tail retail conversation and surface a real `val_score` (~0.69 expected based on 39/40 in-flight average). NOW HIGH-PRIORITY after the all-Ollama sweep proved unviable.
- **lmstudio-community/Qwen3.6-35B-A3B-GGUF** exists on HF (verified 2026-05-10). Ships fixed templates. Now superseded by the froggeric override (cheaper than 22 GB download).
- **gemma4:26b on Ollama as task agent** untested. Ollama has its own template (independent of LMS jinja) so worth a try as alternative — non-thinking model so the think-patch above doesn't affect it.

---

## Phase 3 — Condition C: Gated loop (LLM-judge approval)

**Status:** ☐ deferred post-merge.

Re-run the improvement loop with ownEvo's LLM-judge approval engaged at `apps/kernel/src/ownevo_kernel/approvals/llm_judge.py`. Every gate-passing proposal goes through the judge; approved → committed to skill + audit chain.

**Substeps:** wire gate-pass → approval queue → LLM-judge → commit; 10 iterations on fresh workspace; record val_score_C, gate-blocked regressions, judge approve/reject; founder re-approves ≥5 to compare against the judge.

**Exit gate:** `val_score_C > val_score_A` (any lift with gate engaged).

---

## Phase 4 — Results document

**Status:** ☐ deferred post-merge.

Write `ownevo_docs/benchmarks/tau3-results-2026-Q3.md` with three-condition table (val_score A/B/C + lift A→C), honest disclosure (all-local task agent = qwen3.6-35b-a3b LMS, not cloud GPT-5.4), NeoSigma comparison, top 3 improvements (from skill audit chain). Reproducibility: `make tau3-replay` target. **Pass³ stretch:** re-run condition C top-N tasks 3× per Claw-Eval.

---

## Results ledger

| Condition | Model (all roles) | Domain | Tasks | val_score | Lift vs A | Wall time | Cost |
|---|---|---|---|---|---|---|---|
| A — frozen baseline (cloud) | claude-sonnet-4-6 + haiku user-sim | retail test | 40 | **0.8500** | — | ~16 min | ~$9.27 |
| B — autonomous loop (cloud) | claude-sonnet-4-6 loop + task | retail test | 40 | **0.9500** | +10pp | 14 cycles | ~$50-80 |
| **A-LOCAL — frozen baseline (local)** | qwen3.6-35b-a3b LMS all-3 | retail test | 40 | **0.7500** | — | ~27 min | $0 |
| **B-LOCAL — autonomous loop (local, this branch)** | qwen3.6-35b-a3b LMS all-3 (5 cycles) | retail test | 40 | **0.8250** (best); mean 0.7350 | +10pp | ~25-30 min/cycle | $0 |
| C — gated loop | TBD | retail | 40 | ☐ | — | — | TBD |

**Alternative proposers landed end-to-end (val_score on the all-local path):**

| Run | Proposer | Task/user | val_score | Notes |
|---|---|---|---|---|
| 12 | google/gemma-4-e4b LMS (all-3) | (same) | 0.1750 | 40/40 clean, ~39 min — smallest-viable PASS |
| 15 | qwen/qwen3-coder-30b LMS (all-3) | (same) | 0.1250 | 40/40 clean, ~30 min — retail-weak |
| 23 v2 | qwen3.6:27b Ollama dense | anthropic/qwen3.5-* LMS (JIT→qwen3.6) | 0.6750 | 40/40 clean — MoE > dense for proposer |
| 32 v2 | glm-4.7-flash:latest Ollama (DeepSeek-2) | qwen3.6-35b-a3b LMS | 0.6750 | Architecture diversity proven |

**Alternative task agents landed (proposer = qwen3.6-35b-a3b LMS):**

| Run | Task agent | val_score | Notes |
|---|---|---|---|
| 24 cycle 4 | qwen3.6-35b-a3b LMS (winner) | 0.8250 | Ceiling reachable via 2 distinct skill patterns |
| 28 | qwen/qwen3.5-9b LMS (v13, ctx=65k) | 0.5750 | Real 9B post-JIT-disabled |
| 25 | openai/gpt-oss-20b LMS | 0.3000 | 40/40 clean |
| 36 v2 (partial) | qwen3.5-4b LMS | ~0.22-0.30 | Killed at 9/40; trajectory locked |
| 41 v2 | ollama_chat/gemma4:e2b | ~0.00 | SANDBOX_ERROR, avg_reward=0.00, retail-weak |
| 43 | ollama_chat/gemma4:26b | ~0.00 | SANDBOX_ERROR, avg_reward=0.00, too slow + retail-weak |
| 44/45 | ollama_chat/devstral-small-2:latest | ~0.33 (partial) | Full-eval-infeasible (TASK_TIMEOUT), partial avg=0.33 (N=6) |

**P2-LOCAL LMS task-agent sweep (2026-05-13, post-Ollama queue):**

Mixed topology: qwen3.6-35b-a3b LMS as proposer + various LMS models as task agent, swap mode, ctx=65536, c=4.

⚠️ **LiteLLM prefix rule discovered (Run B v2):** All LMS task-agent models need `openai/<id>` or `anthropic/<id>` prefix — bare LMS IDs (e.g. `google/gemma-4-31b`) fail with `LLM Provider NOT provided`.

| Run | LMS task agent | val_score | Notes |
|---|---|---|---|
| A (⚠ deferred) | `anthropic/qwen/qwen3-30b-a3b-2507` | ctx=32768 overflow | Systemic overflow on ~50% retail tasks. Needs ctx=65536, fresh LMS session. |
| B v1 (✗) | `anthropic/google/gemma-4-31b` | smoke fail | Anthropic-compat format incompatible with gemma4-31b. |
| B v2 (✗) | `google/gemma-4-31b` (no prefix) | smoke fail, rc=9 | liteLLM provider prefix missing. |
| B v3 (🔄) | `openai/google/gemma-4-31b` | in progress | Fix: `openai/` prefix + `OPENAI_API_BASE=http://192.168.1.50:1234/v1`. |
| C | `anthropic/qwen/qwen3-32b` | queued | Dense 32B qwen3, froggeric v13 template. |
| D | `anthropic/qwen/qwen3-14b` | queued | 9 GB, v13 template, ctx=65536. |
| E | `openai/mistralai/mistral-small-3.2` | queued | 24B, Llama arch, new function-calling. |
| F | LMS native nemotron-cascade | queued | LMS hub nvidia_nemotron-cascade-2-30b-a3b (22.45 GB). |
| G | `openai/LGAI-EXAONE/EXAONE-4.5-33B-GGUF` | queued | GGUF Q4_K_M, 33B, LG AI. |
| H | `openai/ServiceNow-AI/Apriel-1.6-15b-Thinker-GGUF` | queued | 15B Thinker GGUF, ServiceNow-AI. |
| I | `openai/bartowski/nvidia_Nemotron-Cascade-2-30B-A3B-GGUF` | queued | bartowski Q4_K_S GGUF on LMS. |
| J | `openai/lmstudio-community/Apriel-Nemotron-15b-Thinker-GGUF` | queued | Apriel×Nemotron 15B Thinker, GGUF Q4_K_M. |
| K | `anthropic/nvidia/nemotron-3-nano-4b` | queued | 4B, speed floor test. |
| ⚠ | `nvidia/nemotron-3-nano-omni` | blocked | Main GGUF missing (only mmproj downloaded). |

**NeoSigma reference (cloud GPT-5.4, no gate):** 0.56 → 0.78 (+39.3%), 18 iterations, 96 experiments.

---

## Open questions / blockers

All resolved as of 2026-05-12. Kept for institutional reference:

| # | Question | Resolution |
|---|---|---|
| Q1 | Does tau2 respect `OPENAI_BASE_URL`? | ✅ tau2 uses LiteLLM — route via `ollama_chat/`/`openai/`/`anthropic/` prefix + matching `_API_BASE` env. |
| Q2 | Does qwen3-coder:30b emit tau2-compatible tool calls? | ✅ Yes — clean. But retail-weak as task agent (0.15 mean reward); use as proposer only. |
| Q3 | Loop driver: cloud or all-local? | ✅ Both proven. Cloud Sonnet → 0.95 (P2). All-local qwen3.6-35b-a3b → 0.825 (P2-LOCAL, this branch). |
| Q4 | Per-task timeouts at concurrency? | ✅ Concurrency defaults: LMS=4, Ollama=2 (wrapper). Per-task timeout 2400s. |
| Q5 | Does Docker reach `192.168.1.50:11434`? | ✅ Default bridge network works. |
| Q6 | Does `num_ctx` propagate through tau2? | ✅ Yes, via `llm_args` on `TauBenchRunner`. Retail needs ctx ≥ 65536 to cover long-tail conversations (32K hits `Context size has been exceeded` on ~1/40). |
| Q7 | Default `num_ctx` for qwen3-coder:30b? | ✅ Not load-bearing — qwen3-coder is retail-weak as task agent regardless. |

---

## Key files

| Path | Purpose |
|---|---|
| `apps/kernel/scripts/run_tau3_loop.py` | Loop driver — proposer + task-agent + user-sim orchestration, smoke gate, swap hooks |
| `apps/kernel/scripts/tau3_p2_local_loop.sh` | Wrapper for local-LLM cycles; per-backend concurrency defaults + swap-mode hooks |
| `apps/kernel/scripts/tau3_p2_local_sweep.sh` | Multi-config sweep harness |
| `apps/kernel/scripts/tau3_p2_sonnet_loop.sh` | Cloud Sonnet baseline (P2) |
| `apps/kernel/sandbox/tau2_patches.py` | Monkey-patches LiteLLM to inject `options.think=false` for `ollama_chat/qwen3*`; baked into sandbox image |
| `apps/kernel/baselines/tau3_v1/agent.py` | `HarnessAgent` skill (the thing being optimized) |
| `apps/kernel/src/ownevo_kernel/benchmark/tau3/runner.py` | `TauBenchRunner` — implements `BenchmarkRunner` Protocol |
| `apps/kernel/src/ownevo_kernel/eval_runner/ollama_native.py` | Ollama native `/api/chat` client for loop role; `OllamaChatClient` auto-injects `options.think=false` |
| `apps/kernel/src/ownevo_kernel/middleware/claude_sdk/runner.py` | Loop-agent turn runner; auto-appends `/no_think` for qwen3* via openai path |
| `apps/kernel/src/ownevo_kernel/middleware/claude_sdk/tool_definitions.py` | `write_skill` validator chain (commits aaa9fef, 08f2249, 58cf93a) |
| `STATUS.md` (gitignored working doc) | Live per-run log; delete before merge |
| `docs/local-model-testing.md` | Desktop model capabilities reference (A4.4 gate + tau3 cross-link) |

---

## Next action

**Active sweep (2026-05-13):** LMS task-agent sweep Runs B–K. Run B v3 in progress (PID 1652436, `openai/google/gemma-4-31b`, swap mode, c=4). Queue above lists C–K.

**After sweep completes:**
1. Delete `STATUS.md` from working tree (gitignored).
2. Open PR `feat/ollama-loop-runner` → `main`.
3. P3 (gated loop with LLM-judge) and P4 (results doc).

**To reproduce the winning local config:**

```bash
# Prereqs:
#   LMS @ 192.168.1.50:1234 with qwen/qwen3.6-35b-a3b loaded, ctx=65536, froggeric v13 template, JIT DISABLED.
#   Postgres + sandbox image: `make sandbox-image-tau3` then `docker compose -f infra/docker-compose.yml up -d postgres`.

cd apps/kernel
./scripts/tau3_p2_local_loop.sh \
  qwen/qwen3.6-35b-a3b \
  lms-anthropic \
  my_workflow_tag \
  anthropic \
  anthropic/qwen/qwen3.6-35b-a3b \
  anthropic/qwen/qwen3.6-35b-a3b
```

Expected: 40/40 clean in ~25-30 min, val_score in [0.6750, 0.8250] depending on which skill the proposer lands.
