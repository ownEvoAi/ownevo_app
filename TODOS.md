# TODOs

Deferred work captured by the 2026-05-03 CEO review and eng review. Each item has
context sufficient that someone picking it up in 3 months understands the
motivation, the current state, and where to start.

Format follows the standard:
- **What:** one-line description
- **Why:** the concrete problem it solves
- **Pros / Cons:** trade
- **Context:** background
- **Effort:** S / M / L / XL (human team) → human-team estimate; with CC + gstack, compress 5-10x.
- **Priority:** P1 / P2 / P3
- **Depends on:** prerequisites

---

## Phase-2 retrofit checklist (load-bearing)

### TODO-1: Multi-tenant retrofit (D4)

- **What:** Add `workspace_id` to every domain table; enable RLS; wrap every kernel session with `SET LOCAL app.workspace_id`.
- **Why:** D4 ratified single-tenant for MVP. Customer #2 onboarding needs workspace isolation.
- **Pros:** Bounded migration (we designed for it); unblocks every multi-tenant customer.
- **Cons:** ~1-2 weeks of focused work; touches every read query in the kernel and every API endpoint.
- **Context:** [`docs/SCHEMA.md`](docs/SCHEMA.md) § "Phase-2 retrofit (D4)" has the migration sketch. The schema was deliberately designed retrofit-friendly — no composite PKs that would need widening.
- **Effort:** L (human ~1-2 weeks / CC ~2-3 days).
- **Priority:** P1 — blocks customer #2.
- **Depends on:** customer #1 successfully shipped + design-partner-onboarding doc validated.

### TODO-2: Sandbox provider migration (D3)

- **What:** Add `E2BSandbox` (or `ModalSandbox`) implementation behind the `SandboxRuntime` interface; switch by env var.
- **Why:** D3 chose local Docker for MVP. Local Docker hits resource ceiling around 4-6 concurrent customers OR first managed customer.
- **Pros:** No code change beyond the new impl + a config flag; existing `LocalDockerSandbox` stays as the dev/CI runner.
- **Cons:** ~3-5 days of integration + retesting hardening guarantees in the new env.
- **Context:** `apps/kernel/src/ownevo_kernel/sandbox/` has the interface. Drop a sibling file. The hardening checklist (network=none, mem/cpu/pids limits, structured stderr) maps to e2b's `Sandbox` config and Modal's `Image` config.
- **Effort:** M (human ~3-5 days / CC ~half day).
- **Priority:** P2 — triggers when local Docker can't keep up.
- **Depends on:** observability showing sandbox throughput is the bottleneck.

### TODO-3: Audit chain crypto upgrade (D2)

- **What:** Add canonical-JSON content hash + parent hash to `audit_entries`; build a chain rotation procedure that migrations use; optionally Merkle tree + signed root + transparency log.
- **Why:** D2 reframed sovereignty pitch from "tamper-evident" to "append-only." A regulated-industry buyer (healthcare, finance, defense) will ask for crypto-grade.
- **Pros:** Closes the "is this just append-only or is it tamper-evident?" objection. Schema is ready (just add `prev_hash`, `entry_hash` columns).
- **Cons:** ~1-2 weeks if Merkle tree + transparency log; ~3-5 days if just hash chain.
- **Context:** [`docs/SCHEMA.md`](docs/SCHEMA.md) § `audit_entries`. Spec the migration as "stop the log, snapshot the canonical-JSON of every existing entry, compute hashes, restart with hashes from the snapshot."
- **Effort:** M-L (human ~1-2 weeks / CC ~1-2 days).
- **Priority:** P2 — triggers when first regulated buyer evaluates.
- **Depends on:** specific regulatory requirement (EU AI Act, HIPAA, SOC 2 Type II audit, etc.).

### TODO-4: `AgentEvent` schema license / public-release / naming

