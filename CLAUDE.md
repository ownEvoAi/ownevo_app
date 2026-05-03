# Notes for future Claude sessions

## What this repo is

The production implementation of ownEvo per `../ownevo_docs/ownEvo_MVP.md`. That doc is the source of truth for scope, stack, and sequencing — read it before making non-trivial changes here.

## Stack split (locked)

- **Python** — `apps/kernel/`. Agent runtime, eval harness (Inspect AI), failure clustering (sentence-transformers + UMAP + HDBSCAN), regression gate, background jobs.
- **TS / Next.js** — `apps/web/`. Approval UX, side-by-side diff, lift chart, audit trail.
- **Seam** — REST + SSE from kernel to web. Don't blur the boundary.

Why not pure TS: clustering ecosystem is Python-first at the quality bar required.
Why not pure Python: web UI is unavoidably TS/Next.

## Multi-tenant from day one

Every domain table gets `workspace_id`. RLS at the DB level. Audit log on every state change. Painful to retrofit — don't.

## Trace format is the contract

`packages/trace-format/` defines the typed `AgentEvent` schema. It's the seam between any customer agent and the improvement loop. Same role as OTel for distributed tracing — standardize once, everything downstream works. Target: OSS spec (Apache 2).

## Where the IP lives

Build (no OSS substitute): natural-language sim/eval/metric generator, failure clustering pipeline, eval-case generation, regression gating, approval UX, skill registry with retention contracts, knowledge ingestion.

Use (don't build): Langfuse, ClickHouse, OTel collector, LiteLLM, Inspect AI.

## Reference patterns

- `startup2026/core/src/agentos_harness/evolution/` — tracker → reflector → curator → proposer with 377 passing tests. Lift candidate for the improvement loop.
- `startup2026/mvp5-playground/` — schema reference for tracing + approval models (will diverge once multi-tenant requirements firm up).
- `startup2026/core/src/agentos_harness/store/` — SQLite + sqlite-vec memory store. Defer lifting until trace + clustering pipelines exist.

## Out of scope for MVP (don't build unless asked)

Multiple framework integrations beyond Claude Agent SDK, self-evolving harness, custom Rust gateway, knowledge ingestion connectors, mobile UI, skills marketplace. See `ownEvo_MVP.md` § Out of Scope for the full list.
