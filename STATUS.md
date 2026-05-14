# τ³ Local-LLM Sweep Status — 2026-05-12

> **NOT for committing.** This file is the live working doc — gitignored
> (`/log/`) but tracked-state recorded here for session resumption. Delete
> before committing the working tree.

A new coding agent (or a human) starting cold should be able to
read this file, run `## Resume protocol` below, and pick up exactly
where the prior session left off.

---

### Killed — Run 39 `qwen36_loop_devstral_s2_task_c2` (2026-05-12T23:02Z → 23:20Z, killed at 4/40)

- **Config:** qwen3.6-35b-a3b LMS proposer + `ollama_chat/devstral-small-2:latest` task/user, c=2
- **Proposer phase:** ✅ clean — 8 iters, 8 tool_calls, 2 tool_errors, end_turn. v_seq=223, proposal `d1458e4b`.
- **Smoke gate:** ✅ **PASSED** — `task_id='5' evaluated=1 infra_errors=0`. **Key finding: devstral-small-2 on Ollama DOES NOT have the LMS jinja alternation error.** Ollama Modelfile chat template correctly handles tau3's tool-call/result turns. LMS Run 26 failure was stack-specific, not model-specific.
- **Full eval:** killed at 4/40, avg reward 0.50 (N=2 successful tasks, 2 permanent failures). Proposal v_seq=223 had a codegen bug: `_resolve_gaps_from_facts` method called but not defined → `AttributeError` on ~50% of tasks. Also SyntaxWarning on `\s` escape sequence. Contaminated run — no clean val_score.
- **Verdict:** devstral-small-2 **Ollama infra-viable** (jinja bug gone). Reward signal on successful tasks (0.50, N=2) is promising but N too small and contaminated. Per-task latency ~3-4 min (slower than granite8b at NP=4).
- **NOT queued for immediate retry** — move on to qwen3:30b-a3b which has higher information value. devstral retry can come after if qwen3:30b-a3b clears.

### Killed — Run 40 `qwen36_loop_qwen3_30ba3b_task_c2` (2026-05-12T23:25Z → 23:36Z, killed at smoke)

- **Config:** qwen3.6-35b-a3b LMS proposer + `ollama_chat/qwen3:30b-a3b` task/user, c=2
- **Proposer phase:** ✅ clean — 7 iters, 7 tool_calls, 1 tool_error, end_turn. v_seq=225, proposal `7cb55411`.
- **Smoke gate:** ❌ KILLED at smoke task 5 @ 450s+ with no progress. Sandbox still showed `1 running: 5.0(450s)` — no completion signal.
- **Root cause:** qwen3:30b-a3b is a **thinking model** (qwen3moe family, 30.5B Q4_K_M). The tau3/LiteLLM sandbox path does NOT inject `think:false` or `/no_think` for `ollama_chat/` task agents. Result: model burns extensive thinking tokens on every task turn → ~7.5+ min for one smoke task.
- **Throughput projection at c=2:** 40 tasks × 450s / 2 concurrent = ~9000s = 2.5 hours. Far exceeds TASK_TIMEOUT=2400s.
- **Verdict:** qwen3:30b-a3b on Ollama is **thinking-bound, throughput-unviable** as task agent without `think:false` injection in the sandbox LiteLLM call path.
- **qwen3.5:4B also SKIPPED** — same root cause. Run 36 v2 already confirmed heavy thinking on qwen3.5 family (3.5 min/task). No new info to gain.
- **Next: Run 41 gemma4:e2b** — non-thinking model, speed floor test.

### Failed (routing bug) — Run 41 v1 `qwen36_loop_gemma4e2b_task_c2` (2026-05-12T23:37Z → 23:40Z, rc=9)

- **Config:** qwen3.6-35b-a3b LMS proposer + `ollama_chat/gemma4:e2b` task/user, c=2
- **Failure:** `Ollama_chatException - Unsupported Media Type. POST requests must use 'application/json'` on smoke task 5.
- **Root cause:** **OLLAMA_API_BASE routing bug in wrapper.** When proposer is `lms-anthropic`, `BASE_URL=http://192.168.1.50:1234` (LMS). Wrapper set `OLLAMA_API_BASE=${BASE_URL%/v1}=http://192.168.1.50:1234` — LMS, not Ollama (11434). LiteLLM routed `ollama_chat/gemma4:e2b` to LMS which returned "Unsupported Media Type."
- **Fix shipped** (this session): `tau3_p2_local_loop.sh` OLLAMA_API_BASE default changed from `${BASE_URL%/v1}` → `http://${LLM_HOST}:11434`. Caller-env override still works. This also explains why **Run 40 (qwen3:30b-a3b) couldn't have had valid throughput data** — OLLAMA_API_BASE was also wrong for that run. But Run 40 hit thinking-token stall so the difference is moot.
- **Note:** Run 39 (devstral-small-2) must have had OLLAMA_API_BASE set correctly in parent env from prior session — Ollama path did work there.

### Completed — Run 41 v2 `qwen36_loop_gemma4e2b_task_c2_v2` (2026-05-12T23:42Z → 05-13T00:31Z, 49 min)

- **Config:** qwen3.6-35b-a3b LMS proposer + `ollama_chat/gemma4:e2b` task/user, c=2, **OLLAMA_API_BASE=http://192.168.1.50:11434 (routing fix confirmed)**
- **Result:** ❌ SANDBOX_ERROR, val_score=None. Proposer: 6 iters, 6 tool_calls, 1 tool_error, end_turn. v_seq=229, `2b1ab1a8`.
- **Smoke:** ✅ PASSED — task_id='5' evaluated=1 infra_errors=0. **OLLAMA_API_BASE fix confirmed working** (Run 41 v1 failed here; v2 now passes).
- **Full eval:** SANDBOX_ERROR — gate's raw_summary shows n_simulations=1, n_evaluated=1 (only smoke result). Likely cause: TASK_TIMEOUT=2400s consumed by proposer (~8 min) + smoke (~2 min) = ~600s overhead; remaining budget ~1800s insufficient for 40 tasks at ~90-120s/task at c=2 (needs ~2100s).
- **Reward trajectory:** 3/40 complete at 22 min mark (docker log), avg reward **0.00** on all 3 completed tasks. Model is infra-viable but produces 0 reward on retail conversations.
- **Verdict:** gemma4:e2b on Ollama is **infra-viable** (smoke passes with routing fix) but **retail-weak** — avg_reward=0.00. ~2B active params insufficient for multi-turn retail task conversations.
- **Log:** `log/tau3_p2/qwen36_loop_gemma4e2b_task_c2_v2_p2_cycle1.log`

### Failed — Run 42 `qwen36_loop_gemma3_12b_task_c2` (2026-05-13T02:41Z → 02:46Z, rc=9)

- **Config:** qwen3.6-35b-a3b LMS proposer + `ollama_chat/gemma3:12b` task/user, c=2
- **Result:** ❌ rc=9 — smoke FAILED in ~4 min. `litellm.APIConnectionError: Ollama_chatException - {"error":"registry.ollama.ai/library/gemma3:12b does not support tools"}`
- **Root cause:** gemma3 (prior-gen) lacks tool-calling support in Ollama's Modelfile template. Hard API rejection — no `ollama_chat/` workaround.
- **Verdict:** gemma3:12b on Ollama **task-agent-unviable** (no tool support). Different from gemma4 family which supports tools.
- **Note:** Confirmed the gemma4:e2b smoke pass wasn't a fluke — gemma4 family added tool support that gemma3 lacks.
- **Next:** Run 43 = `gemma4:26b` Ollama task agent (MoE 26B-A4B, ~4B active; proven tool-capable from Run 32 v2 loop test). Then Run 44 = devstral-small-2:latest Ollama retry (Run 39 was contaminated by proposer codegen bug, not devstral capability).

### ✗ Run 43 CLOSED — gemma4:26b Ollama task agent (2026-05-13T02:48Z → 03:36Z, ~48 min)

- **Config:** qwen3.6-35b-a3b LMS proposer + `ollama_chat/gemma4:26b` task/user, c=2, OWNEVO_TAU3_CYCLES=1
- **Result:** decision=SANDBOX_ERROR, val_score=None, avg_reward=0.00 (all completed tasks hit `max_steps`)
- **Root cause:** Tasks took ~360-380s each at c=2 (vs ~90-120s for cloud Sonnet). TASK_TIMEOUT=2400s budget exhausted before full 40-task eval — only ~5-7 tasks completed before timeout. All completed tasks returned reward=0.00 (simulation terminated prematurely, `max_steps`).
- **Verdict:** gemma4:26b infra-viable on Ollama (smoke PASSED, Ollama routing confirmed working) but retail-weak AND too slow. Despite 4B active params (vs 2B for e2b), performance is identical: 0.00 avg reward, max_steps on every task. The ~4B active MoE params appear insufficient for multi-turn retail task-completion.
- **Log:** `log/tau3_p2/qwen36_loop_gemma4_26b_task_c2_p2_cycle1.log`, v_seq=233

### ⚠ Run 44 CLOSED — devstral-small-2:latest Ollama task agent (2026-05-13T03:37Z → 04:22Z, ~45 min)

- **Config:** qwen3.6-35b-a3b LMS proposer (lms-anthropic) + `ollama_chat/devstral-small-2:latest` task/user, c=2, OWNEVO_TAU3_CYCLES=1, TASK_TIMEOUT=2400s
- **Result:** decision=SANDBOX_ERROR, val_score=None — TASK_TIMEOUT exhausted before 40/40 eval
- **Partial signal:** 5/40 complete at 21 min in, **avg_reward=0.33 (N=3)** — devstral IS genuinely capable (vs gemma4 0.00). Consistent with contaminated Run 39 estimate (0.50 N=2). Tasks ~200-250s each at c=2 (vs 360-380s for gemma4:26b); faster but still exceeds 2400s budget for 40 tasks.
- **Verdict:** devstral infra-confirmed + retail-capable (~0.30-0.35 partial reward). Need full eval (TASK_TIMEOUT=7200) to get real val_score. Run 45 queued with extended timeout.
- **Log:** `log/tau3_p2/qwen36_loop_devstral_s2_task_c2_v2_p2_cycle1.log`, v_seq=235

### ✗ Run 45 CLOSED — devstral-small-2:latest extended eval (2026-05-13T04:22Z→06:25Z, ~2hr)

- **Config:** qwen3.6-35b-a3b LMS proposer + `ollama_chat/devstral-small-2:latest` task/user, c=2, OWNEVO_TAU3_TASK_TIMEOUT=7200 (2hr budget for full 40 tasks)
- **Result:** decision=SANDBOX_ERROR, val_score=None — TASK_TIMEOUT=7200 EXHAUSTED again before 40/40 eval
- **Partial signal:** 10/40 complete, **avg_reward=0.33 (N=6)** — consistent across all 3 devstral measurements (Runs 39/44/45 all ~0.33). Some tasks hit R2/R3 retries (task 27: ~1800s on R1; task 38: R3). A single long-running task blocks a c=2 slot for 30+ min, making the budget unwinnable.
- **Root cause:** devstral's conversational quality triggers tau2's retry mechanism frequently. At c=2, one stuck task consumes the entire slot budget. At TASK_TIMEOUT=7200 we only reach ~10/40 tasks.
- **Verdict:** devstral-small-2 Ollama **retail-capable (~0.33) but full-eval-infeasible** at any reasonable TASK_TIMEOUT. The ~0.33 partial average is now well-established by 3 consistent measurements — no further full-eval retries needed. Accept ~0.33 as the established estimate (comparable to gpt-oss-20b 0.30 and real qwen3.5-4b ~0.22-0.30).
- **Log:** `log/tau3_p2/qwen36_loop_devstral_s2_task_c2_v3_p2_cycle1.log`, v_seq=237

---

## 📋 LMS task-agent queue (added 2026-05-13)

Ordered by expected information value. Each is 1-cycle (40 tasks @ c=4) with qwen3.6-35b-a3b LMS as proposer. Run after Run 45 completes.

| # | Model | Notes | Expected val_score | Status |
|---|---|---|---|---|
| **A** | `qwen/qwen3-30b-a3b-2507` LMS (17.28 GB) | ctx=32768 insufficient in swap run. **Baseline-only** (`tau3_baseline.py`, `lms load -c 65536`, anthropic format). | ? | ⏳ queued (baseline) |
| **B** | `google/gemma-4-31b` LMS (19.89 GB) | Too slow for full eval (~40 min/task). **SKIP** — no baseline planned. | — | ✗ SKIP |
| **C** | `qwen/qwen3-32b` LMS (19.76 GB, dense 32B) | ctx-flag failed with `--context-length`; `-c 65536` expected to work. **Baseline-only** (anthropic format + v13 template). | ? | ⏳ queued (baseline) |
| **D** | `qwen/qwen3-14b` LMS (9.00 GB) | Same dense qwen3 family. **Baseline-only** (`lms load -c 65536`, anthropic format + v13 template). | ? | ⏳ queued (baseline) |
| **E** | `mistralai/mistral-small-3.2` LMS (15.21 GB, 24B, Llama arch) | val_score=0.0750. 40/40 clean, no infra_errors. Retail-weak. Llama arch poor at retail multi-turn. | 0.0750 | ✅ DONE |
| **F** | `nvidia_nemotron-cascade-2-30b-a3b` (22.45 GB, nemotron_h_moe) | v1 SANDBOX_ERROR (timeout). v2 full eval TASK_TIMEOUT=7200 → PASS. | **0.5000** | ✅ PASS (N=40/40, infra_errors=0, v2) |
| **G** | `exaone-4.5-33b` (25.19 GB, exaone4) | LG AI dense 33B. Different arch family. `openai/exaone-4.5-33b` | — | ✗ CLOSED (exaone4 arch load fails in LMS) |
| **H** | `apriel-1.6-15b-thinker` (9.66 GB, Llama) | ServiceNow-AI 15B Thinker. Thinking model. `openai/apriel-1.6-15b-thinker` | ~0.09 est. | ⚠ SANDBOX_ERROR (timeout); partial avg_reward=0.09 (N=11/40) |
| **I** | `nvidia/nemotron-3-nano-omni` (26.10 GB, nemotron_h_moe) | SANDBOX_ERROR in swap run (16/40). **Baseline-only** (`tau3_baseline.py`, openai format, `--timeout-seconds 7200` needed — model too slow for 2400s). | ~0.38 est. | ⏳ queued (baseline, after F v2) |
| **J** | `nvidia/nemotron-3-nano-4b` (2.84 GB, nemotron_h) | Tiny NVIDIA model, speed-floor test. `openai/nvidia/nemotron-3-nano-4b` | **0.3000** | ✅ PASS (N=40/40, infra_errors=0, ~41 min) |
| ~~skip~~ | ~~`apriel-nemotron-15b-thinker`~~ | ~~NVIDIA×ServiceNow 15B Thinker — skipped per user~~ | — | ✗ skip |

**LMS IDs are from `lms ls` (authoritative). All GGUF models registered with short IDs in LMS (e.g. `nvidia_nemotron-cascade-2-30b-a3b` = the bartowski Q4_K_S GGUF).**

## 📋 No-proposer baseline queue (added 2026-05-13)

Pure task-agent capability: run `tau3_baseline.py` (no proposer step) with each model as agent+user-sim. Eliminates proposer-codegen noise; gives clean floor score. `tau3_baseline.py` patched 2026-05-13 to forward `OPENAI_API_BASE` / `ANTHROPIC_API_BASE` to the sandbox. Run after corresponding proposer-sweep entry, or in parallel (no proposer loaded = full 48 GB VRAM available, no swap needed).

