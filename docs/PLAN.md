# ownEvo MVP — Build Plan

8 weeks to a YC-grade demo plus the underlying production pipeline.

Source of truth: [`../../ownevo_docs/ownEvo_MVP.md`](../../ownevo_docs/ownEvo_MVP.md). This doc is the executable derivation — what to build, in what order, with what unknowns called out. When the two conflict, the MVP doc wins; update this one.

*Last updated: 2026-05-02*

---

## North star (Week 8 demo)

Domain expert types a workflow description in plain English. ownEvo generates the simulator, eval cases, and success metric in front of them. Initial agent runs; lift chart shows baseline. Within minutes, 3+ failure clusters surface. Domain expert reviews proposed improvements in plain language, approves. Regression gate validates each approved change. Lift chart climbs visibly with full audit trail.

Auto-harness's +40% on tau-bench is cited as proof-of-machinery. We do not re-run it.

---

## Phase 0 — Lock before Week 1

Decisions the MVP doc leaves loose. Pinning these now avoids Week 1 churn.

| Decision | Choice | Rationale |
|---|---|---|
| Background jobs | asyncio + Postgres queue | MVP default per doc. Migrate to Temporal post-MVP if gate runs need durable replay. |
| Primary DB | Postgres + pgvector | Skills, iterations, eval_cases, approvals, audit. ClickHouse added when trace volume justifies. |
| Multi-tenancy | `workspace_id` + RLS from day one | Painful to retrofit. |
| Web framework | Next.js App Router (TS) | Standard surface; SSE/WebSocket for real-time. |
| Python deps | uv | Already wired in workspace `pyproject.toml`. |
| TS deps | pnpm | Standard for Next monorepos. |
| Agent runtime | Anthropic SDK + Claude Agent SDK (Python) | Wave 1 integration target. |
| Eval harness | Inspect AI | Confirmed in doc. |
| Observability | Langfuse self-hosted + custom OTel spans | Confirmed in doc. |

**Two questions to flag (do not block W1):**
- Managed cloud vs self-host only for design partners — affects `infra/` shape in Phase 4.
- License header for `trace-format/` — Apache 2 is the working assumption per doc; confirm before public publish.

---

## Phase 1 — Foundation (Weeks 1-2)

Goal: structured traces flowing from a real agent through Langfuse, evolution loop lifted and adapted, repo runnable on a clean machine.

### Week 1
1. **`packages/trace-format/`** — typed `AgentEvent` discriminated union (Pydantic + Zod), JSON Schema generated. The contract everything else hangs on.
2. **`apps/kernel/src/ownevo_kernel/types.py`** — domain types: Workspace, Skill, Iteration, EvalCase, Trace, Approval. Schema mirrors the "auto-harness → ownEvo (web + database)" mapping in the MVP doc.
3. **`apps/kernel/src/ownevo_kernel/evolution/`** — copy `tracker.py`, `reflector.py`, `curator.py`, `proposer.py` from `startup2026/core/agentos_harness/evolution/`. Add `regression_gate` to `ProposalAction`. Reframe SRE-incident → skill-change. Keep the 377 tests green through the lift.
4. **`infra/docker-compose.yml`** — Postgres + Langfuse + OTel collector. One command to local-stack.

### Week 2
5. **Claude Agent SDK middleware** — adapter that intercepts SDK tool calls, emits typed `AgentEvent` to the OTel collector. ~200-400 lines per the doc.
6. **Trace ingestion pipeline** — collector → Langfuse + Postgres `traces` table scoped by `workspace_id`.
7. **τ-bench retail reference agent** — wire with the middleware, run 100 trajectories. Proves trace plumbing on a real agent; produces clustering input for Week 5.
8. **Integration test seam** — agent runs → events captured → visible in Langfuse → queryable by `workspace_id` in Postgres.

**Exit criteria:** real agent traces flowing end-to-end. Evolution loop callable as a library with at least one unit test green. `docker compose up && uv run pytest` clean on a fresh machine.

---

## Phase 2 — Natural-language differentiator (Weeks 3-4)

The IP. No reference exists. This is the hardest phase to get right and the part most exposed to "demo cheats won't work for design partners."

### Week 3
1. **NL → workflow spec** — Claude prompt + structured output: plain-English description → workflow spec (tools, environment, success criterion, UI primitives from `ownEvo_MVP.md` § UI Information Architecture).
2. **NL → simulator** — workflow spec → Python sim module. Deterministic, seedable. Tested by replay-equivalence (same seed → same trajectory).
3. **NL → eval case set** — 10-30 representative `EvalCase` rows with success criteria.
4. **NL → success metric** — metric definition tied to the eval case format.

### Week 4
5. **Inspect AI integration** — generated eval cases → Inspect AI task. Single command: replay an agent → score.
6. **Validate on 3 workflows** — supply chain demand forecast, credit risk, contract review. Each must produce a working sim that a Claude agent runs and Inspect AI scores. If even one fails, slip Phase 3 — do not paper over it.
7. **Cost + determinism guardrails** — fixed token budget per eval replay (Karpathy pattern). Nondeterministic eval failures are bugs.

