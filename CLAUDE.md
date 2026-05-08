# Notes for future Claude sessions

## What this repo is

The production implementation of ownEvo per `../ownevo_docs/ownEvo_MVP.md`. That doc is the source of truth for scope, stack, and sequencing — read it before making non-trivial changes here.

## Stack split (locked)

- **Python** — `apps/kernel/`. Agent runtime, eval harness (Inspect AI), failure clustering (sentence-transformers + UMAP + HDBSCAN), regression gate, background jobs.
- **TS / Next.js** — `apps/web/`. Approval UX, side-by-side diff, lift chart, audit trail.
- **Seam** — REST + SSE from kernel to web. Don't blur the boundary.

Why not pure TS: clustering ecosystem is Python-first at the quality bar required.
Why not pure Python: web UI is unavoidably TS/Next.

## Single-tenant for MVP, multi-tenant retrofit before next deployment (D4)

Per the 2026-05-03 design review (D4), the MVP runs on **one workspace** — no `workspace_id` columns, no RLS policies, no workspace-scoped query helpers. Multi-tenant retrofit is a bounded 1-2 week job in the breathing room between the investor program and the next deployment. Schema design should stay "retrofit-friendly" (no patterns that fight a future `workspace_id` column being added) but does not pre-build the isolation.

## Append-only audit log, customer-controlled export (D2)

`audit_entries` is append-only at the DB level: `REVOKE UPDATE, DELETE` from the app role; only `INSERT` permitted. Exportable in canonical JSON (sorted keys, no whitespace). **Crypto-grade tamper-evidence** (canonical content hash + parent hash + chain rotation; Merkle + signed root for the strongest claim) is a Phase-2 retrofit when first regulated-industry buyer requires it. The marketing claim is "append-only audit log, customer-controlled export" — not "tamper-evident hash chain."

## Sandbox: local Docker for MVP (D3)