**Plan updated 2026-05-13: skip proposer for all remaining runs — baseline only.**

**⚠️ Ollama config updated 2026-05-13:** Restarted Ollama container with `OLLAMA_CONTEXT_LENGTH=65536` (was 32768). Previous 32k was insufficient for hard retail tasks — same overflow risk as LMS Run 27 (3 infra_errors at 32k). LMS now unloaded; full 48 GB available. Config: `OLLAMA_NUM_PARALLEL=4 OLLAMA_MAX_LOADED_MODELS=2 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_FLASH_ATTENTION=1 OLLAMA_GPU_COUNT=2`. For models ≥ 18 GB, may need to reduce `NUM_PARALLEL` to 2 if VRAM is tight.
Topology labels (matching `docs/local-model-testing.md` compat matrix): `lms-anthropic` | `lms-openai` | `ollama-openai` (OpenAI shim `/v1`) | `ollama` (native `/api/chat`)

> **LMS load protocol (all runs):** always `lms load "<model-id>" --gpu max --context-length 65536`. Default ctx (often 4096) causes immediate 400-errors — retail system prompt alone is ~5228 tokens. Exception: models with a known lower max (e.g. gemma-4-26b-a4b: 32768).

| # | LMS/Ollama ID | topo | liteLLM arg | timeout | baseline_val_score | Status |
|---|--------|------|------------|---------|-------------------|--------|
| ref | `qwen/qwen3.6-35b-a3b` (LMS) | lms-anthropic | `anthropic/qwen/qwen3.6-35b-a3b` | 2400s | **0.75** | ✅ known |
| I-base | `nvidia/nemotron-3-nano-omni` (LMS, 26 GB) | lms-openai | `openai/nvidia/nemotron-3-nano-omni` | **7200s** | **0.6250** | ✅ PASS (N=40/40, infra_errors=0, ~45 min) |
| F-base | `nvidia_nemotron-cascade-2-30b-a3b` (LMS, 22 GB) | lms-openai | `openai/nvidia_nemotron-cascade-2-30b-a3b` | **7200s** | **~0.43 est** | ⚠ PARTIAL (37/40, one task hit 7200s per-task limit) |
| A-base | `qwen/qwen3-30b-a3b-2507` (LMS, 17 GB) | lms-anthropic | `anthropic/qwen/qwen3-30b-a3b-2507` | 2400s | **0.4250** | ✅ PASS (N=40/40, infra_errors=0, ~34 min) |
| C-base | `qwen/qwen3-32b` (LMS, 20 GB) | lms-anthropic | `anthropic/qwen/qwen3-32b` | **7200s** | **~0.25** | ❌ KILLED — avg=0.25 at 4/40 (v4, API patch confirmed working — fast ~30-60s/task); qwen3 base weaker than qwen3.6 series; not worth completing |
| D-base | `qwen/qwen3-14b` (LMS, 9 GB) | lms-anthropic | `anthropic/qwen/qwen3-14b` | **7200s** | **~0.22** | ❌ KILLED — avg=0.22 at 18/40 (was 0.44@9, lucky draw; last 9 scored 0); same pattern as C-base; qwen3 base weaker than qwen3.6/qwen3.5 series |
| qwen36-27b-base | `qwen/qwen3.6-27b` (LMS, 17 GB) | lms-anthropic | `anthropic/qwen/qwen3.6-27b` | **7200s** | **0.8750** | ✅ PASS (N=40/40, infra_errors=0, ~90 min) — new record |
| qwen35-9b-base | `qwen/qwen3.5-9b` (LMS, 6.5 GB) | lms-anthropic | `anthropic/qwen/qwen3.5-9b` | 2400s | **0.5250** | ✅ PASS (N=40/40, infra_errors=0, ctx=65536) |
| gpt-oss-base v1 | `gpt-oss:20b` (Ollama, 12 GB) | **ollama-openai** | `openai/gpt-oss:20b` | 2400s | ☐ | ⚠ **TIMEOUT** — 30/40 partial avg=0.47 (N=30); container wall-clock 2400s exceeded |
| gpt-oss-base v2 | `gpt-oss:20b` (Ollama, 12 GB) | **ollama-openai** | `openai/gpt-oss:20b` | **7200s** | **0.4000** | ✅ PASS (N=40/40, infra_errors=0) |
| gpt-oss-native-base | `gpt-oss:20b` (Ollama, 12 GB) | **ollama** | `ollama_chat/gpt-oss:20b` | 2400s | — | ❌ SKIPPED (user) |
| qwen3-14b-oai-base | `qwen3:14b` (Ollama, 8 GB) | **ollama-openai** | `openai/qwen3:14b` | **7200s** | — | ❌ SKIPPED — thinking model, think:false not injected on openai path; 0/40 at 240s |
| qwen3-14b-native-base | `qwen3:14b` (Ollama, 8 GB) | **ollama** | `ollama_chat/qwen3:14b` | **7200s** | ~0.35 partial | ⚠ PARTIAL (17/40, container 7200s wall-clock, qwen3:14b too slow even with think:false) |
| qwen3-32b-oai-base | `qwen3:32b` (Ollama, 18 GB) | **ollama-openai** | `openai/qwen3:32b` | **7200s** | — | ❌ SKIPPED — qwen3 thinking model too slow on Ollama; 14B took 7200s for 17/40, 32B worse |
| qwen35-9b-oai-base | `qwen3.5:9B` (Ollama, 6 GB) | **ollama-openai** | `openai/qwen3.5:9B` | **7200s** | — | ❌ SKIPPED — ~1400s/task on Ollama, same pattern as qwen3:14b; 3/40 at 29 min, killed |
| qwen35-4b-oai-base | `qwen3.5:4B` (Ollama, 3 GB) | **ollama-openai** | `openai/qwen3.5:4B` | **7200s** | — | ❌ SKIPPED — 0/40 at 14 min, all tasks >840s, same pattern as 9B; qwen3.5:xB uniformly too slow on Ollama |
| J-base | `nvidia/nemotron-3-nano-4b` (LMS, 2.8 GB) | lms-openai | `openai/nvidia/nemotron-3-nano-4b` | 2400s | **0.3250** | ✅ PASS (N=40/40, infra_errors=0, ctx=65536) |
| qwen35-4b-lms-base | `qwen3.5-4b` (LMS, 3.4 GB, no namespace) | **lms-anthropic** | `anthropic/qwen3.5-4b` | **7200s** | **0.3750** | ✅ PASS (N=40/40, infra_errors=0, ctx=65536) |
| K-base | `ServiceNow-AI/Apriel-1.6-15b-Thinker:Q4_K_M` (Ollama) | ollama-openai | `openai/ServiceNow-AI/Apriel-1.6-15b-Thinker:Q4_K_M` | **7200s** | ☐ | ❌ **DROPPED** — too slow (thinker + Ollama serial = infeasible) |

**⚠️ LiteLLM provider prefix requirement (learned from Run B v2 failure):**
All task agent models need a LiteLLM provider prefix:
- Hub models (with slash): `openai/google/gemma-4-31b`, `openai/nvidia/nemotron-3-nano-omni`
- GGUF/local models (no slash): `openai/nvidia_nemotron-cascade-2-30b-a3b`, `openai/exaone-4.5-33b`
- qwen3 family: `anthropic/qwen/qwen3-32b` etc. (anthropic format + v13 template)
- The `OWNEVO_TAU3_SWAP_TASK` env var uses the bare `lms ls` ID (for `lms load`)

**⚠️ ANTHROPIC_API_BASE must NOT include /v1 suffix (learned from A-base v1 failure):**
- ✅ Correct: `ANTHROPIC_API_BASE=http://192.168.1.50:1234`
- ❌ Wrong: `ANTHROPIC_API_BASE=http://192.168.1.50:1234/v1` → litellm appends `/v1/messages` → `POST /v1/v1/messages` → infra_errors=40/40
- OpenAI format: `OPENAI_API_BASE=http://192.168.1.50:1234/v1` (WITH /v1 — opposite convention)

**⚠️ OPENAI_API_BASE must include /v1 even when proposer uses lms-anthropic preset (2026-05-14 T1 smoke investigation):**
- When proposer uses `lms-anthropic` preset, `BASE_URL=http://192.168.1.50:1234` (no `/v1`). The loop script used to set `OPENAI_API_BASE=$BASE_URL` which stripped `/v1`.
- LMS at `/chat/completions` (no `/v1`) returns HTTP 200 `{"error":"Unexpected endpoint"}` — LiteLLM can't parse → `BadRequestError: OpenAIException - ` (empty message).
- **Fixed** in `tau3_p2_local_loop.sh`: OPENAI_API_BASE now hardcoded to `http://${LLM_HOST}:1234/v1` (not derived from BASE_URL). This makes openai/ user models work with lms-anthropic proposer runs.

**Baseline command templates (`tau3_baseline.py`, no proposer):**
```bash
# Ollama direct OpenAI API (ollama-openai: openai/ prefix, OPENAI_API_BASE → Ollama /v1)
OPENAI_API_KEY=ollama OPENAI_API_BASE=http://192.168.1.50:11434/v1 \
  nohup uv run --directory apps/kernel --extra agent python scripts/tau3_baseline.py \
    --agent-model "openai/<ollama-model>" --user-model "openai/<ollama-model>" \
    --concurrency 4 --timeout-seconds <N> --no-db \
  > log/tau3_p2/<tag>_nopr_baseline.log 2>&1 &

# Ollama direct native API (ollama: ollama_chat/ prefix, OLLAMA_API_BASE → Ollama /api/chat)
# think:false auto-injected by tau2_patches.py for qwen3* models
# ⚠ qwen3.5:* blocked on this path (LiteLLM ollama_chat adapter HTTP 415)
OLLAMA_API_BASE=http://192.168.1.50:11434 \
  nohup uv run --directory apps/kernel --extra agent python scripts/tau3_baseline.py \
    --agent-model "ollama_chat/<ollama-model>" --user-model "ollama_chat/<ollama-model>" \
    --concurrency 4 --timeout-seconds <N> --no-db \
  > log/tau3_p2/<tag>_nopr_baseline.log 2>&1 &
```

**Loop wrapper templates (proposer + task, swap mode):**
```bash
# LMS hub model, openai format (gemma4, mistral-small, nemotron, etc.)
OWNEVO_TAU3_CYCLES=1 OWNEVO_TAU3_CONCURRENCY=4 \
  OWNEVO_TAU3_SWAP_PROPOSER="qwen/qwen3.6-35b-a3b" \
  OWNEVO_TAU3_SWAP_TASK="<lms-model-id>" \
  OWNEVO_TAU3_SWAP_TASK_CTX=65536 \
  bash scripts/tau3_p2_local_loop.sh \
    "qwen/qwen3.6-35b-a3b" lms-openai "<workflow_tag>" openai \
    "openai/<lms-model-id>" "openai/<lms-model-id>"

# LMS hub model, anthropic format (qwen3 family with v13 template)
OWNEVO_TAU3_CYCLES=1 OWNEVO_TAU3_CONCURRENCY=4 \
  OWNEVO_TAU3_SWAP_PROPOSER="qwen/qwen3.6-35b-a3b" \
  OWNEVO_TAU3_SWAP_TASK="<lms-model-id>" \
  OWNEVO_TAU3_SWAP_TASK_CTX=65536 \
  bash scripts/tau3_p2_local_loop.sh \
    "qwen/qwen3.6-35b-a3b" lms-anthropic "<workflow_tag>" anthropic \
    "anthropic/<lms-model-id>" "anthropic/<lms-model-id>"
```

---

### ✗ Run A CLOSED — qwen3-30b-a3b-2507 LMS task agent (2026-05-13T06:40Z→07:15Z, killed)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `anthropic/qwen/qwen3-30b-a3b-2507` LMS task/user, c=4, ctx=32768 (VRAM constraint)
- **Result:** KILLED at 6/40 — systemic ctx=32768 overflow. val_score: INCOMPLETE
- **Partial signal:** 1 scored task (reward=1.00), 4 permanent infra_errors (tasks 5, 9, 12, 18 — all context overflow), 2 more (26, 27) actively retrying when killed. **All failures identical: `Context size has been exceeded` after ~120-195s on every retry attempt.**
- **Root cause:** qwen3-30b-a3b-2507 generates systematically longer responses than qwen3.5-9b. At ctx=32768, every "hard" retail task (multi-turn, many tool calls) overflows within ~2 min. 4/4 of the first batch and 2+ of the second batch all failed. Expected ~50% infra_error rate if run to completion — val_score would be unreliable (N~20).
- **Verdict:** ctx=32768 is INSUFFICIENT for qwen3-30b-a3b-2507. Needs ctx=65536. The 1 successful task (reward=1.00) suggests the model IS capable — context is the only blocker.
- **Infra notes from this run:**
  1. `qwen/qwen3-30b-a3b` ambiguous — use exact `qwen/qwen3-30b-a3b-2507` identifier.
  2. ctx=65536 fails with OOM after load/unload cycles. Fresh LMS session may work.
  3. LiteLLM `get_response_cost` errors for unknown model IDs are cosmetic — don't block inference.
- **Needs rerun at ctx=65536** — flag as ⚠ deferred, not closed.
- **Log:** `log/tau3_p2/qwen36_loop_qwen3_30ba3b_task_c4_p2_cycle1.log`

---

### ✗ Run B v1 CLOSED — gemma-4-31b LMS task agent (2026-05-13T07:18Z→07:25Z, rc=9)

- **Config:** `anthropic/google/gemma-4-31b` — wrong format. LMS anthropic-compat doesn't serve gemma4-31b via `/v1/messages`. smoke infra_error=1/1.
- **Log:** `log/tau3_p2/qwen36_loop_gemma4_31b_task_c4_p2_cycle1.log`

### ✗ Run B v2 CLOSED — gemma-4-31b LMS task agent (2026-05-13T07:29Z→07:39Z, rc=9)

- **Config:** `google/gemma-4-31b` (no prefix) — lms-openai preset, openai api-format. Model loaded OK (18.52 GiB, ctx=65536). smoke task_id='5' → infra_error=1.
- **Root cause:** `litellm.BadRequestError: LLM Provider NOT provided. You passed model=google/gemma-4-31b` — LiteLLM needs provider prefix (`openai/`) to route correctly. `google/...` without prefix is unrecognized.
- **Fix:** task agent model must be `openai/google/gemma-4-31b` (not bare `google/gemma-4-31b`). `OWNEVO_TAU3_SWAP_TASK` stays `google/gemma-4-31b` (for `lms load`); liteLLM routing uses the `--task-agent-model` arg.
- **Key LiteLLM rule:** All LMS-OpenAI task models must use `openai/<lms-model-id>` prefix. GGUF models too.
- **Log:** `log/tau3_p2/qwen36_loop_gemma4_31b_task_c4_v2_p2_cycle1.log`

