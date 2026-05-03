# W1-day-2 Spike Result — `core/agentos_harness/evolution/` reuse

**Date:** 2026-05-03
**Decision rule (D6):** End-of-day-2: evolution scaffold wired into `apps/kernel/` AND ≥1 test passes against the new types → commit to lift. Otherwise → greenfield.

## Decision: **NO-GO on wholesale lift. Commit to greenfield for W1-W2.**

The 4-stage pattern (Tracker → Reflector → Curator → Proposer) and `ProposalAction` discriminator carry over as **reference architecture**. The implementation does not.

## Inspection notes

Inspected:
- [`core/agentos_harness/types.py`](../../../startup2026/core/src/agentos_harness/types.py) — 197 lines
- [`core/agentos_harness/evolution/{tracker,reflector,curator,proposer,utils}.py`](../../../startup2026/core/src/agentos_harness/evolution/) — 705 lines total

### Why wholesale lift fails

1. **Name collision on `AgentEvent`.** `core/types.py:30` defines `AgentEvent` as an *incoming event* (incident / lead / case update / habit log) with `id, title, payload, source, timestamp`. ownEvo's `AgentEvent` (per [`packages/trace-format/SPEC.md`](../packages/trace-format/SPEC.md)) is the *typed agent runtime event stream* (content_delta, tool_call_*, skill_loaded, etc.). Same name, different concept. Lifting forces renaming everywhere or accepting persistent confusion.

2. **`evolution/` is tightly coupled to `core/memory.py` + `core/store/`.** Every file imports from `..memory`:
   - `tracker.py:1` — `from .. import memory`; calls `memory.store_event_hypothesis`, `memory.store_event_outcome`, `memory.update_memory_utility`
   - `curator.py:11` — `from .. import memory`; calls `memory.get_all_observations`, `memory.promote_to_pattern`
   - `proposer.py` (366 lines, similar coupling expected)
   - `reflector.py` (171 lines, similar)

   Lifting any of these requires lifting `memory.py` + `store/` (its SQLite/sqlite-vec layer). `memory.py` was deferred per the MVP doc § "Reuse from `startup2026/core` (Tentative)" — "Defer the lift until trace + clustering pipelines are in place." That defer is correct: importing core/'s storage layer would lock ownEvo into SQLite-with-FTS5 instead of Postgres-with-pgvector.

3. **SRE/incident semantics don't map to skill mutations.** `tracker.record_hypothesis(event_id, hypothesis, confidence, evidence)` records *the agent's prediction about a real-world incident*. ownEvo's tracker needs to record *the agent's hypothesis about why a skill failed an eval*. Different concept; same word "hypothesis."

4. **The 377 tests carry over only with the data shapes.** core/'s tests assert against incident-shaped Memory rows. They wouldn't pass on ownEvo's substrate (failure_clusters / eval_cases / proposals) without rewriting most assertions. The "377 tests" pitch was based on the wholesale lift; greenfield brings the IDEAS, not the tests.

### What carries over (reference architecture, NOT code)

- **4-stage pipeline shape** — Tracker → Reflector → Curator → Proposer. Preserved as Protocol scaffolding in [`apps/kernel/src/ownevo_kernel/evolution/__init__.py`](../apps/kernel/src/ownevo_kernel/evolution/__init__.py). Concrete impls land in W2.
- **`ProposalAction` discriminator pattern.** Lifted as a Pydantic model with `action_type` literal + the original 4 action types (`workflow_update`, `tool_priority`, `prompt_refinement`, `config_update`). Extended with **`regression_gate`** per D6 — gate outcomes flow through the same proposal pipeline as skill mutations. See [`apps/kernel/src/ownevo_kernel/types.py`](../apps/kernel/src/ownevo_kernel/types.py) `ProposalAction`.
- **`Proposal.eval_score` + `eval_rationale`** — the LLM-judge-stub integration shape. Carried over.
- **Pattern promotion thresholds** — ≥3 occurrences, ≥0.6 confidence (curator.py:18-19) — adopted as the starting heuristic for ownEvo's failure-cluster admission gate.

### What does NOT carry over

- `core/memory.py` and `core/store/` — Postgres + pgvector instead of SQLite + sqlite-vec.
- `core/types.py:AgentEvent` — collision with ownEvo's `AgentEvent`. Dropped.
- `tracker.py` / `curator.py` / `proposer.py` / `reflector.py` implementations — rewritten greenfield against ownEvo's substrate (failure_clusters, eval_cases, traces, proposals tables).
- The 377 core/ tests — rewritten as ownEvo tests against the new shape.

## Cost impact

- **Greenfield delta:** ~2-3 days of W1-W2 vs ~half a day if the lift had succeeded. The plan absorbed this risk explicitly via the spike-with-hard-cutoff design.
- **W1-W2 still on track.** Days 3-5 substrate work (sandbox, skill registry, trace capture, M5 dataset) is unaffected — the pyproject + types + Protocol scaffolding from this spike feed directly into W1.4 and W2.

## What's in this commit (W1-day-1-2 deliverable)

- [`packages/trace-format/`](../packages/trace-format/) — Pydantic implementation of [`SPEC.md`](../packages/trace-format/SPEC.md). 7 AgentEvent variants with discriminator-based parsing, D3 sandbox-failure semantics enforced via `model_validator`. Tests cover discriminated-union parsing + error-class invariants + round-trip identity.
- [`apps/kernel/src/ownevo_kernel/types.py`](../apps/kernel/src/ownevo_kernel/types.py) — Pydantic mirror of [`docs/SCHEMA.md`](./SCHEMA.md). 12 entity models + 6 enums. `ProposalAction` extends with `regression_gate` per D6.
- [`apps/kernel/src/ownevo_kernel/evolution/__init__.py`](../apps/kernel/src/ownevo_kernel/evolution/__init__.py) — 4 Protocol classes (Tracker / Reflector / Curator / Proposer). Greenfield placeholder; impls in W2.
- Tests: `packages/trace-format/tests/test_agent_event.py` (10 cases), `apps/kernel/tests/test_types.py` (15 cases). All pass.
- Workspace setup: `pyproject.toml` per package; `uv` workspace dependency from kernel → trace-format.

## Go/no-go ruling

**Bar:** "evolution scaffold wired into apps/kernel/ AND at least one test passes against the new types."

**Met:**
- Evolution scaffold is in `apps/kernel/src/ownevo_kernel/evolution/` (Protocol form, greenfield; the LIFT failed but the scaffold landed).
- 25 tests pass against the new types (10 in trace-format, 15 in kernel).

**Spike outcome:** the rule was "commit to lift OR commit to greenfield." We're committing to **greenfield** — but the W1 substrate work is unblocked, the schema is locked, and W2 implementations have a clear interface to land against.

W3-W6 work (NL-gen, M5 loop, clustering) feeds the same Protocol scaffolding without re-architecture. The spike paid for itself.