**Exit criteria:** plain-English description in → working sim + eval set + metric out. Three workflows demonstrably end-to-end. Four demo workflows from `ownEvo_MVP_mocks.md` are scaffolded; demand prediction is fully functional (it's the YC hero).

**Risk:** sim quality. A weak sim makes the loop look fake. **The single most important quality gate of the whole MVP.**

---

## Phase 3 — Loop closure (Weeks 5-6)

Goal: the loop runs autonomously with human approval as the only gate.

### Week 5
1. **Failure clustering** — sentence-transformers embed → UMAP reduce → HDBSCAN cluster → Claude-labeled. Output: `failure_clusters` table (traces, root-cause one-liner, severity, sample excerpt).
2. **Cluster → eval case** — each cluster spawns one or more `EvalCase` rows tagged with cluster id (provenance preserved).
3. **3-step regression gate runner** — replay eval cases against proposed skill change. Block on any pass→fail regression. Promote newly-passing failures into the suite. Auto-harness's three steps verbatim.

### Week 6
4. **Proposer agent** — Claude Agent SDK reads a cluster, proposes a skill diff. One hypothesis per iteration. Three failures on the same hypothesis → abandon.
5. **Approval queue (kernel + DB)** — `approvals` table, state machine: pending → approved/rejected/superseded. Audit row on every transition.
6. **Approval queue (web)** — minimum viable: list of cards with plain-language summary + "View diff" + Approve/Reject. Not polished — that's Phase 4.
7. **Reject-with-comment → eval case** — comment IS the failure description; captured as a new `EvalCase` row.

**Exit criteria:** end-to-end loop runs unattended on the demand-prediction workflow. Cluster surfaces. Proposer suggests. Gate runs. UI shows the card. Approve → new skill version → lift chart moves.

---

## Phase 4 — Demo polish (Weeks 7-8)

Mocks at `www/preview/yc-s26-rk7p3/` define the visual target. Week 7 is making the production app match those mocks on the photographable surfaces.

### Week 7
1. **Lift chart UI** (the YC closer) — time-series, baseline vs ownEvo, annotated with each approved improvement.
2. **Failure cluster card UI** — matches the mock, real backing data.
3. **Proposal review card UI** — plain-language summary on top, side-by-side diff, gate badge.
4. **Audit trail UI** — chronological approval history (will be hash-chained later; visual first).
5. **Health page (default landing)** — glance metrics across workflows.
6. **Synthetic supply-chain trace stream** — 100-200 runs, believable failure patterns. UX prop. Throwaway once design partners come online.

### Week 8
7. **Demo workspace polish** — Acme Distribution data per `ownEvo_MVP_mocks.md`. Four workflows scaffolded; demand prediction fully functional.
8. **YC video** — record per storyboard in `ownEvo_MVP.md` § YC Demo Storyboard.
9. **Website screenshots** — capture, replace placeholders in `www/index.html`.
10. **Onboarding doc** — friction-free install path for design partners.

**Exit criteria:** YC video recorded. Website screenshots live. Demo workspace runs the full loop in under 60 seconds, on demand.

---

## Explicitly NOT in MVP

Per `ownEvo_MVP.md` § Out of Scope. Repeated because they will tempt us mid-build:

- Multiple framework integrations (Claude Agent SDK only)
- Re-running tau-bench for an ownEvo-branded number
- Multi-tenant admin UI / workspace switcher (scaffolding works; no UI)
- Mobile or end-user-facing UI
- Built-in skills marketplace
- Knowledge ingestion connectors (Slack / email / Confluence) — Q3 2026
- Custom LLM gateway (LiteLLM is enough)
- Public benchmark methodology paper

---

## Risks (ranked)

1. **NL sim quality (Phase 2).** Toys-looking sims kill the demo. Mitigation: explicit Week 4 review gate; willing to slip Phase 3 a week.
2. **Failure clustering signal-to-noise.** Nonsense clusters undermine credibility. Mitigation: validate on real τ-bench traces in Week 5 before letting demo data anchor cluster examples.
3. **Approval UX cognitive load.** "Plain-language summary" carries the value prop. If it doesn't reduce reviewer effort, the loop is just process. Mitigation: dogfood on real proposals by Week 6; design-partner test by Week 7.
4. **Multi-tenant retrofit cost.** Postponing RLS would save 1-2 weeks now, cost 4+ weeks later. Mitigation: enforce `workspace_id` on every domain table from Week 1.
5. **Demo data feeling fake.** Synthetic stream is a UX prop. If reviewers smell stock, credibility cracks. Mitigation: Week 7 budgets explicit time to make it read as real.

---

## Open questions to track (do not block W1)

- Pricing model for first paying pilots — doc lands on "platform fee + usage" but unit-of-value is TBD.
- License terms for `trace-format/` — Apache 2 working assumption; confirm before public publish.
- Self-host vs managed cloud for first 5 customers — affects Phase 4 install docs.
- When to OSS-release `trace-format/` — sooner = format wins faster; locks API early.

---

## Where the work lives

| Phase deliverable | Repo path |
|---|---|
| `AgentEvent` schema | `packages/trace-format/` |
| Domain types, evolution loop, eval, gate, clustering, proposer | `apps/kernel/src/ownevo_kernel/` |
| Approval UX, lift chart, audit, workspace dashboards | `apps/web/` |
| Local docker stack | `infra/` |
| Architecture notes, ADRs, this plan | `docs/` |