Agent-generated code runs in **local Docker** with hardening: `--network=none`, `--read-only` rootfs + tmpfs `/tmp`, `--cap-drop=ALL`, mem/cpu/pids limits, hard timeout, structured stdout/stderr capture, explicit failure semantics (`tool_call_result {status: "error", error_class: "Timeout"|"OOM"|"Crash"}`). The `SandboxRuntime` interface stays preserved so a Phase-2 swap to e2b or Modal is bounded. Pyodide eliminated (can't run LightGBM).

## Trace format is the contract

`packages/trace-format/` defines the typed `AgentEvent` schema. It's the seam between any customer agent and the improvement loop. Same role as OTel for distributed tracing — standardize once, everything downstream works.

**Spec status (2026-05-03):** canonical spec written at `packages/trace-format/SPEC.md`. Pydantic + Zod implementations conform to it. The MVP doc § Open-Core Line names Apache 2 as the working assumption for if/when public release happens, but **license, public-release timing, and package naming are deferred** — not blocking W1 implementation. OTel Gen AI alignment is design-with-awareness only (no formal cross-walk). See `packages/trace-format/README.md` for trigger conditions to revisit, and `TODOS.md` TODO-4 for the unresolved strategic surface.

## Where the IP lives

Build (no OSS substitute): natural-language sim/eval/metric generator, failure clustering pipeline, eval-case generation, regression gating, approval UX, skill registry with retention contracts, knowledge ingestion.

Use (don't build): Langfuse, ClickHouse, OTel collector, LiteLLM, Inspect AI.

## Reference patterns

- `startup2026/core/src/agentos_harness/evolution/` — tracker → reflector → curator → proposer with 377 passing tests. Lift candidate for the improvement loop.
- `startup2026/mvp5-playground/` — schema reference for tracing + approval models (will diverge once multi-tenant requirements firm up).
- `startup2026/core/src/agentos_harness/store/` — SQLite + sqlite-vec memory store. Defer lifting until trace + clustering pipelines exist.

## Local LLM backend (dev / dogfooding)

Two distinct tracks; pick the one matching your task before reaching for a model name.

### Multi-turn improvement loop (`scripts/run_improvement_loop.py`)

Code-generating loop on real M5. Supports two API formats via `--api-format`:

- `anthropic` (default) — `AsyncAnthropic` + `/v1/messages`. Works with LM Studio and any LiteLLM proxy. Add `--no-stream` when proxying Ollama through LiteLLM to bypass the streaming tool-call translation bug.
- `openai` — `AsyncOpenAI` + `/v1/chat/completions`. Talks directly to Ollama (or vLLM). Default base URL: `http://$OWNEVO_LLM_HOST:11434/v1`.

**Multi-turn loop: two confirmed lift drivers as of 2026-05-08.** Sonnet 4.6 on Anthropic cloud (B4.2 + B4.3 + Stage C compound lift, ~$1.86 per 7-iter replay) and `qwen3-coder:30b` on Ollama OpenAI + `/no_think` fix (free, ~12 min, +14.9% on TODO-19 Stage D) are both confirmed. `qwen3-coder-30b` on LMS-Anthropic drives the loop but writes a deterministic `_long_frame` bug 14/14 attempts (F6, TODO-20 retest). `devstral-small-2:latest` on Ollama drives the loop and clears the 1 GB sandbox memory limit but its codegen quality fails `run_pipeline` validation every round (TODO-21 closed).

```bash
# Sonnet 4.6 — confirmed lift, ~$0.30 / iter
uv run --directory apps/kernel --extra agent python scripts/run_improvement_loop.py \
  --api-format anthropic \
  --llm-model claude-sonnet-4-6 \
  --no-seed

# qwen3-coder:30b — confirmed lift, free, ~12 min (requires Ollama running)
uv run --directory apps/kernel --extra agent python scripts/run_improvement_loop.py \
  --api-format openai \
  --llm-model qwen3-coder:30b \
  --no-seed
```

Local-model attempts on the multi-turn loop and where they fail:
- `qwen3-coder:30b` (Ollama OpenAI) — **validated lift driver (+14.9%, TODO-19 closed 2026-05-08)**. Requires `/no_think` auto-injection in `run_agent_turn_openai` (added 2026-05-07). Without it the model emits text and 0 tool calls on the M5 kickoff.
- `qwen3-coder-30b` (LMS Anthropic) — drives the loop but hits F6 deterministically.
- `devstral-small-2:latest` (Ollama) — drives the loop, runnable Python, but `run_pipeline` validation rejects every diff.
- `granite4.1:8b` — calls tools but generates em-dashes (U+2013) in Python → SyntaxError.
- `qwen2.5-coder:32b` — doesn't trigger tool calls with `tool_choice=auto`.

### A4.4 single-turn classification gate (`scripts/nl_gen_smoketest.py --from-fixtures`)

Forced-tool-use `predict_label(value: bool)` per case; orthogonal track from the multi-turn loop. **19+ models pass 3/3** across desktop LMS / laptop LMS / desktop Ollama as of the 2026-05-06/07 broader sweep. Source of truth: `docs/local-model-testing.md` § F14a-k (and `apps/kernel/README.md` for the top-pick table). Highlights:

- Fastest desktop 3/3: `granite-4.1-8b` (33 s, LMS). On laptop Apple Metal it sits on the credit-risk gate boundary — F14j flagged a ~0.17 drift, F14k re-test (4 laptop trials: 0.33 / 0.25 / 0.50 / 0.50 vs 0.40 gate) treats it as boundary noise, not systematic kernel drift. For stable laptop iteration use `qwen/qwen3-4b-2507`.
- Fastest desktop Ollama 3/3: `qwen3-coder:30b` (82 s) — **only with `/no_think` auto-injection** (both `agent_solver.py` for the A4.4 gate and `middleware/claude_sdk/runner.py:run_agent_turn_openai` for the BL.3 multi-turn loop auto-append the directive when model id contains `qwen3`; F14i unlocked 5 desktop Ollama 3/3 passers including this one).
- API-format-load-bearing: `qwen/qwen3.5-9b` is 0/3 via OpenAI but 3/3 via Anthropic `/v1/messages` (F14g).
- qwen3.5 / qwen3.6 lineage embeds thinking deeper than the directive can override; not unlocked by `/no_think`. qwen3-base + qwen3-coder ARE unlocked.

## Out of scope for MVP (don't build unless asked)

Multiple framework integrations beyond Claude Agent SDK, self-evolving harness, custom Rust gateway, knowledge ingestion connectors, mobile UI, skills marketplace. See `ownEvo_MVP.md` § Out of Scope for the full list.
