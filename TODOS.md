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

### TODO-4: `AgentEvent` schema OSS positioning [DEADLINE: before W3]

- **What:** Decide whether to open-source `packages/trace-format/` under Apache 2 as a community standard (OTel-Gen-AI-aligned), or keep proprietary as a moat.
- **Why:** The schema is the contract for the improvement loop. If it becomes a standard, every customer's agent traces flow through ownEvo's format for free. If it stays proprietary, the moat deepens but adoption slows.
- **Pros (Apache 2):** Implementation cost is 0 if Apache 2 from start. Industry alignment. Easier customer integration ("we already speak this").
- **Cons (Apache 2):** Loses "we define the format" lock-in. Competitors get the standard for free.
- **Pros (proprietary):** Stronger moat. Format-defining role.
- **Cons (proprietary):** Slower adoption. Retrofit to Apache 2 later costs weeks.
- **Context:** This is the **single unresolved strategic call** from the 2026-05-03 CEO review. Surfaced in [`PLAN.md`](docs/PLAN.md) § Open Questions (DEADLINE: before W3) and [`ownEvo_MVP.md`](../ownevo_docs/ownEvo_MVP.md) § Open Questions.
- **Effort:** S decision; 0 if right call from start, L if retrofitted.
- **Priority:** **P0 — block W3.**
- **Depends on:** founder/board discussion of OSS strategy.

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