### ✗ Run B v3 CLOSED — gemma-4-31b LMS task agent (2026-05-13T07:52Z→08:10Z, rc=9)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `openai/google/gemma-4-31b` LMS task/user, c=4, ctx=65536
- **Result:** rc=9 — smoke FAILED. `'HarnessAgent' object has no attribute '_resolve_gaps_from_facts'` on task_id='5'.
- **Root cause:** Proposer codegen bug (v_seq=249) — qwen3.6-35b-a3b wrote proposal calling `self._resolve_gaps_from_facts(...)` which is not defined in the HarnessAgent class body. Same pattern as Run 39 (v_seq=223). Stochastic proposer failure — NOT a gemma4-31b capability issue. gemma4-31b never got to execute.
- **Smoke detail:** Task 5 ran R1→R2→R3 (~300-420s each) before smoke declared infra_error=1/1. All retries hit identical AttributeError (same broken code re-executed each retry).
- **Verdict:** Retry as v4 (fresh proposer pass → different proposal). gemma4-31b val_score remains unknown.
- **Log:** `log/tau3_p2/qwen36_loop_gemma4_31b_task_c4_v3_p2_cycle1.log`, v_seq=249

### ✗ Run B v4 CLOSED — gemma-4-31b LMS task agent (2026-05-13T08:17Z→09:55Z)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `openai/google/gemma-4-31b` LMS task/user, c=4, ctx=65536
- **Proposer:** ✅ clean — 4 iters, 4 tool_calls, 1 tool_error, end_turn. v_seq=251.
- **Smoke:** ✅ PASSED — task_id='5' evaluated=1 infra_errors=0 (~2340s, barely under TASK_TIMEOUT=2400s).
- **Full eval:** decision=SANDBOX_ERROR, val_score=None — 1/40 complete (task 17, reward=**1.00**), tasks 5/9/12 hit TASK_TIMEOUT=2400s at the ~40 min mark.
- **Root cause:** Dense 31B generates slowly — hardest retail tasks take ~37 min. TASK_TIMEOUT=2400s clips them. Same throughput failure mode as devstral (full-eval-infeasible at standard timeout).
- **Partial signal:** avg_reward=1.00 (N=1) from task 17. Combined with Run 19 prior (avg_reward=0.62, N=36 all-3-roles), gemma4-31b IS capable — TASK_TIMEOUT is the only blocker.
- **Verdict:** TASK_TIMEOUT=2400s insufficient. Would need TASK_TIMEOUT=7200 for a clean eval. Not queuing extended retry — Run 19 prior (0.62) is sufficient signal. Provisionally record as ~0.60 pending confirmation.
- **Log:** `log/tau3_p2/qwen36_loop_gemma4_31b_task_c4_v4_p2_cycle1.log`

### ✗ Run C CLOSED — qwen3-32b LMS task agent (2026-05-13T10:12Z→10:15Z, rc=9)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `anthropic/qwen/qwen3-32b` LMS task/user, c=4, ctx=65536
- **Result:** ❌ rc=9 — `lms load 'qwen/qwen3-32b' --context-length 65536` → "Error loading model (Exit code: null)"
- **Proposer phase:** ✅ clean — 5 iters, 5 tool_calls, 1 tool_error, end_turn. v_seq=253. Proposal OK.
- **Failure:** after_proposer hook: model started loading then failed with "Exit code: null". Smoke then ran with no model loaded → infra_error=1/1. after_eval hook also failed ("Cannot find a model with the identifier qwen/qwen3-32b" since it never loaded).
- **Root cause:** `--context-length 65536` CLI flag does not work for dense qwen3 base models (qwen3-32b, and by extension qwen3-14b). Only works for MoE variants (qwen3-30b-a3b-2507 loads at 32768 successfully). This is a model-family-specific LMS CLI limitation — context must be set in LMS UI for dense qwen3 models.
- **Verdict:** Run D (qwen3-14b) same family — will hit same ctx flag failure. **Both C and D deferred** until user sets ctx=65536 in LMS UI (My Models → qwen3-32b / qwen3-14b → Settings → Context Length).
- **Skipping to Run E** (mistral-small-3.2, openai format, no ctx-flag risk, different arch).
- **Log:** `log/tau3_p2/qwen36_loop_qwen3_32b_task_c4_p2_cycle1.log`, v_seq=253

### ⚠ Run D DEFERRED — qwen3-14b LMS task agent

- **Reason:** Same dense-qwen3-family ctx CLI issue as Run C. `--context-length` flag fails; model would load at LMS default (4096) which is insufficient for tau3.
- **Unblock:** Set ctx=65536 AND froggeric v13 template in LMS UI for `qwen/qwen3-14b`, then retry.

### ✅ Run E COMPLETE — mistral-small-3.2 LMS task agent (2026-05-13T10:37Z→11:18Z, ~41 min)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `openai/mistralai/mistral-small-3.2` LMS task/user, c=4, ctx=65536
- **Result:** PASS (trivial gate, first run for workflow), **val_score=0.0750** (3/40 tasks)
- **Proposer:** ✅ clean — 4 iters, 4 tool_calls, 1 tool_error, end_turn. v_seq=255.
- **Model load:** ✅ 49.98s, 14.17 GiB, `--context-length 65536` flag WORKS (Llama arch confirmed)
- **Smoke:** ✅ PASSED
- **Full eval:** n_simulations=40, n_evaluated=40, **infra_errors=0** — clean full eval, no TASK_TIMEOUT issues. All 40 tasks completed.
- **Verdict:** mistral-small-3.2 **infra-viable** (40/40 clean) but **retail-weak** (0.0750). Worse than devstral (~0.33) and gpt-oss-20b (0.30). Llama arch function-calling on retail multi-turn is much weaker than qwen3 family.
- **Key learning:** `--context-length 65536` flag WORKS for Llama/mistral (confirmed). Failure in Run C was dense-qwen3-specific.
- **After-eval:** proposer (qwen3.6-35b-a3b) reloaded OK. LMS clean.
- **Log:** `log/tau3_p2/qwen36_loop_mistral_s32_task_c4_p2_cycle1.log`, v_seq=255

### ⚠ Run F CLOSED — nvidia_nemotron-cascade-2-30b-a3b LMS task agent (2026-05-13T11:20Z→12:10Z, ~50 min)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `openai/nvidia_nemotron-cascade-2-30b-a3b` LMS task/user, c=4, ctx=65536
- **Result:** decision=SANDBOX_ERROR, val_score=None — overall sandbox timeout (2400s) killed eval at 27/40 complete
- **Proposer:** ✅ clean — 6 iters, 6 tool_calls, 1 tool_error, end_turn. v_seq=257.
- **Model load:** ✅ 20.91 GiB, `--context-length 65536` flag WORKS (nemotron_h_moe arch confirmed)
- **Smoke:** ✅ PASSED
- **Partial eval:** 27/40 complete when killed, **avg_reward=0.41 (N=27)** — best non-qwen3.6 result in this sweep. Reward recovered from early dip (0.25 at N=12 → 0.41 at N=27). Some tasks on R1 retry (task 65 hit 427s before completing). Hard tasks ~5+ min each.
- **Verdict:** **Strongest model tested so far after qwen3.6-35b-a3b.** Full eval needs TASK_TIMEOUT=7200. Estimated val_score ~0.40 if extended run confirms partial. **Queue extended retry after main sweep completes.**
- **Gate raw_summary:** n_simulations=1, n_evaluated=1 (only smoke; full 40-task results lost to timeout)
- **Log:** `log/tau3_p2/qwen36_loop_nemotron_c2_30b_task_c4_p2_cycle1.log`, v_seq=257

### ✗ Run G CLOSED — exaone-4.5-33b LMS task agent (2026-05-13T12:22Z→12:25Z, ~3 min)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `openai/exaone-4.5-33b` LMS task/user, c=4, ctx=65536
- **Result:** ❌ rc=9 — `lms load 'exaone-4.5-33b' --context-length 65536` → "Error: Failed to load model" (exit code 1)
- **Proposer:** ✅ clean — 5 iters, 5 tool_calls, 1 tool_error, end_turn. v_seq=259.
- **Failure:** after_proposer hook: `lms load 'exaone-4.5-33b' --context-length 65536` spinner ran ~30s then exited with "Error: Failed to load model" rc=1. Smoke ran with no model → infra_error=1/1 (`OpenAIException - No models loaded`). after_eval hook correctly reloaded qwen3.6-35b-a3b (20.55 GiB, 6.37s) — proposer restored.
- **Root cause:** LMS does not support the exaone4 architecture. Model is registered in `lms ls` (25.19 GB) but the exaone4 backend binary is missing or incompatible with this LMS version. Fails even without `--context-length` flag.
- **Verdict:** exaone-4.5-33b **arch-unviable in LMS**. G-base also skipped (arch-unviable). Skip to Run H.
- **Log:** `log/tau3_p2/qwen36_loop_exaone45_33b_task_c4_p2_cycle1.log`, v_seq=259

### ⚠ Run H CLOSED — apriel-1.6-15b-thinker LMS task agent (2026-05-13T12:49Z→13:33Z, ~44 min)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `openai/apriel-1.6-15b-thinker` LMS task/user, c=4, ctx=65536
- **Result:** decision=SANDBOX_ERROR, val_score=None — 2400s overall timeout
- **Proposer:** ✅ clean — 4 iters, 4 tool_calls, 1 tool_error, end_turn. v_seq=261.
- **Model load:** ✅ 8.99 GiB, 32.38s. `--context-length 65536` WORKS (Llama arch confirmed).
- **Smoke:** ✅ PASSED — task_id='5' evaluated=1 infra_errors=0.
- **Partial eval:** 11/40 complete when killed, **avg_reward=0.09 (N=11)** — 1/11 tasks succeeded. Task 36 stalled at 1320+s (thinking-model retry loop); only task 36 in running queue, other 3 concurrency slots idle. TASK_TIMEOUT=2400s fired at 13:33Z.
- **Gate:** raw_summary n_simulations=1, n_evaluated=1 (only smoke retained). run_dir: `20260513_125253_retail_custom_agent_apriel-1.6-15b-thinker...`
- **Verdict:** **Retail-weak** — avg_reward=0.09 (N=11) similar to mistral-small-3.2 (0.075). Llama arch thinker models not viable for retail multi-turn even at 15B. Thinker overhead causes TASK_TIMEOUT at c=4.
- **Log:** `log/tau3_p2/qwen36_loop_apriel15b_task_c4_p2_cycle1.log`, v_seq=261

### ⚠ Run I CLOSED — nvidia/nemotron-3-nano-omni LMS task agent (2026-05-13T13:44Z→14:30Z, ~46 min)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `openai/nvidia/nemotron-3-nano-omni` LMS task/user, c=4, ctx=65536
- **Result:** decision=SANDBOX_ERROR, val_score=None — 2400s overall timeout at 14:30:36Z
- **Proposer:** ✅ clean — 3 iters, 3 tool_calls, 0 tool_errors, end_turn. v_seq=263.
- **Model load:** ✅ `--context-length 65536` flag WORKS (nemotron_h_moe arch confirmed).
- **Smoke:** ✅ PASSED.
- **Partial eval:** ≥16/40 complete when killed, **avg_reward=0.38 (N≥16)** — ~6 tasks succeeded. Trajectory: 0.60 (N=5) → 0.38 (N=16). Some tasks 400-600s+. Gate raw_summary n_simulations=1, n_evaluated=1.
- **Verdict:** nemotron_h_moe family consistent: ~0.38-0.41 partial avg_reward (compare nemotron-cascade-2 0.41 N=27). **Extended retry queued** (TASK_TIMEOUT=7200) after Run J. ctx=65536 flag WORKS.
- **Log:** `log/tau3_p2/qwen36_loop_nemotron3_omni_task_c4_p2_cycle1.log`, v_seq=263

### ✅ Run J DONE — nvidia/nemotron-3-nano-4b LMS task agent (2026-05-13T14:35Z→15:16Z, ~41 min)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `openai/nvidia/nemotron-3-nano-4b` LMS task/user, c=4, ctx=65536
- **Format:** lms-openai (nemotron_h arch, 4B dense, 2.64 GiB loaded — floor test)
- **Proposer:** v_seq=265, iterations=5, tool_errors=1 (clean)
- **Smoke:** PASS (task_id=5, evaluated=1, infra_errors=0)
- **Task model load:** 10.36s, 2.64 GiB
- **Result:** **val_score=0.3000, PASS, N=40/40, infra_errors=0** — full clean eval
- **Verdict:** 4B floor confirmed. nemotron-3-nano-4b scores 0.30 on τ³ retail (surprisingly capable for 4B). ctx=65536 works (nemotron_h arch).
- **Log:** `log/tau3_p2/qwen36_loop_nemotron3_4b_task_c4_nohup.log`, v_seq=265

---

## 🚀 Active Lift Campaign — qwen3.6-35b-a3b proposer × task model sweep (2026-05-14)

**Goal:** Fix proposer at qwen3.6-35b-a3b (LMS, lms-anthropic). Vary task agent. Fixed user model: `openai/nvidia/nemotron-3-nano-4b` (LMS, 2.84 GB) for all lift runs.
**Baseline sweep results (prior session):**

| Task model | val_score (no proposer) |
|---|---|
| `qwen3.6-27b` (LMS) | 0.8750 |
| `nemotron-3-nano-omni` (LMS) | 0.6250 |
| `qwen3.5-9b` (LMS) | 0.5250 |
| `qwen3-30b-a3b-2507` (LMS) | 0.4250 |
| `qwen3.5-4b` (LMS) | 0.3750 |
| `nemotron-3-nano-4b` (LMS) | 0.3250 |

**Active lift campaign queue:**

| ID | Task agent | Baseline | Status |
|---|---|---|---|
| **T1** | `anthropic/qwen/qwen3.5-9b` | 0.5250 | ✅ **DONE** — c4 smoke crash (AGENT_INSTRUCTION NameError). best_ever=0.4250 (no lift vs baseline 0.5250) |
| **T2** | `anthropic/qwen3.5-4b` | 0.3750 | 🔄 **resume run IN PROGRESS** — c4 killed by crash; 6-cycle resume started ~18:24Z (best_ever=0.4750 in DB) |
| **T3** | `openai/nvidia/nemotron-3-nano-4b` | 0.3250 | ⏳ queued after T2 |
| **T4** | `openai/nvidia/nemotron-3-nano-omni` | 0.6250 | ⏳ queued after T3 |

### T1 smoke v1 (2026-05-14T04:01Z, rc=9)
- **Root cause:** Pre-loaded proposer manually before SWAP mode → double-loaded on cycle start (22+22 GB) → VRAM overflow when qwen3.5-9b tried to load.
- **Fix:** Never pre-load proposer; let SWAP mode handle all model loading.

### T1 smoke v2 (2026-05-14T11:04Z, rc=9)
- **Proposer:** ✅ ran clean (6 iters, 6 tool_calls, 3 tool_errors, end_turn, v_seq=271). SWAP worked: unloaded proposer, loaded qwen3.5-9b (6.10 GiB, 2.98s).
- **Smoke failure:** `litellm.BadRequestError: OpenAIException - ` (empty message) on task 5.
- **Root cause (diagnosed 2026-05-14):** `OPENAI_API_BASE` was set to `http://192.168.1.50:1234` (no `/v1`) because lms-anthropic preset's `BASE_URL` lacks `/v1`. The nemotron user model (`openai/nvidia/nemotron-3-nano-4b`) sent requests to `/chat/completions` (not `/v1/chat/completions`). LMS returns HTTP 200 `{"error":"Unexpected endpoint"}` → LiteLLM fails to parse → empty `BadRequestError`.
- **Fix shipped:** `tau3_p2_local_loop.sh` now hardcodes `OPENAI_API_BASE=http://${LLM_HOST}:1234/v1` for openai/ prefix models.