- **What:** Lock the license (Apache 2 working assumption per MVP doc § Open-Core Line, but no formal commitment in code yet), publication path (npm + PyPI? separate repo? quiet drop or public announcement?), and package naming (currently internal-only Python `ownevo_format`, no npm/PyPI name reserved).
- **Status (2026-05-03):** Spec written at [`packages/trace-format/SPEC.md`](packages/trace-format/SPEC.md). Pydantic + Zod implementations land in W1 against the spec, internal-use-only within `ownevo_app/`. The W3 schema-freeze deliverable bumps the spec to 1.0 internally. None of these decisions block W1 implementation.
- **Why deferred:** The "decide before W3" framing was unnecessarily aggressive. The spec exists; the team can build against it. License + publication + naming are externally-facing concerns that don't gate internal implementation. Premature commitment carries small but real costs (e.g., picking an npm scope before there's a customer to install it).
- **Trigger conditions to revisit:**
  - A customer asks "what license is this under?"
  - A second team or repo needs to depend on this package
  - An OTel Gen AI working group asks to align (or vice versa)
  - Strategic decision to publish (post-MVP, with first design partners)
- **Sub-decisions when triggered:**
  - License: Apache 2 (MVP doc default), or stay proprietary, or BSL/AGPL middle-ground (note: AGPL doesn't apply to schemas; BSL hurts standardization)
  - Where it lives: stay in monorepo, or extract to `ownEvoAi/agent-event-spec` standalone repo
  - Publication: quiet drop to npm + PyPI, or coordinated announcement (YC demo, OSS post, design-partner-only)
  - Scope: just the JSON Schema + Pydantic + Zod, or also reference middleware (Claude Agent SDK adapter)
  - Naming: npm scope (`@ownevoai/...`?), PyPI name (`ownevo-agent-event`?)
  - OTel Gen AI alignment: design-with-awareness (current state, no cross-walk doc) vs formal cross-walk doc as a Phase-2 task
- **Effort to lock when triggered:** ~30-45 min if Apache-2-in-monorepo (current default); 1-2 days if extract + publication pipeline; weeks if retrofitting to Apache 2 after public release as proprietary.
- **Priority:** **P1 — strategic, not blocking.** No longer P0 since the spec is written and W1 is unblocked.
- **Depends on:** founder/board discussion of OSS strategy, OR first external trigger above.

---

## Substrate quality (build-now items, captured here for tracking)

These were added to the W1-W3 plan by the 2026-05-03 eng review. Listed here as
backup tracking in case PLAN.md edits drift.

### TODO-5: Cluster-label LLM eval (W3, Track B)

- **What:** Hand-label 20 M5 clusters with ground-truth names. Add nightly judge-vs-human eval at `apps/kernel/eval_runner/cluster_label_eval/`. Target agreement ≥0.7.
- **Why:** D4 (eng-review) — the demo storyboard at 0:25-0:38 shows a cluster card with an LLM-generated label. Hallucinated labels are a credibility hit; no eval = no detection.
- **Effort:** S (human ~1 day / CC ~2 hours).
- **Priority:** P1 — surfaces in YC demo.
- **Depends on:** clustering pipeline operational (W3 Track B).

### TODO-6: LLM-judge stub eval expansion (W5)

- **What:** Expand W5.2's "5 hand-crafted proposals" to ~30 hand-labeled (proposal, explanation) pairs with structural-element ground truth. Run nightly.
- **Why:** Eng review surfaced that 5 cases is a smoke test, not an eval. The stub admits proposals during M5 unattended replay; a false-positive admit drifts the lift chart in the wrong direction.
- **Effort:** S (human ~1 day / CC ~2 hours).
- **Priority:** P1 — required for unattended M5 replay.
- **Depends on:** LLM-judge stub operational (W5.2).

### TODO-7: Reproducibility CI cache strategy (W3)

- **What:** Document and implement: (a) cached LLM responses replayed from a fixture file, (b) pre-built sandbox Docker image cached, (c) M5 data pre-loaded into a Postgres volume, (d) cached LightGBM training artifacts keyed by skill-version-hash.
- **Why:** Replaying 30 days in <30 min requires all four cache layers. Without them, CI hits live APIs and misses the budget by 10x.
- **Effort:** M (human ~2-3 days / CC ~half day).
- **Priority:** P1 — blocks reproducibility CI being green.
- **Depends on:** M5 pipeline operational (W2).

### TODO-8: Parallel τ³/M5 conditions strategy (W6/W8)

- **What:** Run the 4 M5 conditions (frozen / static-LLM / loop-autonomous / loop-gated) in parallel on separate Docker compose stacks (each with its own Postgres + sandbox); merge results in `iterations` table at the end. Same pattern for τ³ A/B/C.
- **Why:** Sequential 30-day replay = ~150 hours wall time. 4-way parallel ≈ 37 hours. Without parallel strategy, W6 budget is too tight.
- **Effort:** M (human ~2-3 days / CC ~half day).
- **Priority:** P1 — required for W6/W8 timelines.
- **Depends on:** M5 pipeline + reproducibility rig operational (W4).

### TODO-17: Sandbox classifier hardening — runner exit-code spoof — ✅ DONE 2026-05-03

- **What:** A hostile (or buggy) agent inside `LocalDockerSandbox` can call `os._exit(0)` directly, bypassing the runner's `try/except` around `runpy.run_path`. The classifier sees `exit_code == 0` and returns `status="ok"` — or `os._exit(100)` to spoof the "logical error the agent owns" path with `error_class=None`. Per `apps/kernel/src/ownevo_kernel/types.py:SandboxErrorClass`, the gate runner advances `best_ever_score` only when `error_class is None`, so this is part of the trust boundary.
- **Status (2026-05-03):** Approach 1 shipped. Runner now runs user code as a subprocess (`subprocess.run([sys.executable, '/sandbox/user_code.py'])`); the runner's own exit code is derived from the child's returncode via a fixed policy (0 → 0; 1 → 100; 100 → 102=Crash; negative → 128+|N|; else passthrough). Closes the `os._exit(100)` spoof and the same-process attack surface. The `os._exit(0)` case remains observably indistinguishable from clean exit at the process boundary; defense-in-depth lives at the metric layer (`run_pipeline`'s JSON-output requirement). Pinned by 3 new tests in `apps/kernel/tests/test_sandbox.py`. Documented limit captured in the runner script's policy comment.
- **Effort:** S (CC ~30 min, as predicted).
- **Priority:** P1 — fix before W4 unattended M5 replay. Not blocking W2/W3.
- **Depends on:** none. Self-contained sandbox change.

### TODO-18: Pagination for `export_audit_log` and `list_eval_cases`

- **What:** Add `limit: int | None` and `offset: int | None` (or keyset `since_seq`) parameters to `export_audit_log` and `list_eval_cases`. Default to returning the full table (current behavior) for now; add a configurable hard cap once real volume exists.
- **Why:** Both functions do an unbounded `SELECT *` with no LIMIT. `export_audit_log` returns all rows into memory before serializing to canonical JSON; `list_eval_cases` does the same for the gate runner. `audit_entries` is WORM (can't be trimmed), so it grows monotonically. For MVP internal-only usage this is fine. For production with a real customer, a single export call against a multi-month log is an OOM/latency bomb.
- **Pros / Cons:** Simple to add; keyset pagination (`since_seq`) is already half-built for `export_audit_log`. The gate runner needs the full eval-case list per run — callers that need the full set should pass `limit=None` explicitly so the pattern is auditable.
- **Context:** `apps/kernel/src/ownevo_kernel/audit/writer.py:export_audit_log` and `apps/kernel/src/ownevo_kernel/eval_cases/registry.py:list_eval_cases`. Flagged in w2-foundations review (2026-05-03) by performance + security specialists.
- **Effort:** S (human ~half day / CC ~15 min).
- **Priority:** P2 — triggers before customer #1 has meaningful log volume.
- **Depends on:** none. Self-contained API change.

### TODO-9: Anti-pattern lint enforcement

- **What:** Custom check (Python ~5 lines, run in CI): any file in `apps/kernel/src/ownevo_kernel/` over 400 lines fails lint.
- **Why:** MVP doc § Anti-Patterns lists "don't put the whole harness in one file." Enforce as code, not as comment.
- **Effort:** XS (CC ~15 min).
- **Priority:** P3.
- **Depends on:** none.

---

## Feature roadmap (Phase 2 / post-MVP)

### TODO-10: Existing-trace OTel-ingest path (Entry Point B)

- **What:** Build the OTel collector intake adapter that maps customer's existing OTel spans → ownEvo's typed `AgentEvent` schema. Add workflow-inference step that reconstructs a workflow spec from observed traces.
- **Why:** Per MVP doc, "Customer already has OTel traces" entry point is mocked at `24-existing-connect.html`/`25-existing-inferred.html`. Real for customers who refuse to adopt yet-another-SDK.
- **Effort:** L (human ~2-3 weeks / CC ~3-5 days).
- **Priority:** P2 — first Phase-2 priority post-MVP per MVP doc § Phase 2.
- **Depends on:** MVP shipped + customer pull justifies intake-adapter work.

### TODO-11: Knowledge ingestion pipeline

- **What:** Slack/email/docs/runbooks → eval cases AND skill-shaped prompts (rules + rationale + negative examples).
- **Why:** Cold-start mechanism for non-greenfield customers. On Q3 2026 roadmap per MVP doc.
- **Effort:** L (human ~3-4 weeks / CC ~5-7 days).
- **Priority:** P2 — driven by enterprise design-partner pull.
- **Depends on:** ≥3 design partners onboarded.

### TODO-12: Lazy capability-queryable skill registry

- **What:** `list_skills(capability="forecasting")` query path with skill embeddings.
- **Why:** MVP-scale (~3-6 hand-picked workflows + M5 + τ³ templates) doesn't need this. Add when registry exceeds ~30 skills.
- **Effort:** S (human ~3-5 days / CC ~half day).
- **Priority:** P3 — driven by skill count.
- **Depends on:** skill registry has ≥30 distinct skills.

### TODO-13: Wave-2 framework integrations

- **What:** Mastra, LangGraph, OpenAI Agents SDK, raw Anthropic SDK middleware adapters.
- **Why:** Per MVP doc § Integration Targets Roadmap. Wave 1 (Claude Agent SDK) is enough for the demo + first design partners.
- **Effort:** M (~200-400 LOC per adapter; ~1 week per once schema is locked).
- **Priority:** P2 — driven by design-partner stack composition.
- **Depends on:** Wave 1 adapter shipped + first design partner asks for one of these.

### TODO-14: SWE-Bench Verified

- **What:** Generalization signal benchmark. Reuses M5 + τ³ infrastructure.
- **Why:** Per MVP doc § Phase 2. Lowest-priority benchmark — only run if there's bandwidth post-Demo-Day.
- **Effort:** S (human ~1 week / CC ~1-2 days once M5+τ³ infra is operational).
- **Priority:** P3.
- **Depends on:** MVP shipped.

### TODO-15: OpsAgent-Bench (custom benchmark we publish)

- **What:** Post-Series-A custom benchmark. Requires real customer relationships + design-partner consent + ground-truth annotation labour.
- **Why:** Per MVP doc § Phase 2. Framing-ownership moat.
- **Effort:** XL (~4-6 weeks once design partners are onboarded).
- **Priority:** P3 — post-Series-A material.
- **Depends on:** ≥3-5 design partners onboarded.

### TODO-16: Multi-agent topology graph view

- **What:** n8n / Google Opal style visualization for multi-agent workflows.
- **Why:** MVP workflows are single-agent loops; the Workflow Agent-anatomy pane (W7.1.12) is enough for single-agent inspection.
- **Effort:** M (human ~1-2 weeks / CC ~2-3 days).
- **Priority:** P3 — wait for multi-agent workflows to actually exist.
- **Depends on:** customer pull for multi-agent topologies.
