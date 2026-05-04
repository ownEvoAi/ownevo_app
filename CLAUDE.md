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

The improvement loop (`scripts/run_improvement_loop.py`) supports two API formats via `--api-format`:

- `anthropic` (default) — `AsyncAnthropic` + `/v1/messages`. Works with LM Studio and any LiteLLM proxy. Add `--no-stream` when proxying Ollama through LiteLLM to bypass the streaming tool-call translation bug.
- `openai` — `AsyncOpenAI` + `/v1/chat/completions`. Talks directly to Ollama (or vLLM). Default base URL: `http://$OWNEVO_LLM_HOST:11434/v1`.

**Confirmed working model (2026-05-04):** `devstral-small-2:latest` on Ollama. Calls tools reliably, generates clean Python, gate-pass confirmed.

```bash
uv run --directory apps/kernel --extra agent python scripts/run_improvement_loop.py \
  --api-format openai \
  --llm-model devstral-small-2:latest \
  --no-seed
```

Models tested and **not** working well:
- `granite4.1:8b` — calls tools but generates em-dashes (U+2013) in Python → SyntaxError
- `qwen3-coder:30b` — thinking mode causes it to skip tool calls on first turn
- `qwen2.5-coder:32b` — doesn't trigger tool calls with `tool_choice=auto`

## Out of scope for MVP (don't build unless asked)

Multiple framework integrations beyond Claude Agent SDK, self-evolving harness, custom Rust gateway, knowledge ingestion connectors, mobile UI, skills marketplace. See `ownEvo_MVP.md` § Out of Scope for the full list.