### T1 smoke v3 (2026-05-14T11:10Z, rc=8 — NameError)
- OPENAI_API_BASE fix applied. SWAP worked: unloaded proposer, loaded qwen3.5-9b (6.10 GiB). Smoke tried task_id='5' → confirmed OPENAI_API_BASE fix working.
- **Failure:** `NameError: name 'HarnessState' is not defined` at `agent.py:374` — proposer (v_seq=271 reused) generated type annotation `def _build_known_facts_context(state: HarnessState) -> str:` without importing `HarnessState`. Python evaluates annotations at module load → NameError → rc=8 (smoke crash).
- **Root cause:** Stochastic proposer codegen bug. write_skill validator catches syntax errors but NOT unresolved name references.
- **Fix:** Retry (fresh proposer generates different code).

### T1 smoke v4 (2026-05-14T11:29Z → 12:27Z, COMPLETE ✅)
- **Cycle 1:** PASS — val_score=0.2750, N=40/40, infra_errors=0. Proposer v_seq=276.
- **Cycle 2:** PASS — val_score=0.4250, N=40/40, infra_errors=0. Proposer v_seq=280. **+0.15 gain in one iteration.**
- **Pipeline status:** FULLY VERIFIED end-to-end. lms-anthropic proposer + anthropic/qwen3.5-9b task + openai/nemotron-4b user + SWAP mode all working correctly.
- **Full 10-cycle T1 run:** launched 2026-05-14T12:28:36Z, workflow=tau3-retail-v1__qwen36prop_qwen35_9b, PID=3307337.
  - **Cycle 1:** PASS — val_score=0.4250, N=40/40, infra_errors=0. Proposer v_seq=282, 4 iters. best_ever_after=0.4250. (12:28Z→12:55Z, ~27 min)
  - **Cycle 2:** FAIL_NO_IMPROVEMENT — val_score=0.4250, N=40/40, infra_errors=0. Tied best_ever (0.4250) but did not beat it. (12:55Z→13:33Z, ~38 min)
  - **Cycle 3:** FAIL_NO_IMPROVEMENT — val_score=0.4000, N=40/40, infra_errors=0. Proposer v_seq=287. best_ever_after=0.4250. (13:33Z→14:04Z, ~31 min)
  - **Cycle 4:** rc=9 SMOKE_CRASH — NameError `AGENT_INSTRUCTION` undefined in proposer codegen. Series stopped. (14:04Z→14:12Z, ~8 min)
  - **T1 VERDICT:** best_ever=0.4250. No lift achieved vs standalone baseline (0.5250). Proposer generated weaker skills than the registered baseline for qwen3.5-9b task agent.

### T2 — qwen3.5-4b task agent (baseline 0.3750) — 2026-05-14
- **Config:** qwen3.6-35b-a3b LMS proposer (lms-anthropic, SWAP mode, ctx=65536/65536) + `anthropic/qwen3.5-4b` task + `openai/nvidia/nemotron-3-nano-4b` user, c=4, 10 cycles
- **SWAP:** PROPOSER=`qwen/qwen3.6-35b-a3b`, TASK=`qwen3.5-4b` (no `qwen/` prefix for 4b — LMS model ID)
- **Workflow:** `tau3-retail-v1__qwen36prop_qwen35_4b`, PID=3438182
- **Launch note:** lms load reported "exit code null" on SWAP init (model already loaded from T1). Proposer confirmed loaded; cycle 1 started cleanly at 14:21:55Z.
  - **Cycle 1:** PASS — val_score=0.4500, N=40/40, infra_errors=0. Proposer v_seq=291, 6 iters. best_ever_after=0.4500. **+0.075 lift vs baseline 0.3750.** (14:21Z→14:56Z, ~35 min)
  - **Cycle 2:** PASS — val_score=0.4750, N=40/40. Proposer v_seq=293, 6 iters. best_ever_after=0.4750. **+0.025 gain.** (14:56Z→15:27Z, ~31 min)
  - **Cycle 3:** SANDBOX_ERROR — val_score=None, 1 infra_error (task_74: empty UserMessage from user-sim). best_ever_after=0.4750 (unchanged). (15:27Z→16:00Z, ~33 min)
  - **Cycle 4:** KILLED — host machine crash mid-eval (smoke had passed, eval was ~21/40). No DB entry written. best_ever remains 0.4750.
  - **Cycles 5-10 (resume run):** started ~2026-05-14T18:24Z, PID=17325, log=`qwen36prop_qwen35_4b_resume_nohup.log`. 6-cycle run with same workflow_id; gate will use best_ever=0.4750 from DB.

---

### ✅ Run F v2 DONE — nvidia_nemotron-cascade-2-30b-a3b extended retry (2026-05-13T15:22Z→16:06Z, ~44 min)

- **Config:** qwen3.6-35b-a3b LMS proposer (swap mode) + `openai/nvidia_nemotron-cascade-2-30b-a3b` LMS task/user, c=4, ctx=65536, **TASK_TIMEOUT=7200**
- **Proposer:** v_seq=267, iterations=6, tool_errors=2 (clean)
- **Smoke:** PASS (task_id=5, evaluated=1, infra_errors=0); task model 20.91 GiB
- **Result:** **val_score=0.5000, PASS, N=40/40, infra_errors=0** — full clean eval. Higher than partial estimate (was 0.31 at N=13, 0.41 at N=27 in v1 — final settled at 0.50).
- **Verdict:** Strongest non-qwen3.6 task agent tested so far with proposer skill boost. nemotron-cascade-2-30b-a3b (nemotron_h_moe) is a genuine mid-tier performer.
- **Log:** `log/tau3_p2/qwen36_loop_nemotron_c2_30b_task_c4_v2_nohup.log`, v_seq=267

### ✅ I-base DONE — nvidia/nemotron-3-nano-omni no-proposer baseline (2026-05-13T16:22Z→17:07Z, ~45 min)

- **Config:** `tau3_baseline.py` (no proposer), `openai/nvidia/nemotron-3-nano-omni` LMS, c=4, ctx=65536, **TASK_TIMEOUT=7200**
- **Result:** **val_score=0.6250, N=40/40, infra_errors=0** — no proposer, clean full eval
- **Trajectory:** avg_reward 0.36@11 → 0.64@25 → 0.68@34 → **0.625 final** (slight end-of-batch settling)
- **Verdict:** 🎯 Strongest no-proposer baseline tested after qwen3.6-35b-a3b (0.75). Beats every proposer-assisted run (F v2 0.50, qwen3.5-9b 0.575). nemotron_h_moe (26 GB) is natively retail-capable — proposer adds minimal marginal value here.
- **v1 FAILED (16:10Z):** infra_errors=40/40 — missing `OPENAI_API_KEY` env var. Fixed in v2.
- **Log:** `log/tau3_p2/nemotron3_omni_nopr_baseline_v2.log`

### ⚠ F-base PARTIAL — nvidia_nemotron-cascade-2-30b-a3b no-proposer baseline (2026-05-13T17:08Z→19:08Z, ~2 hr)

- **Config:** `tau3_baseline.py` (no proposer), `openai/nvidia_nemotron-cascade-2-30b-a3b` LMS, c=4, ctx=65536, timeout=7200s/task
- **Result:** ⚠ SANDBOX_ERROR — one of the last 3 tasks hit the 7200s per-task timeout. 37/40 complete, **avg_reward=0.43 (N=37)** — estimated val_score ~0.43
- **Verdict:** Proposer uplift confirmed: F-base (no proposer) ~0.43 vs F v2 (proposer) 0.5000 → **+0.07 proposer uplift**. Model natively at ~0.43 floor.
- **Log:** `log/tau3_p2/nemotron_c2_nopr_baseline.log`

---

## ⚠️ JIT-FALLBACK DISCOVERY (2026-05-12, post-compact session)

**The "inverse scaling 4B > 9B > 35B" finding from Runs 21, 22 is INVALID.**

Root cause: `qwen/qwen3.5-4b` **does not exist** as an LMS model identifier (verified
2026-05-12: `qwen/qwen3.5-9b` exists, `qwen/qwen3.5-4b` does not). In prior runs, JIT loading was enabled in LMS, so when the loop called `anthropic/qwen/qwen3.5-4b` for task agent/user, LMS silently fell back to whatever was loaded — `qwen/qwen3.6-35b-a3b`. So Run 21's 0.8250 was actually qwen3.6-35b-a3b all-3-roles via the `/v1/messages` (anthropic-compat) endpoint with the froggeric v13 template, NOT a 4B task agent.

**Diagnostic smoke (2026-05-12T06:46Z, JIT disabled, real `qwen3.5-4b` loaded):**
- `qwen36_lms_qwen35_4b_diag_smoke` — killed at 10/40 after ~30 min, **avg reward 0.30** (N=10).
- Run 21 at N=10 was ~0.80. Real 4B is 50pp lower → conclusive: Run 21 used qwen3.6-35b-a3b for task agent.

**Corrected interpretation:**
| Run | Score | What probably happened |
|---|---|---|
| Run 15 (0.7500) | qwen3.6-35b-a3b all-3, openai compat, v12 template |
| Run 21 (0.8250) | qwen3.6-35b-a3b all-3 (JIT-fallback for task/user via /v1/messages), v13 template |
| Run 22 (0.7250) | qwen3.6-35b-a3b proposer + EITHER real 9B (if JIT served it) OR qwen3.6 (fallback) |
| Run 23 v2 (0.6750) | qwen3.6:27b dense Ollama proposer + qwen3.6-35b-a3b task/user (JIT-fallback) |
| Real 4B diag (~0.30) | proposer qwen3.6-35b-a3b + real qwen3.5-4b task — 4B is genuinely much worse |

**The actual win driving Run 21's 0.8250 vs Run 15's 0.7500 was:**
1. v13 template (vs v12) for task agent / user simulator
2. Anthropic /v1/messages routing (vs openai /v1/chat/completions) for task/user role

**Real scale-up config:** qwen3.6-35b-a3b all-3-roles with v13 template + anthropic routing for task/user.

### Completed run — `qwen36_all3_real_scaleup_5c` (2026-05-12T07:20 → 09:33, 2h13m)

| Cycle | val_score | Decision | Proposal | Strategy summary |
|---|---|---|---|---|
| 1 | **0.7500** | PASS | `e02b7643` | Fix call_history tracking |
| 2 | 0.6750 | rejected | `cae4c1f5` | task_state + filtered fact extraction |
| 3 | 0.7250 | no_improvement | `d9796715` | JSON fact extraction + dedup |
| 4 | **0.8250** 🎯 | PASS | `917d8d89` | lookup_tracker + STOP at 8 tool calls |
| 5 | 0.7000 | no_improvement | (query for id) | TBD |

**Best: 0.8250 (cycle 4).** Mean: 0.7350. Range: 0.6750 - 0.8250.

**Two distinct 0.825 strategies now confirmed:**
1. `33f6e90d` (Run 21): "Auto-populate known_facts from tool outputs and inject into system prompt for cross-turn memory" — memory-injection
2. `917d8d89` (cycle 4): "lookup_tracker + STOP after 8 tool calls" — budget/loop-prevention

→ The ceiling is real (~0.825) but reachable via multiple skill patterns. qwen3.6-35b-a3b's *task-agent* capability is the ceiling, not skill quality. Reinforces "test weaker but instructable task agents" hypothesis.

### Task agent test results (post-scale-up)

| Task | Task agent | val_score | Notes |
|---|---|---|---|
| #6 | gpt-oss-20b LMS | **0.3000** | PASS, 40/40 clean, ~22 min. 52pp below qwen3.6 baseline. Confirms weaker task agent does not lift via skill. |
| #7 | qwen3-coder-30b LMS | DEFERRED | VRAM constraint (35b-a3b 20.5 GB + 30b-coder 18 GB > GPU). Workaround: reduce 35b-a3b ctx or pull on Ollama. |

### Completed run — `qwen36_loop_devstral2_task_smoke` (task #5, finished 2026-05-12T03:30Z, pre-crash)

- **Result:** ❌ SANDBOX_ERROR. 40/40 infra_errors. val_score=None.
- **Config:** proposer qwen3.6-35b-a3b LMS + task/user `openai/mistralai/devstral-small-2-2512` LMS
- **Root cause:** LMS jinja template error on every task — "After the optional system message, conversation roles must alternate user and assistant roles except for tool calls and results." Devstral's chat template doesn't tolerate tau3's tool-call/result turn structure.
- **v_seq:** 192. Proposal `22152c06`. Loop ran clean (5 iters, 5 tool_calls, 1 tool_error) — the failure is in the task-agent side, not the proposer.
- **Fix needed:** apply froggeric-style template override in LMS UI before retry (same play as qwen3.5-9b → v13). Deferred.
- **Log:** `log/tau3_p2/qwen36_loop_devstral2_task_smoke_p2_cycle1.log`

### System crash recovery (2026-05-12T~03:30Z → 16:00Z+ UTC)

Computer crashed after devstral run completed. Reboot ~16:00Z. Restored:
- `ownevo-postgres` container restarted
- LMS reloaded: `qwen/qwen3.6-35b-a3b` (proposer) + `qwen/qwen3.5-9b` (task agent for #4)

### Completed run — Run 27 `qwen36_loop_qwen35_9b_task_smoke` (2026-05-12T16:14Z → 17:03Z, ~49 min)

- **Result:** ❌ SANDBOX_ERROR. 37/40 evaluated, 3 infra_errors (tasks 36, 100, and 1 more — LMS HTTP 500 "Context size has been exceeded" on `/v1/messages`).
- **Config:** qwen3.6-35b-a3b LMS proposer + `anthropic/qwen/qwen3.5-9b` LMS task/user, v13 template, **ctx=32768** (TOO SMALL).
- **Partial data (N=31):** avg reward **0.6129** (19 wins / 12 zeros) — well below Run 22's 0.7250 attributed to "9B" task agent.
- **JIT-fallback hypothesis CONFIRMED:** Real 9B at ~0.61 (partial) ≪ Run 22's 0.7250. Run 22 must have served qwen3.6-35b-a3b via JIT-fallback, not real 9B.
- **Proposal:** v_seq=194, `9ae40d65`. Loop ran clean (4 iters, 4 tool_calls, 1 tool_error, end_turn).
- **Fix:** Reload qwen3.5-9b with `-c 65536` (same fix applied to qwen3.6-35b-a3b in early-May runs).
- **Log:** `log/tau3_p2/qwen36_loop_qwen35_9b_task_smoke_p2_cycle1.log`

### Completed run — Run 28 `qwen36_loop_qwen35_9b_task_ctx65k` (2026-05-12T17:09Z → 17:31Z, ~22 min)

- **Result:** ✅ **PASS, val_score=0.5750.** 40/40 evaluated, 0 infra_errors.
- **Config:** qwen3.6-35b-a3b LMS proposer + `anthropic/qwen/qwen3.5-9b` LMS task/user (v13 template), **ctx=65536** (ctx fix vs Run 27).
- **Proposal:** v_seq=196, `280130ad`. Loop: 5 iters, 5 tool_calls, 1 tool_error, end_turn.
- **Key finding — JIT-fallback DEFINITIVELY CONFIRMED:** Real qwen3.5-9b lands at **0.5750**, vs Run 22's claimed 0.7250 for "9B" task agent — a 15pp gap. Run 22 served qwen3.6-35b-a3b via JIT-fallback, not real 9B. The "inverse scaling 4B > 9B > 35B" claim is fully invalidated; real ranking on retail τ³ task agent is **qwen3.6-35b-a3b (0.75) > qwen3.5-9b (0.575) > gpt-oss-20b (0.30) ≫ qwen3.5-4b (~0.30)**. Bigger > smaller.
- **Log:** `log/tau3_p2/qwen36_loop_qwen35_9b_task_ctx65k_p2_cycle1.log`

### 📦 POST-MERGE BACKLOG (2026-05-12 — dropped from active queue, pick up after session + branch merge)

Active proposer queue continues through #10–#14 (glm-4.7-flash, Ollama qwen3:30b variants, gemma-4-31b retry, qwen3.6-27b). Everything below is deferred:

**Model-swap-enabled tests (need wrapper swap mode — implemented 2026-05-12 in `tau3_p2_local_loop.sh` + `run_tau3_loop.py`):**

- **#19 [P8a] qwen3-30b-a3b-2507 LMS** (redownloaded). Same MoE family as winner (qwen3.6-35b-a3b). Use swap mode: `OWNEVO_TAU3_SWAP_PROPOSER=qwen/qwen3-30b-a3b-2507 OWNEVO_TAU3_SWAP_TASK=qwen/qwen3.6-35b-a3b` with the wrapper.
- **#20 [P8b] qwen3:30b-a3b-instruct-2507-q4_K_M Ollama**. Same model on Ollama. Bypasses LMS load issues; tests whether Ollama can serve 2507 alongside LMS task agent.

**Task-agent tests (need different topologies):**

- **#7 qwen3-coder-30b task agent** — VRAM blocked at LMS (35b-a3b 20.5 + coder 18 > 35 GB GPU). Options: (a) swap mode, (b) pull on Ollama (`ollama pull qwen3-coder:30b`), (c) lower 35b ctx.
- **#15 qwen3.5-4b task agent** — real 4B (was killed at 10/40 @ 0.30 in diag smoke). Run as full 40-task cycle to confirm the 0.30 ceiling. Low priority.
- **#21 gemma-4-26b-a4b as TASK AGENT** (mixed topology). Untested role for this variant (proposer failed Run 29). Pair with qwen3.6-35b-a3b proposer. Try Ollama variant too — bypasses LMS max_tokens cap.
- **#24 granite-4.1-30b TASK AGENT** (deferred 2026-05-12). Same family as granite-4.1-8b which hit UTF-8 surrogate bug in Run 37 v2; 30B variant expected to repro. Revisit only after either: (a) LiteLLM payload sanitization for U+D800-DFFF surrogates, or (b) LMS update with a different chat-template that doesn't generate the offending byte sequences. Also needs LMS-side load (not currently in catalog).

**Future-model task-agent tests:**

- **#16 Apriel-1.6-15B-Thinker** (ServiceNow-AI, Q4_K_M GGUF)
- **#17 EXAONE-4.5-33B** (LG AI)
- **#18 Nemotron Cascade 2 30B A3B** (Nvidia)
- **gemma-4-e2b** (LMS, ~2B active, 4.41 GB) — speed-focused floor test; smallest local variant. After session + merge.

**Ollama task-agent queue (added 2026-05-12, post-merge):**

Ordered by expected information value. Each is 1-cycle smoke (40 tasks @ c=2) with qwen3.6-35b-a3b LMS as proposer + `ollama_chat/<model>` task/user.

1. ~~**`devstral-small-2:latest`**~~ — **Run 39 DONE.** Ollama Modelfile template IS compatible with tau3 tool-call/result turns (LMS jinja bug is stack-specific). Contaminated run (proposer codegen bug). Infra-viable, reward TBD.
2. ~~**`qwen3:30b-a3b`**~~ — **Run 40 KILLED.** Thinking-bound task agent (~450s/smoke task). Unviable without `think:false` in sandbox LiteLLM call for qwen3moe family.
3. ~~**`qwen3.5:4B`**~~ — **SKIPPED.** Same thinking issue as Run 40. LMS Run 36 v2 already locked 0.22-0.30 ceiling on real 4B.
4. ~~`granite4.1:3b`~~ — **DROPPED** 2026-05-12 after Run 38 v2 locked granite4.1:8b Ollama at ceiling ~0.10.
5. ~~**`gemma4:e2b`**~~ — **Run 41 v2 DONE.** SANDBOX_ERROR, val_score=None, avg_reward=0.00. Infra-viable (smoke passes with routing fix) but retail-weak (~2B active params).
6. ~~**`granite3.3:8b`**~~ — **SKIPPED.** No conversation-depth workaround surfaced; granite family confirmed ceiling ~0.10 regardless of stack.
7. ~~**`gemma3:12b`**~~ — **Run 42 FAILED (rc=9).** `gemma3:12b does not support tools` — Ollama API hard rejection. Prior-gen gemma lacks tool-calling Modelfile template.
8. **`gemma4:26b`** (Ollama, 16 GB) — ✗ **Run 43 DONE.** SANDBOX_ERROR, val_score=None, avg_reward=0.00. Tasks ~360-380s each at c=2 → timeout before 40 tasks. All tasks hit `max_steps`. Retail-weak + too slow.
9. **`devstral-small-2:latest`** (Ollama, 14 GB) — ⚠ **Run 44 DONE.** SANDBOX_ERROR (2400s budget), partial avg_reward=0.33 (N=3). Retail-capable! Retry as Run 45 with TASK_TIMEOUT=7200 for real val_score.
10. **`qwen3.6:35b-a3b`** (Ollama) **swap-mode** — requires code change #5.
10. **`qwen3.6:35b-a3b`** (Ollama, 22.3 GB) **with swap-mode** — backend-diversity check on the winner. ⚠ requires code-change backlog item #5.

Each: `OWNEVO_TAU3_CYCLES=1 OWNEVO_TAU3_CONCURRENCY=2 OLLAMA_API_BASE=http://192.168.1.50:11434 ./scripts/tau3_p2_local_loop.sh qwen/qwen3.6-35b-a3b lms-anthropic <workflow> anthropic ollama_chat/<model> ollama_chat/<model>`.

Each is 1-cycle smoke as TASK AGENT with qwen3.6-35b-a3b LMS as proposer.

**Dense LMS proposers (LOWEST priority — confirmed too slow under VRAM contention; needs swap-mode + patience):**

- **gemma-4-31b dense LMS proposer** — Run 34 killed; would need swap-mode to give it full GPU during proposer phase. Run 19 already showed it emits clean proposals (just slow). Expected wall-time: ~30 min proposer + 30 min eval = ~1hr per cycle with swap.
- **qwen/qwen3.6-27b dense LMS proposer** — Run 35 killed during swap-mode load (user said "too slow"). At ~3-5 tok/s native, proposer phase alone may take >30 min. Likely not worth running.

**Wrapper commands ready in `tau3_p2_local_loop.sh` — see Resume protocol § "What to RUN NEXT" for invocation templates.**

---

### Task #8 SKIPPED — qwen3-30b-a3b proposer load issues

- **2507 variant:** `lms load qwen/qwen3-30b-a3b-2507` fails 3× with `(X) CAUSE (Exit code: null)` even after daemon restart. Other models load fine. Model-file / template-specific issue, not investigated.
- **Non-2507 variant:** `qwen/qwen3-30b-a3b` loads alone (18.63 GB @ ctx=65536) — `--context-length 65536` (long-form flag) works where `-c` short-form hung the first attempt this session. Quirk in CLI flag parsing or daemon state, not reproducible reliably.
- **VRAM cap discovered:** 30b-a3b auto-unloads when 35b-a3b loads at ctx=65k (combined ~40.7 GB > GPU). Real GPU headroom ≈ 40 GB total.
- **Decision (per user 2026-05-12T17:55Z):** skip #8, jump to #9 gemma-4-26b-a4b. 2507 was the fastest proposer in queue but architecturally a sister of the current winner (same qwen3-MoE family) — marginal info value lost.

### Completed run — Run 29 `gemma4_26b_a4b_proposer_smoke` (2026-05-12T18:00Z → 18:09Z, ~10 min)

- **Result:** ❌ SANDBOX_ERROR. 0/40 evaluated, 40 infra_errors.
- **Config:** proposer `google/gemma-4-26b-a4b` LMS @ ctx=32k + task/user `anthropic/qwen/qwen3.6-35b-a3b` LMS @ ctx=65k. Concurrency=3 (old wrapper).
- **Proposer phase OK:** gemma ran clean — 7 iters, 6 tool_calls, 1 tool_error, end_turn, 14,717 out tokens (did NOT hit max_tokens cap, so the num_predict pre-req warning was stale). v_seq=199, proposal `e7b6d533`.
- **Eval failure:** `AttributeError: 'NoneType' object has no attribute 'validate'` at `orchestrator.py:865` → `agent_msg.validate()`. All 40 tasks. The proposal's `HarnessAgent.generate_next_message` returns None for some condition → orchestrator can't validate the agent message.
- **Verdict:** gemma-4-26b-a4b is mechanically viable as loop driver but its codegen quality for this skill is poor — same failure class as Run 20 (qwen3.6 `self.known_facts` uninit) and granite-30B (missing HarnessAgent). **Mark as proposer-weak.**
- **Log:** `log/tau3_p2/gemma4_26b_a4b_proposer_smoke_p2_cycle1.log`

### Completed run — Run 30 `glm47flash_proposer_smoke` (2026-05-12T18:14Z → 18:27Z, ~13 min)

- **Result:** ❌ rc=1, **httpx.ReadTimeout** in proposer phase first call (LMS path).
- **Config:** proposer `zai-org/glm-4.7-flash` LMS @ ctx=32k + task/user `anthropic/qwen/qwen3.6-35b-a3b` @ ctx=65k. Concurrency=4 (new default).
- **Root cause:** AsyncOpenAI client at `run_tau3_loop.py:460` uses default 600s SDK timeout. glm-4.7-flash didn't emit first token within 10 min.
- **Fix shipped (commit 8307385):** `timeout=1800.0` on both `AsyncOpenAI()` + `AsyncAnthropic()` constructors. Also adds swap-mode hooks.
- **Log:** `log/tau3_p2/glm47flash_proposer_smoke_p2_cycle1.log`

### Killed run — Run 31 `glm47flash_proposer_smoke_retry` (2026-05-12T18:50Z, killed at ~3 min)

- **Result:** killed by user after web search revealed **known LMS bugs with glm-4.7-flash:**
  - LMS's bundled llama.cpp lacks full glm-4.7 architecture support (users told to use llama.cpp directly).
  - Tool-call / freezing bugs with default sampling params — fixed by removing `--temp 0.7 --min-p 0.0 --top-p 0.80 --top-k 20 --repeat-penalty 1.05`.
  - MTP (multi-token-prediction) drops throughput 10× when active.
- **Decision:** pivot to Ollama path (Ollama has full glm-4.7-flash arch support; model already pulled locally as `glm-4.7-flash:latest` 19 GB).
- **Sources:** [Unsloth glm-4.7-flash docs](https://unsloth.ai/docs/models/tutorials/glm-4.7-flash), [HF Jan-21 reupload thread](https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF/discussions/10), [ollama.com/library/glm-4.7-flash](https://ollama.com/library/glm-4.7-flash).

### Killed run — Run 32 v1 `glm47flash_ollama_proposer_smoke` (2026-05-12T18:55Z → ~19:09Z, killed)

- **Result:** killed at ~14 min after user reported Ollama loaded glm-4.7-flash with **18% CPU / 82% GPU split** — VRAM contention (LMS qwen3.6-35b-a3b @ ctx=65k = 22 GB + Ollama glm-4.7-flash 19 GB = 41 GB > GPU). 1 GB spilled to CPU caused 3-10× proposer slowdown.
- **Caveat (post-incident):** sandbox container `ownevo-sb-da2f1f4915ef` from Run 32 v1's eval phase was **orphaned** when wrapper was killed — kept hammering LMS for ~20 min before manual `docker kill`. **TODO post-merge:** wrapper trap should `docker kill ownevo-sb-*` on SIGTERM.

### Completed run — Run 32 v2 `glm47flash_ollama_proposer_smoke_v2` (2026-05-12T19:11Z → 19:41Z, ~30 min)

- **Result:** ✅ **PASS, val_score=0.6750.** 40/40 evaluated, 0 infra_errors.
- **Config:** glm-4.7-flash Ollama proposer + `anthropic/qwen/qwen3.6-35b-a3b` LMS @ ctx=65k task/user. Concurrency=4.
- **Proposer phase:** ~5.5 min (Ollama loaded + 5 iters + 1 tool_error, end_turn). 43,573 in / 10,143 out tokens. v_seq=206, `fc360914`.
- **Ollama daemon config (key unlock):** `OLLAMA_GPU_COUNT=2` keeps glm-4.7 off CPU; `OLLAMA_MAX_LOADED_MODELS=1` auto-frees GPU for LMS after proposer phase.
- **Verdict:** glm-4.7-flash (DeepSeek-2 arch) is a viable cross-family proposer. **0.6750 matches qwen3.6:27b dense Ollama (Run 23 v2)** — below the qwen3.6-35b-a3b LMS ceiling (~0.75) but credibly within variance. Different family/arch → confirms proposer diversity is achievable on Ollama.
- **Log:** `log/tau3_p2/glm47flash_ollama_proposer_smoke_v2_p2_cycle1.log`

### Completed run — Run 33 `qwen3_30b_instruct_ollama_proposer_smoke` (2026-05-12T19:43Z → 19:48Z, ~5 min)

- **Result:** ❌ rc=6 — loop agent did NOT call write_skill. 6 iters, 5 tool_calls, 0 tool_errors, end_turn, only 2,501 out tokens.
- **Verdict:** qwen3:30b-instruct **proposer-unviable**. Dense + non-thinking instruct-tuned variant explored but never committed a proposal. Same failure family as granite-4.1-8b. Mark proposer-weak.

### Killed run — Run 34 `gemma4_31b_proposer_smoke` (2026-05-12T19:48Z → ~19:58Z, killed at ~10 min)

- VRAM contention slowed dense gemma-4-31b to unusable. Deferred to post-merge.

### Killed run — Run 35 `qwen36_27b_proposer_smoke_swap` (2026-05-12T20:25Z, killed during swap-mode load)

- First production test of swap-mode hooks: wrapper pre-cycle hook correctly unloaded 35b-a3b and started loading 27b. Killed by user before 27b finished loading ("too slow"). Dense qwen3.6-27b at ~3-5 tok/s native — proposer phase alone would be 30+ min even with full GPU.
- **Swap-mode confirmed working** at the load-orchestration layer (hooks ran in correct order, env vars propagated). Remaining concern is just dense-model speed, not the swap pattern.
- **Both dense LMS proposers (gemma-4-31b, qwen3.6-27b) moved to post-merge backlog lowest priority** — confirmed not worth waiting at current daemon throughput.

### Completed/Killed runs — Run 36 v1 & v2 (task #22)

- **Run 36 v1** `qwen36_loop_qwen35_4b_real_task_smoke` (2026-05-12T20:32Z, ~10 min): **SANDBOX_ERROR**, qwen3.6-35b-a3b proposer codegen emitted `def _build_next_step_directive(state: HarnessState)` without defining `HarnessState` → NameError on agent.py import → 40/40 infra. **This exact failure mode is now caught by commit `aaa9fef` write_skill validator + commit `08f2249` class checks.** v_seq=211, `ed26b810`.
- **Run 36 v2** `..._v2` (2026-05-12T20:46Z → 21:26Z, killed at 40min): proposer codegen valid this time; **eval phase reached** with real qwen3.5-4b. **Killed at 9/40, avg reward 0.2222** (2 wins / 7 zeros). Per-task latency ~3.5 min (heavy thinking on small model). Trajectory matches diag smoke (10/40 @ 0.30). **Verdict: real qwen3.5-4b task agent ceiling ≈ 0.20-0.30** — confirms post-JIT-fallback finding (bigger > smaller for retail task agent).

### Completed runs — Run 37 v1 & v2 (task #23, granite-4.1-8b task agent retry c=4)

- **Run 37 v1** `qwen36_loop_granite8b_task_smoke_c4` (2026-05-12T21:27Z, rc=8 in ~5 min): smoke crashed with `ValueError: Not all tasks were found for task set retail - test: {'0'}` — tau2 retail task_ids aren't sequential 0..N. **Fixed in commit 58cf93a** by adding candidate fallback list `["1", "5", "2", "3", "9"]` + `OWNEVO_TAU3_SMOKE_TASK_ID` override in `run_tau3_loop.py`.
- **Run 37 v2** `qwen36_loop_granite8b_task_smoke_c4_v2` (2026-05-12T21:44Z → 21:48Z, **rc=9 in ~4 min**): proposer phase succeeded (v_seq=217, `d8e23974`, 5 iters/5 tool_calls); smoke task_id=`"1"` correctly fell through to `"5"`, then `infra_error=1/1`. **Verdict: granite-4.1-8b emits UTF-8 surrogates → `litellm.InternalServerError: 'utf-8' codec can't encode characters in position 310-311: surrogates not allowed`.** Same family bug as `granite4.1:8b` em-dash issue documented in CLAUDE.md. **granite-4.1-8b task-agent-unviable on LMS via openai routing.** Smoke gate saved ~24 min vs full 40-task eval. Validator fallback list works correctly.
- **Log:** `log/tau3_p2/qwen36_loop_granite8b_task_smoke_c4_v2_p2_cycle1.log`

### Killed — Run 38 v1 & v2 (task #25/#26, granite4.1:8b Ollama task agent)

- **Run 38 v1** `qwen36_loop_granite8b_ollama_task_smoke` (2026-05-12T21:58Z → 22:13Z, killed at ~15 min, NUM_PARALLEL=1 + c=2): 4/40 @ avg 0.50. ETA ~2 hr full eval. Killed for throughput.
- **Run 38 v2** `qwen36_loop_granite8b_ollama_task_smoke_c4` (2026-05-12T22:15Z → 22:42Z, killed at ~27 min, NUM_PARALLEL=4 + c=4): **20/40 @ avg 0.10**. Trajectory: 0.50 (N=4) → 0.20 (N=10) → 0.12 (N=16) → 0.10 (N=20). 4+ `too_many_errors` early terminations — granite degrades at conv depth ≥10. Proposal v_seq=221 `bcc0cd9d`.
- **✅ Key win: UTF-8 surrogate bug GONE on Ollama path.** Same model, different chat template (Modelfile vs LMS bundled jinja) → no surrogate bytes. **Bug is LMS-template-specific, not granite-intrinsic.**
- **Verdict:** granite4.1:8b on Ollama is **infra-clean but the WEAKEST task agent in this branch** — ceiling ≈ 0.10-0.12 on retail, **worse than gpt-oss-20b (0.30) and real qwen3.5-4b (0.22-0.30).** Granite family confirmed retail-unviable as task agent regardless of stack. Skip all granite task-agent retries; mark granite4.1:3b as redundant.
- **Logs:** `log/tau3_p2/qwen36_loop_granite8b_ollama_task_smoke_p2_cycle1.log`, `..._c4_p2_cycle1.log`

### 🔧 Code-change-needed backlog (post-merge cleanup)

1. ~~Bump OpenAI/Anthropic client timeout 600s → 1800s~~ ✅ shipped (commit 8307385).
2. ~~Swap-mode hooks for LMS proposer ↔ task-agent~~ ✅ shipped (commit 8307385).
3. **Per-backend concurrency default should key off `TASK_AGENT_MODEL` prefix, not `BASE_URL_OR_PRESET`** — current logic in `tau3_p2_local_loop.sh` picks Ollama=2 when proposer is Ollama, but eval-phase concurrency is bounded by the task agent backend (here LMS, which handles 4 fine). Fix is ~5 lines. *(Related: OLLAMA_API_BASE default was also keyed off BASE_URL — fixed this session to default to `http://${LLM_HOST}:11434`.)*
4. **Wrapper SIGTERM trap to clean up sandbox containers** — when the parent wrapper / python is killed mid-eval, the spawned `ownevo-sb-*` container is orphaned and keeps consuming LMS quota. Add `trap 'docker kill ownevo-sb-* 2>/dev/null' EXIT` to wrapper (or scope by workflow id).
5. **Swap-mode for Ollama-proposer + LMS-task topology** — current `OWNEVO_TAU3_SWAP_*` assumes both on LMS. Add `OWNEVO_TAU3_SWAP_OLLAMA_PROPOSER` (or generalize) so Ollama proposer can request LMS unload during its phase and reload before eval. Relevant because LMS task agent + Ollama proposer just hit the spill problem.

**Findings so far:**
- Cycle 1 reproduces Run 15 (0.7500), confirming Run 21's "0.8250 record" was real proposal-quality variance (NOT routing noise) — the `33f6e90d` known_facts proposal genuinely scored 0.825, no other proposal has matched it.
- Cycle 2's new skill scored *worse* than cycle 1, despite the proposer reading cycle 1's results — qwen3.6-35b-a3b may not effectively use the feedback loop to improve over cycles.
- **Hypothesis emerging:** the proposer self-loop has diminishing returns when proposer == task agent. Reinforces the "weaker task agent" experiment queued next.

**Architectural note:** `persist_gate_run` holds a single DB transaction for the entire ~25 min gate eval. Cycle N's proposal is only visible in the proposals table after cycle N commits — explains why mid-cycle DB queries miss the in-flight proposal.

### Next queue (12 runs after scale-up)

1. **4 task-agent tests** in throughput order — tests "weaker-but-instructable task agent" hypothesis (qwen3-coder-30b → gpt-oss-20b → devstral-small-2 → qwen3.5-9b)
2. **7 proposer tests** in throughput order — tests model-family-and-arch diversity for proposer role (qwen3-30b-a3b-2507 → gemma-4-26b-a4b → glm-4.7-flash → qwen3:30b-instruct → qwen3:30b-a3b → gemma-4-31b → qwen3.6-27b)

See § "What to RUN NEXT" below for table + wrapper commands.

---

## TL;DR — where we are

- **Goal:** get an end-to-end τ³ retail run using local LLMs only (loop +
  task agent + user simulator) producing a `val_score`. ✅ **DONE 2026-05-11**.
- **Confirmed win:** `qwen36lms_ctx65k_smoke` — PASS, val_score = **0.7500**,
  40/40 evaluated, 0 infra_errors, gate-pass, proposal v_seq=133, ~27 min.
  Config: LMS `qwen/qwen3.6-35b-a3b` all 3 roles, froggeric v12 template,
  ctx=65536.
- **Second end-to-end win:** `gemma4_e4b_full_local_64k` — PASS,
  val_score = **0.1750**, 40/40 evaluated, 0 infra_errors, gate-pass,
  proposal v_seq=141, ~39 min wall-time. Confirms gemma-4-e4b can drive
  the loop end-to-end at ctx=65k; retail reward is weak vs qwen3.6 (0.75)
  but the path is fully clean. Loop: 6 iters / 5 tool_calls / 2 tool_errors.
- **Granite-30b verdict (2026-05-12):** SANDBOX_ERROR. Granite-30B
  DOES emit write_skill (vs 0 from granite-8B) but its `agent.py` lacks
  the required `HarnessAgent` class → 40/40 import failure. Codegen
  quality too low for self-driven proposer. See "Last failed run" below.
- **#25 unsloth/qwen3.6 verdict (2026-05-12):** SANDBOX_ERROR. 39/40 at
  avg reward **0.77** (would beat winner 0.75 by 2pp if gate passed).
  Single long-tail task 101 hit the 4hr per-task wall (initial 44 min
  + R1 70 min + R2 → 14400s timeout). **Cross-quant generalizability
  CONFIRMED** — unsloth quant ≈ qwen/ quant within noise band.
- **#29 result (2026-05-11):** SANDBOX_ERROR — qwen3.5-9b jinja template
  error ("No user query found in messages"), 8/40 evaluated, 32/40 infra.
  Transport (anthropic/ routing) confirmed working; template fix needed in
  LMS UI (same froggeric v12 override qwen3.6 required). v_seq=156.
- **#30 result (2026-05-12):** SANDBOX_ERROR. google/gemma-4-31b all-3-roles,
  36/40 evaluated, 4 infra_errors (tasks 55, 56, 60, 61). Root cause: LMS HTTP 500
  "Failed to resolve model metadata for google/gemma-4-31b." — intermittent LMS
  registry failure under load. Avg reward **0.62** (N=36). Loop 7 iters, 0 tool_errors,
  v_seq=158. Duration ~2h32m.
- **Run 20 (2026-05-12):** SANDBOX_ERROR. qwen3.6 loop + qwen3.5-9b (v13 template).
  0/40 evaluated, 40 infra. **v13 template CONFIRMED working** (no jinja errors — different
  failure mode). Root cause: qwen3.6's proposal (v_seq=161) referenced `self.known_facts`
  (uninitialized attr) → AttributeError on all 40 tasks. Proposal codegen bug, not task agent.
  Loop: 7 iters, 7 tool_calls, 2 tool_errors. ~4 min.
- **Run 21 — NEW RECORD (2026-05-12):** PASS. `qwen36loop_qwen35_4b_lms_anthropic_smoke`.
  val_score = **0.8250** (new all-time best, beats 0.7500 by +10pp). 40/40 evaluated, 0 infra_errors.
  Loop: qwen/qwen3.6-35b-a3b LMS, 5 iters, 5 tool_calls, 2 tool_errors, v_seq=163. ~24 min.
  Task/user: **anthropic/qwen/qwen3.5-4b** with v13 froggeric template. **Key finding:** 4B model
  with v13 template is faster (24 min) and scores higher than qwen3.6 all-roles (0.8250 > 0.7500).
- **Run 22 done (2026-05-12):** PASS val_score=0.7250. `qwen36loop_qwen35_9b_lms_anthropic_smoke_v3`. 9B < 4B by −10pp. **Inverse scaling confirmed: 4B > 9B > 35B for task agent.** ~24 min, 40/40 clean.
- **Run 23 v1 FAILED (2026-05-12T02:35Z → 02:51Z):** `qwen36_27b_loop_qwen35_4b_lms_anthropic_smoke`. `httpx.ReadTimeout` after ~15 min. Root cause: qwen3.6:27b is a 27B DENSE model (17.4GB) — disk load (~5 min) + dense-model generation exceeded the 600s httpx timeout. qwen3.6-35b-a3b worked fine because it's MoE (only 3B active params). Fix: `DEFAULT_TIMEOUT_SECONDS` bumped `600s→1800s` in `ollama_native.py` (committed SHA `9a700f1`).
- **Run 23 v2 DONE (2026-05-12T02:56Z → 03:38Z):** `qwen36_27b_loop_qwen35_4b_lms_anthropic_smoke_v2`. **PASS val_score=0.6750.** 6th end-to-end local landing. 40/40 evaluated, 0 infra_errors. Loop: 5 iters, 5 tool_calls, 1 tool_error, v_seq=169, 27649 in / 7860 out. ~41.5 min total. **Key finding:** dense 27B proposer (0.6750) < MoE 35b-a3b proposer (0.8250) — uncontrolled thinking chain + denser model weaker for proposal quality.
- **Queue extended (2026-05-12):** added 5 new tasks per user direction:
  - #27, #28, #29 — mixed-topology smokes with qwen3.5-4b/9b agent+user
    (user said "qwen3.5 4b and 9b should be good for agent and user;
    qwen3-4b-2507 is NOT"). Memory updated accordingly.
  - #30 — gemma-4-31b dense baseline (does dense dodge the max_tokens
    cap that killed MoE gemma-4-26b-a4b?).
  - #31 — qwen3.6-27b dense (smaller/faster cousin of 35B-A3B winner).
- **Branch:** `feat/ollama-loop-runner` — many commits ahead of `dc77adc`
  (session start). All landed code committed + pushed.

---

## Pre-session run index (Runs 8-23, 2026-05-10 → 2026-05-12 early-morning)

Full chronology lives in `docs/TAU3_LOCAL_TESTPLAN.md`. One-line index for grep-friendliness:

| Run | Workflow | Verdict | Detail |
|---|---|---|---|
| 8 | gemma4_e4b_full_local_64k | ✅ PASS 0.1750 | gemma-4-e4b all-3-roles LMS; smallest-viable baseline |
| 11 | qwen36loop_graniteagent_64k_retry | killed | granite-30B all-3 attempt (model file mismatch) |
| 13 | granite_30b_full_local_64k | ❌ SANDBOX_ERROR | granite-30b emitted skill but no HarnessAgent class |
| 14 | unsloth_qwen36_full_local_64k | ❌ SANDBOX_ERROR | 39/40 @ avg 0.77 (task 101 hit 4hr wall); cross-quant ≈ qwen/ |
| 15 | qwen3coder_30b_lms_full_local_64k | ✅ PASS 0.1250 | qwen3-coder LMS retail-weak (vs Ollama 0.15) |
| 16 | qwen36loop_qwen35_4b_ollama_smoke | ❌ SANDBOX_ERROR | ollama_chat/qwen3.5:* HTTP 415 (LiteLLM adapter bug); track CLOSED |
| 17 | qwen36loop_qwen35_9b_ollama_smoke | ❌ SANDBOX_ERROR | same 415 — model-size independent |
| 18 | qwen36loop_qwen35_9b_lms_anthropic_smoke | ❌ SANDBOX_ERROR | LMS jinja template for qwen3.5-9b; fixed by froggeric v13 |
| 19 | gemma4_31b_full_local_64k | ❌ SANDBOX_ERROR | dense 31B clean as proposer & task; 4/40 LMS HTTP 500 infra-flake; avg 0.62 (N=36) |
| 20 | qwen36loop_qwen35_9b_lms_anthropic_smoke_v2 | ❌ SANDBOX_ERROR | v13 confirmed working; codegen bug `self.known_facts` uninit |
| 21 | qwen36loop_qwen35_4b_lms_anthropic_smoke | ✅ PASS 0.8250 | JIT-fallback to 35b-a3b (not real 4B). v_seq=163. |
| 22 | qwen36loop_qwen35_9b_lms_anthropic_smoke_v3 | ✅ PASS 0.7250 | JIT-fallback to 35b-a3b (not real 9B). v_seq=165. |
| 23 v1 | qwen36_27b_loop_qwen35_4b_lms_anthropic_smoke | ❌ TIMEOUT | qwen3.6:27b Ollama dense 600s httpx; bumped to 1800s commit 9a700f1 |
| 23 v2 | qwen36_27b_loop_qwen35_4b_lms_anthropic_smoke_v2 | ✅ PASS 0.6750 | dense 27B Ollama proposer (uncontrolled thinking); v_seq=169 |

Pattern across 4 Ollama all-3-roles attempts (Runs 14, 17, qwen3coder, gptoss20b): every config fails differently, all unviable. Throughput / codegen / latency. Use Ollama for LOOP role only.

LMS daemon wedge incident (between Runs 11–12): `lms load` returned "Terminated" until `lms server stop && start` cleared it.

---

## Live diagnostic commands


```bash
# What's running
ps -ef | grep -E "tau3_p2_local_loop|run_tau3_loop" | grep -v grep

# Sandbox container (will be Up while gate eval runs)
docker ps --format '{{.Names}}\t{{.Status}}' | grep ownevo-sb

# LMS log = source of truth for whether qwen36 is generating cleanly
# (look for empty content or jinja errors)
lms log stream

# Cycle log
tail -f /media/fast_data/work2026/ownevo/ownevo_app/log/tau3_p2/qwen36lms_v12template_smoke_p2_cycle1.log

# If we run anything on Ollama in parallel, this is the diag command
docker logs --tail 50 ollama 2>&1 | grep "POST.*api/chat" | tail -10
# Healthy = 200 status, ~7-13s. Broken = 500 status, 10m0s.
```

**Stop / kill:**
```bash
pkill -TERM -f "tau3_p2_local_loop.sh.*qwen36lms_v12template_smoke"
pkill -TERM -f "scripts/run_tau3_loop.py.*qwen36lms_v12template_smoke"
docker stop $(docker ps -q --filter "name=ownevo-sb")
```

---

## Resume protocol (run after machine reboot)

After a reboot all of these come down. Run in order:

```bash
cd /media/fast_data/work2026/ownevo/ownevo_app

# 1. Postgres
cd infra && docker compose up -d postgres
docker exec ownevo-postgres pg_isready -U ownevo
cd ..

# 2. Ollama (auto-starts as a Docker container if --restart always was set)
docker ps --format '{{.Names}}' | grep -q ollama || bash ~/ollama_open_web_p.sh
curl -s -m 3 http://192.168.1.50:11434/api/tags | python3 -c "import json,sys; print('models:', len(json.load(sys.stdin).get('models',[])))"

# 3. LM Studio — daemon defaults to 127.0.0.1, MUST bind 0.0.0.0 for LAN
lms server stop 2>/dev/null
lms server start --cors --bind 0.0.0.0
ss -tlnp 2>/dev/null | grep 1234   # should show 0.0.0.0:1234, not 127.0.0.1
curl -s -m 3 http://192.168.1.50:1234/v1/models -o /dev/null -w 'http=%{http_code}\n'

# 4. Sandbox image — required for τ³ gate eval
docker images ownevo-sandbox-tau3:0.1.0 | head
# If missing or stale (built before commit 35cdfc5):
make sandbox-image-tau3
```

**Models loaded by sweep scripts on demand.** Don't pre-load unless
debugging. To check / explicitly load:
```bash
lms ps --json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f\"{m['modelKey']}: ctx={m['contextLength']}\") for m in d]"
lms load <model> -c <context_length>     # context override
lms unload <model>
```

---

## What WORKS (confirmed this session)

### Loop drivers (proven end_turn + valid proposal)

| Loop model | API path | Notes |
|---|---|---|
| `gemma4:26b` (Ollama) | `ollama-openai` | Most reliable. P1.3 + P2.3 in 2026-05-10 sweep — both `end_turn`, valid proposals (v_seq=84, 95) |
| `qwen/qwen3.6-35b-a3b` (LMS) | `lms-openai` | Loop drives cleanly, stop_reason=end_turn |
| `qwen/qwen3.6-35b-a3b` (LMS) | `lms-anthropic` | Works after `runner.py:_run_turn_no_stream` fix (commit `4202f1e`). cache_read=31491 (LMS auto-cache) |
| `qwen3.6:35b-a3b` (Ollama) | `ollama` (native /api/chat) | Smoke 2: 5 iters, end_turn, v_seq=107. Auto-`think:false` via OllamaChatClient |
| `qwen3-coder:30b` (Ollama) | `ollama-openai` | Prior session +14.9% lift (TODO-19), F6 issue still pending |

### Task agents (proven, post-2026-05-10 patch)

| Task model | Backend | Status |
|---|---|---|
| `ollama_chat/qwen3.6:35b-a3b` | Ollama | **Unblocked 2026-05-10** by sandbox `tau2_patches.py` think:false patch (commit `35cdfc5`). Verified `/api/chat` 7-13s latency in smoke4 before reboot killed it. **Val_score still pending** — needs rerun |
| `openai/qwen/qwen3.6-35b-a3b` | LMS | **Unblocked 2026-05-10** by froggeric `chat_template-v12.jinja` via LMS UI prompt-template override. Curl probe verified single + multi-turn tool flow. **Smoke ran 39/40 cleanly, avg reward 0.69 (N=39)**, but gated to SANDBOX_ERROR by 1 ctx-exceeded infra error. Real val_score still gated — fix is `lms load -c 65536` (deferred low-pri 2026-05-10). |

### Other

- `lms-anthropic` runner path: works for high `max_tokens` after the
  `_run_turn_no_stream` → silent-streaming fallback (commit `4202f1e`)
- `OWNEVO_TAU3_TASK_TIMEOUT=N` env knob in
  `tau3_p2_local_loop.sh` overrides the 2400s default; needed for
  slow local backends (4 hr / 14400s recommended)

---

## What's BROKEN (confirmed, don't re-run as-is)

### Loop drivers

| Loop | API path | Failure | Mitigation |
|---|---|---|---|
| `qwen3.6:35b-a3b` (Ollama) | `ollama-openai` | Verbose thinking → 16K out tokens in 2 iters → `DEFAULT_MAX_TOKENS_OPENAI=16384` cap | Use `ollama` native preset instead — auto-injects think:false |
| `google/gemma-4-26b-a4b` (LMS) | both | `stop_reason=max_tokens` at ~1k output tokens | Untriaged. Suspect LMS-side `max_completion_tokens` — try `lms load … --num-predict 16384` |
| `gemma4:26b` (Ollama) | `ollama` native | `httpx.ReadTimeout` at 5 min | **Fixed in commit `30a61a8`** — `DEFAULT_TIMEOUT_SECONDS` 300→600. Re-test pending |
| `qwen3.5-9b` | `openai` paths | F14g — needs `anthropic/` routing | Use `lms-anthropic` |
| `zai-org/glm-4.7-flash` (LMS) | `lms-openai` | Context overflow on kickoff at default LMS load | **Fixed by `lms load … -c 32768`** — same pattern as granite. Phase 3 sweep now loads with explicit ctx (commit-pending) |

### Task agents

| Task model | Failure |
|---|---|
| `openai/qwen/qwen3.6-35b-a3b` (LMS, ctx=32768) | Jinja fix works (v12 template). But 1/40 tasks hit `Context size exceeded` → gate=SANDBOX_ERROR. Real rewards 39/40 ≈ 0.69. Bump to ctx=65536 to surface val_score (deferred low-pri). |
| ~~`anthropic/qwen/qwen3.6-35b-a3b` (LMS)~~ | Same template fix expected to apply via `/v1/messages`. Not re-tested. Same ctx caveat. |
| `openai/granite-4.1-8b` (LMS) | LiteLLM `OpenAIException` with empty message in 40/40. First-turn response IS structurally valid (verified via direct curl). Suspect numeric tool_call id `"873012003"` (vs OpenAI `call_*`) or non-standard `reasoning_content` field tripping LiteLLM strict pydantic validation. Multi-turn flow not yet probed. |

### Models 🚫 already documented broken (in compat matrix)

`qwen2.5-coder:32b` (no tool calls), `granite4.1:8b` (em-dash SyntaxError),
`granite4.1:30b` (read-only), `devstral-small-2:latest` (run_pipeline rejects),
`mistralai/devstral-small-2-2512` (tool-error storm), `mistralai/ministral-3-14b-reasoning` (template alternation).
**Don't re-run.** See `docs/TAU3_LOCAL_TESTPLAN.md` § Local LLM compat matrix.

---

## What to RUN NEXT (priority order)

### Active priorities (2026-05-12 session, post JIT-fallback discovery)

**OLD A–I queue invalidated** by JIT-fallback finding (`anthropic/qwen/qwen3.5-4b` is an invalid identifier; prior task-agent results were JIT-fallback to qwen3.6-35b-a3b).

**New queue: 1 active + 4 task-agent tests + 7 proposer tests = 12 runs total, sequential.**

#### Currently running

| ID | Workflow | Status |
|---|---|---|
| #3 | `qwen36_all3_real_scaleup_5c` — 5-cycle scale-up of corrected Run 21 config | **IN PROGRESS** — cycle 1/5, 30/40 @ avg 0.80 (07:20Z start, ~6 min in at last check) |

Config: proposer `qwen/qwen3.6-35b-a3b` LMS (openai) + task/user `anthropic/qwen/qwen3.6-35b-a3b` LMS (/v1/messages). The real winner config — same model all 3 roles, but task/user goes through `/v1/messages` not `/v1/chat/completions`. Tests whether multi-cycle proposer refinement can push past ~0.82.

#### Task-agent queue (after scale-up) — hypothesis: weaker-but-instructable task agent makes the skill matter more

Run order = throughput-sorted. Each test = 1 cycle, qwen3.6-35b-a3b LMS as proposer.

| # | Task agent | Backend | tok/s | TTFT | Why |
|---|---|---|---|---|---|
| #7 | `openai/qwen/qwen3-coder-30b` | LMS | **221** | 186ms | Fastest + baseline 0.1250 all-3 = biggest headroom for skill-lift signal |
| #6 | `openai/openai/gpt-oss-20b` | LMS | 154 | 260ms | Light thinking (~8 tok); AgentOS +22.6% M1 |
| #5 | `openai/mistralai/devstral-small-2-2512` | LMS | 66-69 | 173ms | No thinking; agentic-coding specialist; Apache 2.0 |
| #4 | `anthropic/qwen/qwen3.5-9b` | LMS | 70 (post-21s) | **21s** | ⚠ heavy thinking (~2190 reasoning tok/task) — likely 60-90 min/cycle |

#### Proposer queue (after task-agent tests) — sorted by speed

Each test = 1 cycle, qwen3.6-35b-a3b LMS as task/user via anthropic routing.

| # | Proposer | Backend | tok/s | TTFT | Notes |
|---|---|---|---|---|---|
| #8  | `qwen/qwen3-30b-a3b-2507`       | LMS MoE      | **336** | 130ms | Never tested. 2507 = non-thinking variant. Same family as winner. |
| #9  | `google/gemma-4-26b-a4b`        | LMS MoE      | 200-267 | 4-5s  | **Pre-req: set num_predict ≥ 16384 in LMS UI** |
| #10 | `zai-org/glm-4.7-flash`         | LMS DeepSeek-2| 172    | 4.4s  | Pre-req: `lms load -c 32768` |
| #11 | `qwen3:30b-instruct`            | Ollama dense | 159     | 174ms | Non-thinking; think:false works on qwen3 base family |
| #12 | `qwen3:30b-a3b`                 | Ollama MoE   | 123     | 5.6s  | Thinking; lower priority than #11 |
| #13 | `google/gemma-4-31b`            | LMS dense    | 54-58   | 296ms | Retry from intermittent LMS HTTP 500 |
| #14 | `qwen/qwen3.6-27b`              | LMS dense    | ~3-5    | —     | Slow; deprioritized |

**Skipped permanently:**
- `unsloth/qwen3.6-35b-a3b`: cross-quant ≡ qwen/ quant
- `qwen3:32b` Ollama: prompt-format issue
- `qwen3:14b` / `qwen3:8b`: below useful floor
- gemma-4-e4b, granite-4.1-8b as task agents: gemma too weak on tool flow, granite throughput-bound at conc=3
- qwen2.5 family: previous generation, retail-weak

**JIT-fallback fix applied to all queued runs:** Wrapper now uses validated identifiers — `anthropic/qwen/qwen3.6-35b-a3b` (loaded) instead of `anthropic/qwen/qwen3.5-4b` (doesn't exist).

**Wrapper commands (updated for JIT-fallback fix — validated identifiers only):**

```bash
cd /media/fast_data/work2026/ownevo/ownevo_app

# ───────── TASK-AGENT TESTS (with qwen3.6-35b-a3b LMS proposer) ─────────

# #7 — qwen3-coder-30b LMS task agent (fastest, 221 tok/s)
lms load "qwen/qwen3-coder-30b" -c 32768   # ensure both proposer + task loaded
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "qwen/qwen3.6-35b-a3b" "lms-openai" "qwen36_loop_qwen3coder30b_task_smoke" "" \
  "openai/qwen/qwen3-coder-30b" "openai/qwen/qwen3-coder-30b" \
  > log/tau3_p2/qwen36_loop_qwen3coder30b_task_smoke_nohup.log 2>&1 &

# #6 — gpt-oss-20b LMS task agent (154 tok/s, light thinking)
lms load "openai/gpt-oss-20b" -c 32768
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "qwen/qwen3.6-35b-a3b" "lms-openai" "qwen36_loop_gptoss20b_task_smoke" "" \
  "openai/openai/gpt-oss-20b" "openai/openai/gpt-oss-20b" \
  > log/tau3_p2/qwen36_loop_gptoss20b_task_smoke_nohup.log 2>&1 &

# #5 — devstral-small-2 LMS task agent (66 tok/s, no thinking)
lms load "mistralai/devstral-small-2-2512" -c 32768
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "qwen/qwen3.6-35b-a3b" "lms-openai" "qwen36_loop_devstral2_task_smoke" "" \
  "openai/mistralai/devstral-small-2-2512" "openai/mistralai/devstral-small-2-2512" \
  > log/tau3_p2/qwen36_loop_devstral2_task_smoke_nohup.log 2>&1 &

# #4 — qwen3.5-9b LMS task agent (⚠ slow — 21s TTFT, 4hr timeout recommended)
lms load "qwen/qwen3.5-9b" -c 32768
OWNEVO_TAU3_CYCLES=1 OWNEVO_TAU3_TASK_TIMEOUT=14400 \
  nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
    "qwen/qwen3.6-35b-a3b" "lms-openai" "qwen36_loop_qwen35_9b_task_smoke" "" \
    "anthropic/qwen/qwen3.5-9b" "anthropic/qwen/qwen3.5-9b" \
    > log/tau3_p2/qwen36_loop_qwen35_9b_task_smoke_nohup.log 2>&1 &

# ───────── PROPOSER TESTS (with qwen3.6-35b-a3b LMS task/user) ─────────

# #8 — qwen3-30b-a3b-2507 proposer (fastest, 336 tok/s, non-thinking)
lms load "qwen/qwen3-30b-a3b-2507" -c 65536
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "qwen/qwen3-30b-a3b-2507" "lms-openai" "qwen3_30b_a3b_2507_proposer_smoke" "" \
  "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b" \
  > log/tau3_p2/qwen3_30b_a3b_2507_proposer_smoke_nohup.log 2>&1 &

# #9 — gemma-4-26b-a4b proposer (200+ tok/s; pre-req: num_predict ≥ 16384 in LMS UI)
lms load "google/gemma-4-26b-a4b" -c 32768
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "google/gemma-4-26b-a4b" "lms-openai" "gemma4_26b_a4b_proposer_smoke" "" \
  "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b" \
  > log/tau3_p2/gemma4_26b_a4b_proposer_smoke_nohup.log 2>&1 &

# #10 — glm-4.7-flash proposer (172 tok/s)
lms load "zai-org/glm-4.7-flash" -c 32768
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "zai-org/glm-4.7-flash" "lms-openai" "glm47flash_proposer_smoke" "" \
  "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b" \
  > log/tau3_p2/glm47flash_proposer_smoke_nohup.log 2>&1 &

# #11 — qwen3:30b-instruct (Ollama) proposer (159 tok/s, non-thinking)
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "qwen3:30b-instruct" "ollama" "qwen3_30b_instruct_ollama_proposer_smoke" "" \
  "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b" \
  > log/tau3_p2/qwen3_30b_instruct_ollama_proposer_smoke_nohup.log 2>&1 &

# #12 — qwen3:30b-a3b (Ollama) proposer (123 tok/s, MoE thinking)
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "qwen3:30b-a3b" "ollama" "qwen3_30b_a3b_ollama_proposer_smoke" "" \
  "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b" \
  > log/tau3_p2/qwen3_30b_a3b_ollama_proposer_smoke_nohup.log 2>&1 &

# #13 — gemma-4-31b dense proposer retry
lms load "google/gemma-4-31b" -c 65536
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "google/gemma-4-31b" "lms-openai" "gemma4_31b_proposer_retry" "" \
  "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b" \
  > log/tau3_p2/gemma4_31b_proposer_retry_nohup.log 2>&1 &

# #14 — qwen3.6-27b dense proposer (slowest, deprioritized)
lms load "qwen/qwen3.6-27b" -c 65536
OWNEVO_TAU3_CYCLES=1 nohup bash apps/kernel/scripts/tau3_p2_local_loop.sh \
  "qwen/qwen3.6-27b" "lms-openai" "qwen36_27b_lms_proposer_smoke" "" \
  "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b" \
  > log/tau3_p2/qwen36_27b_lms_proposer_smoke_nohup.log 2>&1 &
```

**Reminder:** LMS must have JIT and auto-unload **disabled** in settings for these to work (otherwise invalid identifiers silently fall back, masking results). Always load both proposer + task agent in LMS before launching.

### Completed val_score landings (this session)

| Workflow | Config | val_score | Notes |
|---|---|---|---|
| `qwen36lms_ctx65k_smoke` | qwen3.6-35b-a3b LMS all-3, ctx=65k, v12 template | **0.7500** | First end-to-end local win. ~27 min. Proposal v_seq=133. |
| `gemma4_e4b_full_local_64k` | gemma-4-e4b LMS all-3, ctx=65k | **0.1750** | Second win. ~39 min. Loop clean, retail-weak. Proposal v_seq=141. |

### Pre-launch checklist for next LMS run

```bash
# 1. Verify LMS bound to LAN
ss -tlnp 2>/dev/null | grep 1234   # expect 0.0.0.0:1234

# 2. Verify only target model is loaded (don't stack VRAM)
lms ps --json | python3 -c "import json,sys; [print(m['modelKey']) for m in json.load(sys.stdin)]"

# 3. Unload anything else before loading target
lms unload <other-model>
lms load <target> -c 65536

# 4. If load times out or returns "Terminated": daemon may be wedged
#    (see "LMS daemon wedge incident" above) — restart server cleanly.
lms server stop && lms server start --cors --bind 0.0.0.0
```

### Queued smokes (sequential — block on previous, all LMS, ctx=65k)

| Task | Wrapper invocation |
|---|---|
| ~~#23 gemma4-e4b all-3~~ | ✅ PASS val_score=0.1750 |
| ~~#24 granite-30b all-3~~ | ❌ SANDBOX_ERROR — codegen missing HarnessAgent class |
| ~~#25 unsloth/qwen3.6 cross-quant~~ | ❌ SANDBOX_ERROR — task 101 hit 4hr wall. avg reward 0.77 (N=39). Cross-quant ≈ qwen/ quant. |
| ~~#26 qwen3-coder-30b LMS all-3~~ | ✅ PASS val_score=0.1250 |
| ~~#27 mixed: qwen3.6 LMS + ollama_chat/qwen3.5:4B~~ | ❌ SANDBOX_ERROR — HTTP 415 (LiteLLM ollama_chat adapter bug). Track CLOSED. |
| ~~#28 mixed: qwen3.6 LMS + ollama_chat/qwen3.5:9B~~ | ❌ SANDBOX_ERROR — same 415, model-size independent. Track CLOSED. |
| ~~#29 mixed: qwen3.6 LMS + anthropic/qwen3.5-9b~~ | ❌ SANDBOX_ERROR — jinja "No user query found" (LMS template). Fix: apply froggeric v12 to qwen3.5-9b in LMS UI. |
| ~~#30 gemma-4-31b dense all-3~~ | ❌ SANDBOX_ERROR — LMS metadata 500 on tasks 55/56/60/61. avg reward 0.62 (N=36). ~2h32m. |
| ~~#31 / Run 23 v2 qwen3.6-27b Ollama loop + 4b LMS task~~ | ✅ **PASS val_score=0.6750** (Run 23 v2) — 40/40 clean, ~41.5 min. Dense 27b proposer weaker than MoE 35b-a3b (0.6750 < 0.8250). v_seq=169. |
| ~~#32 qwen3.5-9b LMS retry (v13 template)~~ | ❌ SANDBOX_ERROR (Run 20) — v_seq=161, proposal used `self.known_facts` (uninit attr) → 40/40 AttributeError. Template confirmed working; codegen bug in proposer. |
| ~~#33 qwen3.5-4b LMS new (v13 template)~~ | ✅ **PASS val_score=0.8250** (Run 21) — NEW RECORD. 40/40 clean, ~24 min. Inverse scaling: 4B > 35B. |
| ~~Run 22 qwen3.5-9b v3 (same topology as #32 but fresh proposer)~~ | ✅ **PASS val_score=0.7250** (Run 22) — 40/40 clean, ~24 min. 9B < 4B by −10pp; inverse scaling confirmed. |
| (option) qwen3.5 Ollama OpenAI-compat | Use `openai/qwen3.5:9b` (port 11434/v1) instead of `ollama_chat/`. Avoids 415 bug. Template may differ. |

All sequential — no parallelism (single 24GB VRAM limit + LMS instance).
**Current state: idle, queue blocked on #31.** After #31: open up #14 (remaining LMS loop drivers + Qwq:32b) and #13 (5-cycle scale-up on the 0.82 winner).

### Sequencing rule (loop body)

When a run finishes:
1. Read the gate decision from `log/tau3_p2/<workflow>_p2_cycle1.log` (look for `gate: decision=`).
2. Append a "Last finished/killed run" section to STATUS.md with the result.
3. Update the relevant compat-matrix row in `docs/TAU3_LOCAL_TESTPLAN.md`.
4. Mark the in_progress task completed (`TaskUpdate`).
5. Pick up the next unblocked task — unload previous model, load target, launch wrapper, mark new task in_progress, re-arm Monitor.

### Decoupled debt to RETRY with the fixes already landed

| Item | Fix | How to retry |
|---|---|---|
| `granite-4.1-8b` (LMS) as task agent | try `anthropic/granite-4.1-8b` routing | Set task=`anthropic/granite-4.1-8b` in any sweep step |
| `gemma-4-26b-a4b` (LMS) max_tokens cap | try `lms load google/gemma-4-26b-a4b -c 32768` and/or `--num-predict 16384` | Manual LMS reload before loop |
| Ollama-native `httpx.ReadTimeout` | `DEFAULT_TIMEOUT_SECONDS` bumped 300→600 (commit `30a61a8`) | Re-run gemma4:26b ollama native |
| `lmstudio-community/Qwen3.6-35B-A3B-GGUF` | Pre-fixed templates ship in this variant — would unlock LMS qwen36 as task agent | `lms get https://huggingface.co/lmstudio-community/Qwen3.6-35B-A3B-GGUF` (~22 GB download) |
| ~~`qwen/qwen3.6-35b-a3b` (LMS) jinja "No user query found"~~ | ~~LMS UI manual prompt-template override~~ — **DONE 2026-05-10**, froggeric `chat_template-v12.jinja` applied and verified end-to-end (39/40 retail tasks clean). |
| ~~**`qwen/qwen3.6-35b-a3b` (LMS) ctx ceiling at 32768**~~ | ~~`lms load -c 65536` then rerun~~ — **DONE 2026-05-11**, surfaced val_score = **0.7500** in `qwen36lms_ctx65k_smoke`. |
| **qwen3.6 verbose-thinking — alternative to `think:false`** | `preserve_thinking: true` keeps thinking ON but stable across turns (vs `think:false` suppressing it) — may give better proposer quality | Pass `extra_body={"preserve_thinking": true}` via OllamaChatClient or LiteLLM completion kwargs. NOT yet plumbed in our runner — would need a code change. Worth trying if `think:false` (current) doesn't lift well |
| **Qwen sampler tuning if loops hit max_tokens** | `presence_penalty=0.0`, `temperature=1.0`. Low temps (0.2-0.7) trap model in reasoning paths that never reach action tokens; `presence_penalty ≥ 1.2` causes instant looping | LMS UI per-model settings, or pass via LiteLLM completion kwargs. Try only if `think:false` path (smoke4) doesn't produce val_score |
| **System-prompt nudge to close `</think>`** | Cheap fix: append `"You MUST close your reasoning block with </think> before calling any tool."` to system prompt | Could be added to `apps/kernel/src/ownevo_kernel/middleware/claude_sdk/runner.py:_maybe_no_think_suffix` for qwen3.5/3.6 lineage. Currently that suffix injects `/no_think` (which doesn't work on these models). |

### Phase 3 broader LMS sweep (opt-in, after task #26)

```bash
OWNEVO_TAU3_PHASES=1,2,3 nohup bash apps/kernel/scripts/tau3_p2_local_sweep.sh \
    > log/tau3_p2/sweep_p3_nohup.log 2>&1 &
```

(Already in `tau3_p2_local_sweep.sh:phase3_full_lms_sweep` — runs all P3 LMS candidates in sequence with cloud Sonnet as evaluator.)

---

## Provider preset reference

For both `tau3_p2_local_loop.sh` (single config) and `tau3_p2_local_sweep.sh` (phased):

| Preset (`$2`) | Loop endpoint | api_format | Use when |
|---|---|---|---|
| `ollama` | `http://$LLM_HOST:11434` | `ollama` | Ollama native `/api/chat`. **Auto-injects think:false for qwen3** via `OllamaChatClient`. |
| `ollama-openai` | `http://$LLM_HOST:11434/v1` | `openai` | Ollama OpenAI-compat. Does NOT inject think:false — qwen3 thinking models will fail. |
| `lms-openai` | `http://$LLM_HOST:1234/v1` | `openai` | LM Studio OpenAI-compat |
| `lms-anthropic` | `http://$LLM_HOST:1234` | `anthropic` | LM Studio Anthropic-compat. Auto-passes `--no-stream` (matters: see commit `4202f1e`'s no-stream fix). |

**Fixed user model for lift cycles:**
When running proposer lift cycles (same proposer, varying task model), pin `--user-model` to a single small fixed model so task-model scores are comparable. Options:
- `openai/gemma3:4b` LMS (lms-openai) — non-thinking, fast, good conversation, ~3 GB VRAM
- `openai/qwen3.5:4b` LMS (lms-openai) — thinking suppressed via API patch, ~3 GB VRAM
- `openai/llama3.2:3b` LMS (lms-openai) — smallest, ~2 GB VRAM
**Prefer LMS over Ollama for the user model** — LMS handles c=4 concurrent requests better and uses less VRAM than a separate Ollama process. Load the small model in LMS alongside the task model (or in a separate LMS port if needed). Don't mix user-model backends mid-campaign.
Baseline runs continue to use same model for both agent and user — fixed user model only applies to lift cycles.

**Task / user model prefix conventions:**
- `openai/<model>` → routes via OPENAI_API_BASE (auto-set by wrapper)
- `ollama_chat/<model>` → routes via OLLAMA_API_BASE (auto-set by wrapper)
- `anthropic/<model>` → ANTHROPIC_API_BASE pinned to LMS root `http://$LLM_HOST:1234` regardless of loop preset

---

## Schema fixes applied this machine (still uncommitted to migrations)

If you `docker compose down -v` and bring up fresh, you'll re-need:
```sql
ALTER TABLE iterations ADD COLUMN IF NOT EXISTS deployment_id uuid;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS eval_score numeric(10,6) CHECK (eval_score >= 0 AND eval_score <= 1);
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS eval_rationale text;
ALTER TYPE proposal_state ADD VALUE IF NOT EXISTS 'gate-passed';
```

(The `BUG: tau3 shell scripts loaded API key wrong` was fixed in earlier
commits — the `grep -E '^(export )?OWNEVO_LLM_API_KEY='` fallback is in
all three `tau3_p2_local_*.sh` scripts.)

---

## Commits landed this session (ahead of `dc77adc`)

| SHA | Subject |
|---|---|
| `4202f1e` | runner.py no-stream fallback + ANTHROPIC_API_BASE pin + granite ctx + queued-rerun script |
| `30a61a8` | run_tau3_loop pipeline_error visibility + task-timeout env knob + Ollama 600s timeout |
| `8006e71` | docs: compat matrix touch-up — qwen3.6-Ollama timeout note, drop 120b |
| `35cdfc5` | sandbox: inject think:false for qwen3 ollama_chat task agents |
| `b36bc86` | sweep: explicit ctx on every lms_load in phase3 (glm-4.7-flash etc.) |
| `b075652` | docs(tau3): qwen3.5/3.6 thinking-loop levers added to compat matrix |
| (next)    | docs(tau3): LMS qwen36 v12 template smoke result — 39/40 clean, ctx=32768 blocker |

```bash
git log --oneline dc77adc..HEAD
git diff --stat dc77adc..HEAD
```

---

## Known issues to triage later (background)

- **Pre-existing 2026-05-09 / earlier:** gemma4:26b cycle 2-5 codegen bugs
  (NameError, slice-truncation, etc.) — separate from API/template issues
  that this session focused on. Loop drives but proposal correctness is
  uneven. May need eval-side resilience patches similar to
  `_patch_nl_evaluator_resilience` and `_patch_tool_call_args_resilience`.
- **F6 / 30-day replay:** `qwen3-coder:30b` regressed from +14.9% to F6
  7/7 in W6 v5. Pending root-cause investigation. See
  `docs/W6_30DAY_REPLAY_NOTES.md`.

---

## File pointers

- Sweep scripts: `apps/kernel/scripts/tau3_p2_local_loop.sh`,
  `apps/kernel/scripts/tau3_p2_local_sweep.sh`
- Compat matrix: `docs/TAU3_LOCAL_TESTPLAN.md` § Local LLM compat matrix (line ~960)
- Runner code: `apps/kernel/src/ownevo_kernel/middleware/claude_sdk/runner.py`
- Native Ollama client: `apps/kernel/src/ownevo_kernel/eval_runner/ollama_native.py`
- Sandbox image build: `apps/kernel/sandbox/Dockerfile.tau3`,
  `apps/kernel/sandbox/tau2_patches.py` (sitecustomize.py inside container)
- Per-run logs: `log/tau3_p2/<workflow_tag>_p2_*.log`
