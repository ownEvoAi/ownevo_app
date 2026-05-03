# ownEvo MVP — Build Plan

8 weeks to a YC-grade demo on **three pillars**: the natural-language workflow generator (the customer-facing IP), the M5 code-gen-loop benchmark (supply-chain VP credibility), and the τ³-bench head-to-head with NeoSigma (YC-partner / AI-engineer credibility).

**Source of truth:** [`../../ownevo_docs/ownEvo_MVP.md`](../../ownevo_docs/ownEvo_MVP.md) (currently v4.1, 2026-05-03). Companion benchmark plans live at [`../../ownevo_docs/benchmarks/`](../../ownevo_docs/benchmarks/). Competitive framing lives at [`../../ownevo_docs/competitors/code-gen-loop-landscape.md`](../../ownevo_docs/competitors/code-gen-loop-landscape.md).

This doc is the executable derivation — what to build, in what order, with what validates each step. When the two conflict, the MVP doc wins; update this one.

*Last updated: 2026-05-03 (v2 — substrate-first parallel-track reframe per MVP doc v4.1)*

---

## North star (Week 8 demo)

A 90-second video that hits all three pillars without a slide:

1. **Cold open (0:00-0:08):** M5 lift chart, 30 simulated days compressed. Condition D (loop + approval gate) climbs visibly above condition A (frozen baseline).
2. **Hard cut to NL-gen (0:08-0:25):** A domain expert (Supply Chain VP role, non-engineer) types a workflow description in plain English. ownEvo generates simulator + eval cases + success metric in front of the reviewer.
3. **Loop runs (0:25-0:50):** Failures cluster, system proposes code change with plain-language summary, gate badge shows "passes 47/48 prior eval cases · improves new cluster by 12%", domain expert clicks Approve, hash-chained audit entry appears, lift chart annotation lands.
4. **τ³ split-screen (0:50-1:05):** Bar chart — ownEvo lift on τ³-bench *with the human-approval gate engaged* equals or beats NeoSigma's published autonomous +39.3%.
5. **Close (1:05-1:30):** Four-workflow tab strip (demand-prediction live, others as positioning); title card with `github.com/ownEvoAi/ownevo` and `make m5-replay` / `make tau3-replay` for reproducibility.

Reproducibility commitment: a reviewer who clones the repo gets both benchmark charts in <30 minutes from a fresh checkout.

---

## Phase 0 — Lock before Week 1

Decisions the MVP doc leaves loose. Pinning these now avoids Week 1 churn.

### Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Background jobs | asyncio + Postgres queue | MVP default. Migrate to Temporal post-MVP if gate runs need durable replay. |
| Primary DB | Postgres + pgvector | Skills, iterations, eval_cases, approvals, audit, failure_clusters embeddings. ClickHouse added when trace volume justifies. |
| Multi-tenancy | `workspace_id` + RLS from day one | Painful to retrofit. Every domain table. |
| Web framework | Next.js App Router (TS) | SSE + WebSocket for real-time gate-run status. |
| Python deps | uv | Already wired in `pyproject.toml`. |
| TS deps | pnpm | Standard for Next monorepos. |
| Agent runtime | Anthropic SDK + Claude Agent SDK (Python) | Wave 1 integration target. |
| Eval harness | Inspect AI | Confirmed in MVP doc. |
| Observability | Langfuse self-hosted + custom OTel spans | Confirmed in MVP doc. |

### New decisions to lock (added in v2 reframe)

| Decision | Recommended choice | Rationale | Block W1 if undecided? |
|---|---|---|---|
| **Sandboxed code execution** | **Modal** (managed, fastest to MVP) with abstraction so we can swap to e2b or self-hosted later | Modal: zero-infra, sub-second cold start, native Python. e2b: more control but ops burden. Pyodide: too restrictive (no LightGBM). | **YES — decide before W1 day 1.** Substrate dependency for everything. |
| **M5 fold strategy** | Held-out window: last 28 days = test fold; 28 prior days = validation fold for gate; everything before = training data agent can use | Mirrors real demand-planning evaluation; matches public M5 methodology. | YES — decide W1 day 1. |
| **τ³ approval mechanism for benchmark runs** | LLM-judge stub (Claude Sonnet) admits proposals if (a) gate passes AND (b) plain-language explanation is coherent. Subset re-run with human approver (founder) for credibility. | Per `benchmarks/tau3-bench.md`. Unattended runs need an automated approver; human subset documents both paths. | No — decide by W6. |
| **Reproducibility rig** | `make m5-replay` and `make tau3-replay` targets; Docker-packaged with cached intermediate artifacts (skill registry snapshots, eval-case snapshots) | <30-minute fresh-checkout repro is a Week-8 success criterion. | No — decide by W7. |
| **Public-results post format** | Immutable markdown files: `benchmarks/m5-results-2026-Q3.md`, `benchmarks/tau3-results-2026-Q3.md` in `ownevo_docs/benchmarks/` | Matches the established `<benchmark>-results-<date>.md` convention. | No — decide by W8. |

### Two questions still open (do not block W1)

- Managed cloud vs self-host only for design partners — affects `infra/` shape in Phase 3.
- License header for `packages/trace-format/` — Apache 2 working assumption per MVP doc; confirm before public publish.

---

## Phase 1 — Substrate (Weeks 1-2)

**Goal:** every primitive that **all three MVP pillars** depend on is real, tested, and exercised end-to-end on M5 by the end of Week 2. Nothing in Phase 2+ can start until this lands.

**Why this phase exists:** the natural-language workflow generator (Phase 2 Track A), the M5 benchmark (Phase 2 Track B), and the τ³-bench head-to-head (Phase 3 Track C) all share the same substrate. Building it once and proving it on M5 (the hardest target) means everything downstream just plugs in.

---

### Week 1 — Sandboxed exec + skill registry + trace capture + M5 dataset

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 1.1 | **`packages/trace-format/`** typed `AgentEvent` discriminated union | `packages/trace-format/src/` (Pydantic for Python, Zod-generated for TS) | JSON Schema generated; round-trip test (Python emit → TS parse → Python re-emit identical) |
| 1.2 | **Domain types** | `apps/kernel/src/ownevo_kernel/types.py` — `Workspace`, `Skill`, `SkillVersion`, `Iteration`, `EvalCase`, `Trace`, `FailureCluster`, `Proposal`, `Approval`, `AuditEntry`. Schema mirrors MVP doc § "auto-harness → ownEvo (web + database)" mapping. | Pydantic models import + validate; `pytest -k test_types` green |
| 1.3 | **Sandboxed code execution** wrapper around Modal | `apps/kernel/src/ownevo_kernel/sandbox/` — `SandboxRuntime` interface; `ModalSandbox` impl. Captures stdout/stderr/exitcode; pins deps via lockfile; enforces timeout + memory limits. | Smoke test: run `print("hello"); 1/0` in sandbox → captured stderr contains `ZeroDivisionError`, exitcode != 0. Run `import lightgbm; print(lightgbm.__version__)` → captured stdout has version string. |
| 1.4 | **Skill-file registry** | `apps/kernel/src/ownevo_kernel/skills/` — `SkillRegistry` class; Postgres-backed; `read_skill(workspace_id, skill_id, version=HEAD)`, `write_skill(workspace_id, skill_id, content, parent_version) -> new_version`. Each version a row with diff to parent. | Integration test: write v1; write v2 with edited content; read HEAD returns v2; read v1 returns v1; diff(v1, v2) returns expected unified diff. RLS test: write in workspace A, can't read from workspace B. |
| 1.5 | **Trace capture pipeline** | `apps/kernel/src/ownevo_kernel/tracing/` — Claude Agent SDK middleware emitting `AgentEvent` to OTel collector; `infra/docker-compose.yml` brings up Langfuse + Postgres + OTel collector. | Run τ-bench retail reference agent with middleware; events appear in Langfuse UI; `traces` table queryable by `workspace_id`. |
| 1.6 | **M5 dataset loaded** | `apps/kernel/src/ownevo_kernel/benchmarks/m5/` — `dataset_loader.py` downloads M5 (kaggle CLI, cached); held-out fold defined per Phase 0 decision; `metric.py` implements RMSE + WRMSSE. | `pytest -k test_m5_metric` confirms RMSE/WRMSSE on a fixture matches a known reference value within 1e-6. Held-out fold rows count matches expected. |
| 1.7 | **`infra/docker-compose.yml`** brings up local stack | Postgres + Langfuse + OTel collector + (later: web). Single command. | `docker compose up && uv run pytest` clean on a fresh machine. |

**Week 1 exit criteria (must all pass):**
- A hand-written hello-world skill executes in the sandbox, produces a metric value, is recorded as v1 in the registry — verified by integration test.
- The τ-bench retail reference agent emits structured `AgentEvent`s end-to-end — verified by Langfuse UI inspection + Postgres query.
- M5 dataset is loaded; baseline RMSE harness returns the same value across two runs (deterministic).
- `docker compose up && uv run pytest` passes on a fresh clone.

---

### Week 2 — Loop primitives + M5 baseline runs end-to-end

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 2.1 | **Coding-agent tool surface** | `apps/kernel/src/ownevo_kernel/agent_tools/` — `read_skill`, `write_skill`, `run_pipeline(workspace, version_id, fold)`, `read_metrics(run_id)`, `analyze_failures(run_id, k=10)`. Wired to Claude Agent SDK. | Unit test per tool; integration test: agent reads a skill, writes a modified version, runs the pipeline, reads the metric — all without human intervention. |
| 2.2 | **3-step regression gate** | `apps/kernel/src/ownevo_kernel/gate/` — copy semantics from `startup2026/core/agentos_harness/evolution/` (gate.py if present, otherwise reimplement). Steps: (1) prior-eval-suite still passes, (2) full-test val-score beats best-ever, (3) newly-passing failures auto-promote to suite. Background job; status streams via SSE. | Integration test: write change that improves on new case but breaks an old case → gate rejects. Write change that improves both → gate accepts and promotes new cases. Train/test discipline: gate refuses to use test-fold rows for training. |
| 2.3 | **Eval-case format + table** | `apps/kernel/src/ownevo_kernel/eval_cases/` — schema with `id`, `workspace_id`, `provenance` (cluster_id or "hand-authored" or "nl-gen"), `input`, `expected_behavior`, `regression_tolerance`, `created_at`. | Schema migration runs; insert + query roundtrip; eval-case provenance preserved through gate runs. |
| 2.4 | **Hash-chained audit trail** | `apps/kernel/src/ownevo_kernel/audit/` — `audit_entries` table with `prev_hash`, `entry_hash` (SHA-256 over canonical-JSON of entry + prev_hash). `append_audit_entry(workspace_id, kind, payload)`. Export: `export_audit_chain(workspace_id) -> JSON` (open format). | Integration test: append 3 entries; tamper with middle entry; verify-chain function detects break. Export → re-import → chain still verifies. |
| 2.5 | **Approval queue UI scaffold** | `apps/web/app/approvals/` — list of pending approvals; each card has plain-language summary placeholder, "View diff" toggle (side-by-side or unified), Approve/Reject + comment textbox. Functional skeleton; polish in W5. | Cypress/Playwright smoke: create pending approval via API → appears in queue → click Approve → state machine transitions → audit entry written. |
| 2.6 | **M5 baseline pipeline runs end-to-end** | Agent (Claude Agent SDK) writes Day-1 LightGBM baseline (6 skill files: `data_loader.py`, `feature_engineer.py`, `model_trainer.py`, `outlier_handler.py`, `ensemble.py`, `predictor.py`). Pipeline runs in sandbox on held-out fold. Baseline RMSE recorded as the floor. | `make m5-baseline` writes 6 skill files (v1) + records baseline RMSE in `iterations` table. RMSE is reproducible across two runs to within numeric tolerance. |
| 2.7 | **Substrate proves itself on a non-M5 task** | Hand-written sim + 3 eval cases + a hand-written skill that solves them. Run through the full pipeline (skill → sandbox → eval → gate → audit). | Smoke test passes end-to-end. Confirms substrate is domain-agnostic before Phase 2 starts. |

**Week 2 exit criteria (must all pass):**
- An agent-proposed change can be written, gated, approved (or rejected), and recorded in the audit chain end-to-end on M5 — proven by integration test.
- The same primitives work on a non-M5 hand-written sim — proven by 2.7's smoke test.
- M5 Day-1 baseline RMSE is recorded and reproducible.
- All `pytest` and Playwright smoke tests pass on a fresh clone.

**Phase 1 validation gate (must pass before Phase 2 starts):**

Run a fresh-checkout end-to-end smoke test:
1. `docker compose up`
2. `make m5-baseline` — agent writes 6 skill files, baseline RMSE recorded
3. Run the agent for one cycle: it edits one skill file, gate runs, audit entry written
4. Open `apps/web/` → see the proposed change in the approval queue → click Approve → audit chain has 2 entries (initial + approval)
5. Tamper with audit entry 1 in DB → run `verify_audit_chain(workspace)` → returns "broken at entry 2"

If any step fails, do not start Phase 2. Diagnose root cause. Slipping Phase 2 by a week is far cheaper than building Phase 2 on a broken substrate.

---

## Phase 2 — Two parallel tracks on the substrate (Weeks 3-6)

**Goal:** by end of Week 6, both Track A (NL-gen, the customer-facing IP) and Track B (M5 30-day replay, the supply-chain credibility) are running end-to-end on the same substrate. Tracks share the failure-clustering pipeline, eval-case format, regression gate, approval surface, and audit chain — built once in Phase 1, exercised by both tracks here.

**Why parallel:** Track A makes the substrate richer (eval-case format must handle generated metrics, not just M5 RMSE). Track B stress-tests it (4 conditions, 30-day replay, hard reproducibility). Each track's pressure improves the other.

---

### Track A — Natural-language workflow generator (the customer-facing IP)

The hardest phase to get right and the part most exposed to "demo cheats won't work for design partners." If Track A's quality slips, slip Phase 2 — do not paper over it.

#### Week 3 — NL → simulator (Track A)

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| A3.1 | **NL → workflow spec** | `apps/kernel/src/ownevo_kernel/nl_gen/` — Claude prompt + structured output (JSON schema): plain-English description → workflow spec (tools, environment, success criterion stub, UI primitives from MVP doc § UI Information Architecture). | Snapshot test: 3 hand-picked descriptions (supply chain demand forecast, credit risk, contract review) produce stable workflow specs. Schema validates. |
| A3.2 | **NL → simulator** | `apps/kernel/src/ownevo_kernel/nl_gen/sim_generator.py` — workflow spec → Python sim module written into the skill registry. Deterministic, seedable. | Replay-equivalence test: same seed → same trajectory across two runs. Generated sim runs end-to-end without manual fixup for at least 1 of the 3 hand-picked workflows. |
| A3.3 | **Sim runs in the sandbox** | The generated `sim.py` executes in the substrate sandbox (W1.3) without modification. | Generated sim from A3.2 runs in the sandbox; produces deterministic output. |

**Week 3 exit criterion (Track A):** at least one hand-picked workflow has a generated sim that runs deterministically in the sandbox.

#### Week 4 — NL → eval cases + metric, validate on 3 workflows (Track A)

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| A4.1 | **NL → eval case set** | `apps/kernel/src/ownevo_kernel/nl_gen/eval_generator.py` — workflow spec → 10-30 `EvalCase` rows. Provenance tagged "nl-gen". | Generated eval cases insert into the W2.3 schema; replay against the generated sim produces deterministic pass/fail. |
| A4.2 | **NL → success metric** | `apps/kernel/src/ownevo_kernel/nl_gen/metric_generator.py` — workflow spec → metric definition (precision/recall, threshold, etc.). | Metric runs over generated eval cases; returns float in expected range. |
| A4.3 | **Inspect AI integration** | `apps/kernel/src/ownevo_kernel/eval_runner/` — generated eval cases → Inspect AI task. Single command: replay an agent → score. | `make eval-replay WORKFLOW=demand-prediction` runs the loop end-to-end and emits a score. |
| A4.4 | **Validate on 3 workflows end-to-end** | Supply chain demand forecast + credit risk + contract review. Each must produce a working sim + eval set + metric that a Claude agent runs and Inspect AI scores. | All 3 workflows pass `make nl-gen-smoketest WORKFLOW=<name>`. **If even one fails, slip Phase 2.** |
| A4.5 | **Cost + determinism guardrails** | Fixed token budget per eval replay (Karpathy pattern); nondeterministic eval failures flagged as bugs. | Token budget exceeded → run aborts cleanly. Repeat eval-replay → identical score (within numeric tolerance). |

**Week 4 exit criterion (Track A):** plain-English description in → working sim + eval set + metric out, validated on all 3 workflows. **The single most important quality gate of the whole MVP.**

---

### Track B — M5 code-gen-loop benchmark (credibility test, runs in parallel)

#### Week 3 — Failure mining on M5 (Track B)

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| B3.1 | **`analyze_failures` on M5 misses** | `apps/kernel/src/ownevo_kernel/benchmarks/m5/failure_analyzer.py` — top-k worst predictions with structured context (which SKUs, stores, time windows, feature gaps). | Run on M5 baseline output; returns 10 top-k rows with structured context. |
| B3.2 | **Failure clustering pipeline** | `apps/kernel/src/ownevo_kernel/clustering/` — sentence-transformers embed (all-MiniLM-L6-v2 or similar) → UMAP reduce → HDBSCAN cluster → Claude-labeled. Output: `failure_clusters` table (traces, root-cause one-liner, severity, sample excerpt). | Cluster M5 misses → 3+ named clusters appear (e.g., "winter footwear in Pacific NW Q4"). Cluster labels are intelligible. |
| B3.3 | **Cluster → eval case** | `apps/kernel/src/ownevo_kernel/eval_cases/from_cluster.py` — each cluster spawns 1+ `EvalCase` rows tagged with `provenance: cluster:<id>`. | First eval cases generated from clusters; insert into W2.3 schema; pass/fail reproducible. |

**Week 3 exit criterion (Track B):** running M5 baseline + 1 simulated week → ≥3 failure clusters surface → ≥3 eval cases generated, all without human intervention.

#### Week 4 — First end-to-end M5 loop cycle (Track B)

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| B4.1 | **M5 proposer agent** | Reuse `agent_tools` from W2.1. Agent reads a cluster, proposes a skill diff. One hypothesis per iteration. Three failures on the same hypothesis → abandon. | Agent proposes a code change in response to a cluster; gate runs against the change; audit entry written. |
| B4.2 | **First lift on M5** | At least one agent-proposed change passes the gate end-to-end and lifts a held-out metric measurably. | RMSE on held-out fold strictly improves after the change is approved; lift recorded in `iterations` table. |
| B4.3 | **First gate-blocked regression** | At least one proposed change is correctly rejected by the gate (e.g., it improves the new cluster but regresses prior eval cases). | Audit chain shows ≥1 reject entry with structured rationale. |

**Week 4 exit criterion (Track B):** at least one agent-proposed change passes the gate and lifts a metric; at least one regression is caught by the gate.

#### Week 5 — Approval surface polish + 7-day M5 replay

Track A and Track B converge in W5 because **both tracks share the approval surface**. Polish it once.

| # | Track | Deliverable | Files / location | Validation |
|---|---|---|---|---|
| 5.1 | **Shared** | **Approval surface — full polish** | `apps/web/app/approvals/[id]/page.tsx` — plain-language summary on top, side-by-side diff (Monaco or similar), gate-results badge with per-eval-case breakdown, expected-impact estimate, Approve/Reject with comment-to-eval-case flow. | Cypress flow: open card → approve with comment → state transitions → audit entry → if rejected, comment becomes a new eval case. Same UX serves NL-gen-flow and M5 approvals. |
| 5.2 | **Shared** | **LLM-judge stub approver** | `apps/kernel/src/ownevo_kernel/approvers/llm_judge.py` — admits proposals if (a) gate passes AND (b) plain-language explanation is coherent (Claude Sonnet judge). Used for unattended benchmark runs. | Run on 5 hand-crafted proposals (3 good, 2 bad) → judge admits 3, rejects 2. |
| 5.3 | **A** | **NL-gen failure clustering** | Track A's generated-sim traces flow through the W3 clustering pipeline (Track B's clustering, reused). | Run NL-gen workflow → cluster traces → at least 3 NL-gen-derived clusters appear. |
| 5.4 | **B** | **7-day M5 replay** | Replay 7 simulated days of M5. Each day: agent proposes → gate runs → LLM-judge admits or rejects → audit chain grows → eval set grows. | `make m5-replay-7day` produces a visibly climbing lift curve over 7 cycles; audit chain has 7+ entries; eval set grew from clusters. |

**Week 5 exit criteria:**
- (Shared) Approval surface usable by a non-engineer in under 1 minute per card (dogfood test with a non-engineer reviewer).
- (Track A) Generated-sim traces flow through clustering successfully.
- (Track B) 7-day M5 replay produces a visibly climbing lift curve.

#### Week 6 — Full M5 30-day replay + NL-gen end-to-end demo

| # | Track | Deliverable | Files / location | Validation |
|---|---|---|---|---|
| 6.1 | **A** | **NL-gen end-to-end live demo** | The full Track A flow runs in <5 minutes from "type description" to "lift chart climbs". On a hand-picked workflow (probably supply-chain demand-forecast since it overlaps M5 narrative). | **Validation gate:** an external reviewer (founder/advisor) can sit through the live demo without intervention; lift chart visibly moves. |
| 6.2 | **B** | **Full 30-day M5 replay across 4 conditions** | Per [`benchmarks/m5-code-gen-loop.md`](../../ownevo_docs/benchmarks/m5-code-gen-loop.md): A (frozen baseline), B (static LLM single-shot, sanity check), C (loop autonomous), D (loop + approval gate). | `make m5-replay-30day` runs all 4 conditions; hero chart generated; per-cluster lift report generated; gate-blocked-regression count emitted. |
| 6.3 | **B** | **M5 success thresholds met** | Per `benchmarks/m5-code-gen-loop.md` § Success Criteria: ≥+25% RMSE lift Day-1→Day-30 in condition D; ≥50 eval cases generated; ≥15 approved revisions; ≥5 gate-blocked regressions; reproducible from fresh checkout. | All thresholds verified by reading the run record. If any miss the threshold, document why + decide whether to extend Phase 2 or accept the lower number. |

**Week 6 exit criteria (Phase 2 validation gate, must pass before Phase 3):**
- (Track A) NL-gen end-to-end demo runs live in <5 minutes for an external reviewer.
- (Track B) M5 30-day replay completes; hero chart emitted; all success thresholds met or explicitly waived.
- Both tracks produce audit chains that pass `verify_audit_chain` end-to-end.

---

## Phase 3 — Customer skin + τ³-bench head-to-head + demo materials (Weeks 7-8)

**Goal:** by end of Week 8, the workspace UI from `www/preview/yc-s26-rk7p3/` is wired to the live demand-prediction backend, the τ³-bench head-to-head against NeoSigma is published with the human-approval gate engaged, and the YC video + reproducibility artifacts are shipped.

---

### Week 7 — Customer skin (Track 1) + τ³-bench template (Track 3, parallel)

#### Track 1 — Customer-facing workspace skin

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 7.1.1 | **Wire workspace UI to live backends** | `apps/web/app/workspaces/[wsId]/workflows/demand-prediction/` — Health page, Failures, Eval cases, Audit, Skills, Operate views — all reading from live tables for the M5-backed demand-prediction workflow. Match the visual target in `www/preview/yc-s26-rk7p3/`. | Cypress: open workspace → see live M5 lift chart → click into a real failure cluster → see real proposal cards → real audit chain entries. |
| 7.1.2 | **Lift chart UI** (the YC closer) | `apps/web/components/LiftChart.tsx` — time-series, baseline vs ownEvo, annotated with each approved improvement. | Renders Track B's M5 lift over 30 days. Annotations correspond to real audit entries. |
| 7.1.3 | **Failure cluster card UI** | `apps/web/components/FailureClusterCard.tsx` — matches the mock, real backing data. | All 3+ M5 clusters render as cards with real top-k worst predictions. |
| 7.1.4 | **Proposal review card UI** | `apps/web/components/ProposalCard.tsx` — plain-language summary on top, side-by-side diff, gate badge with per-eval breakdown. | All 15+ approved Track B proposals viewable historically + any pending proposal viewable in queue. |
| 7.1.5 | **Audit trail UI** | `apps/web/app/workspaces/[wsId]/audit/page.tsx` — chronological hash-chained history; each entry expandable; "verify chain" button calls W2.4 export+verify. | Verify-chain button shows green check; click an entry → see diff + gate result + approval rationale. |
| 7.1.6 | **Health page (default landing)** | `apps/web/app/workspaces/[wsId]/page.tsx` — glance metrics across workflows (only demand-prediction is live; other 3 are positioning mocks). | Default landing for the workspace; M5 lift chart visible above the fold. |
| 7.1.7 | **"New Workflow" entry point active** | The workspace UI surfaces "New Workflow" in the sidebar; clicking it opens the NL-gen flow from Track A. | A non-engineer can describe a workflow in plain English in the live workspace UI and see the generation happen. |
| 7.1.8 | **Three other workflows as positioning mocks** | Labour, contract, customer support — wire to mock data per `ownEvo_MVP_mocks.md` (or current preview). Visual parity with demand-prediction; no live backend. | Tab strip shows 4 workflows; clicking the 3 mocks renders the mocked surfaces; the framing "same loop, NL-gen the rest" is visible. |

#### Track 3 — τ³-bench template + reproduce-NeoSigma sanity check

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 7.3.1 | **τ³-bench dataset + harness** | `apps/kernel/src/ownevo_kernel/benchmarks/tau3/` — dataset loader, training/test split per Sierra's published methodology, scoring harness. | Hand-run a known-good agent against the test set → score matches Sierra's published baseline within ±2pp. |
| 7.3.2 | **Per-domain agent templates** | Templates for retail, airline, telecom. Same coding-agent harness as M5; per-domain skill-file shapes (multi-turn agent tasks rather than tabular forecasting). | Each template runs end-to-end on training subset; emits structured AgentEvents. |
| 7.3.3 | **Conditions A + B replay on training subset** | A (frozen baseline) + B (loop autonomous, structurally equivalent to NeoSigma's auto-harness). | **Validation gate:** condition B reproduces NeoSigma's published Tau3 number (0.78) to within ±5pp on the training subset. If not, diagnose before W8 condition C runs. |

**Week 7 exit criteria:**
- (Track 1) A non-engineer can open the workspace UI, type a workflow description, watch ownEvo generate sim+evals+metric, see the M5 lift chart climbing for demand-prediction, click into a failure cluster, approve a proposed change in plain language, and watch the audit trail update — all live, no manual fixup.
- (Track 3) Condition B reproduces NeoSigma's published number to within ±5pp on the training subset.

---

### Week 8 — Full τ³ replay + demo materials + reproducibility + onboarding

#### Track 3 — τ³-bench completion

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 8.3.1 | **Condition C with gate engaged on full test set** | LLM-judge stub approver (W5.2) admits proposals; subset re-run with human approver (founder/advisor) for credibility. | Threshold: ≥+35% lift A→C. Stretch: ≥+40% (beats NeoSigma's autonomous +39.3%). All approved changes have a hash-chained audit entry. |
| 8.3.2 | **`benchmarks/tau3-results-2026-Q3.md`** | `ownevo_docs/benchmarks/tau3-results-2026-Q3.md` — immutable run record, three conditions plotted, head-to-head with NeoSigma, full audit chain exportable. | File written; reviewer can clone the repo and re-derive the chart from the audit chain. |
| 8.3.3 | **Sample human-approved subset documented** | ≥5 changes from condition C re-approved by a human (founder/advisor) instead of the LLM-judge stub. Document any divergence between human and LLM-judge decisions. | Subset documented in tau3-results post; honesty about any divergences preserved. |

#### Track 1 — Demo materials (M5 + τ³ + NL-gen together)

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 8.1.1 | **Record 90-second YC video** | Per North Star storyboard. Live demand-prediction workspace + real M5 results from W6 + real τ³ split-screen from W8 + live NL-gen flow scene. | Single take or minimal cuts; reviewer who watches understands all 3 pillars without a slide. |
| 8.1.2 | **`benchmarks/m5-results-2026-Q3.md`** | `ownevo_docs/benchmarks/m5-results-2026-Q3.md` — immutable M5 run record, all 4 conditions plotted, audit chain exportable. | File written; matches success thresholds from `benchmarks/m5-code-gen-loop.md`. |
| 8.1.3 | **Reproducibility rig** | `make m5-replay` and `make tau3-replay` Makefile targets; Docker-packaged; cached intermediate artifacts (skill registry snapshots, eval-case snapshots) so replay is fast for reviewers. | **Validation gate:** an external reviewer who clones the repo gets both charts in <30 minutes from a fresh machine. |
| 8.1.4 | **Website screenshots** | Capture from the real workspace; replace placeholders in `www/index.html` per `ownEvo_MVP.md` § Website Screenshots. Add τ³ head-to-head chart to the Validation section. | Website rebuilt with real screenshots; no placeholders remain. |
| 8.1.5 | **Onboarding doc + friction-free install** | `docs/onboarding.md` — Wave 1 (Claude Agent SDK middleware) install path; tested by an external person. | An external person follows the doc and emits a structured AgentEvent into Langfuse in <30 minutes. |

**Week 8 exit criteria (Phase 3 validation gate):**
- YC video shipped.
- Both `m5-results-*` and `tau3-results-*` posts published.
- `make m5-replay` and `make tau3-replay` work for an external reviewer in <30 minutes from fresh checkout.
- τ³ result hits the ≥+35% threshold (stretch ≥+40%).
- Website screenshots replaced with real product UI.
- Onboarding doc validated by an external person.

---

## Validation strategy summary (per phase)

Each phase has a hard validation gate before the next can start. Slipping a phase by a week is far cheaper than building on a broken foundation.

| Phase | Validation gate | Failure mode if skipped |
|---|---|---|
| **Phase 0** | Sandbox choice locked, M5 fold strategy locked | W1 day-1 churn — sandbox choice is the gating dependency |
| **Phase 1 (W1-2)** | Fresh-checkout end-to-end smoke: docker compose up, agent writes baseline, edits a skill, gate runs, audit entry written, audit-chain verify catches tamper | Phase 2 builds on broken substrate; both tracks degrade together |
| **Phase 2 W4 (Track A only)** | All 3 hand-picked workflows produce working NL-gen sim+eval+metric. **The single most important quality gate of the whole MVP.** | NL-gen demo looks fake to design partners; the IP claim collapses |
| **Phase 2 W6 (Tracks A + B)** | Live NL-gen demo in <5 min for external reviewer + M5 30-day replay meets all thresholds | YC demo loses one or both pillars |
| **Phase 3 W7 (Track 3)** | Condition B reproduces NeoSigma to within ±5pp on training subset | We can't credibly claim head-to-head; W8 condition-C results lose anchor |
| **Phase 3 W8 (final)** | YC video shipped + both results posts + reproducibility + τ³ ≥+35% threshold | MVP slips to 9-10 weeks |

---

## Explicitly NOT in MVP

Per `ownEvo_MVP.md` § Out of Scope. Repeated because they will tempt us mid-build:

- Existing-trace OTel-ingest path — Phase 2 (post-MVP); mocks remain in `www/preview/`
- Live backends for labour, contract, customer-support workflows — Phase 2 (driven by customer pull); mocks remain
- Multiple framework integrations beyond Claude Agent SDK — Wave 2 (post-MVP)
- SWE-Bench Verified — Phase 2 (post-MVP); reuses the same substrate ~1 week
- OpsAgent-Bench (custom benchmark we publish) — post-Series-A
- Self-evolving the harness itself (we evolve skills/prompts/code only)
- Custom Rust gateway (LiteLLM is enough; revisit if local-model latency becomes a problem)
- Multi-tenant features beyond scaffolding (workspace switcher, billing UI, org admin) — single demo workspace is enough for first 10 customers
- Knowledge ingestion from Slack/email/docs/runbooks — Q3 2026 per existing roadmap
- Mobile UI
- Built-in skills marketplace
- Re-running tau-bench in original prompt-only form (auto-harness already proved that loop; we run code-gen-under-gate on M5 + τ³ instead)

---

## Risks (ranked)

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| 1 | **NL sim quality (Track A, W3-4).** Toy-looking sims kill the customer-facing IP demo. | Medium | Catastrophic | Explicit W4 review gate (all 3 workflows must pass); willing to slip Phase 2 a week. **The single most important quality gate of the whole MVP.** |
| 2 | **Sandbox stability (Phase 1).** If sandboxed code execution is flaky, every M5 cycle is flaky and 30-day replay never converges. | Medium | High | Lock sandbox choice in Phase 0; smoke test in W1; build retry/timeout discipline into the runner; cache intermediate artifacts so re-runs are cheap. |
| 3 | **NeoSigma number reproduction (Track 3, W7).** If condition B doesn't reproduce NeoSigma's +39.3% to within ±5pp, the head-to-head loses its anchor. | Medium | High | Use NeoSigma's open-source auto-harness as the literal condition-B baseline; publish honest reproduction notes; budget time in W7 to debug. |
| 4 | **Failure clustering signal-to-noise (Phase 1 + Phase 2).** Nonsense clusters undermine credibility. | Medium | Medium | Validate clustering on real τ-bench traces in W3 before letting M5/NL-gen demo data anchor cluster examples; tune cluster-labeling prompt. |
| 5 | **Approval UX cognitive load (Phase 2 W5).** "Plain-language summary" carries the value prop. If it doesn't reduce reviewer effort, the loop is just process. | Medium | High | Dogfood on real proposals by W6; design-partner test by W7; non-engineer time-to-decision must be <1 min/card. |
| 6 | **30-day replay token cost overrun (Track B, W6).** Code-gen + execution loops are token-hungry. | Medium | Medium | Cap per-cycle token budget; use local models (qwen3:32b or similar) for routine work; cache aggressively. Budget several hundred dollars per complete 30-day replay. |
| 7 | **Multi-tenant retrofit cost.** Postponing RLS would save 1-2 weeks now, cost 4+ weeks later. | Low | High | Enforce `workspace_id` on every domain table from W1. Smoke test cross-workspace isolation by end of W1. |
| 8 | **Demo data feeling fake (Phase 3 mock workflows only).** Demand-prediction uses real M5 data, but the 3 positioning-mock workflows still need believable UX props. | Low | Low | Light prop-grade synthetic data for the 3 mocks; don't over-invest. |
| 9 | **Audit-chain export-import asymmetry (Phase 1 W2).** If a chain exports but doesn't re-import-and-verify, the sovereignty story breaks. | Low | High | Round-trip test in W2.4; canonical-JSON format (sorted keys, no whitespace) avoids serialization drift. |
| 10 | **Sandbox dependency drift across replays.** A LightGBM version bump changes M5 results between replays, breaking reproducibility. | Low | Medium | Pin all deps via lockfile in the sandbox image; cache the image; reproducibility rig validates this. |

---

## Open questions to track (do not block W1)

- Pricing model for first paying pilots — MVP doc lands on "platform fee + usage" but unit-of-value is TBD.
- License terms for `packages/trace-format/` — Apache 2 working assumption; confirm before public publish.
- Self-host vs managed cloud for first 5 customers — affects Phase 3 install docs.
- When to OSS-release `packages/trace-format/` — sooner = format wins faster; locks API early.
- Eval-case generation from clusters: do we let the LLM-judge stub also propose new eval cases, or only humans? (Affects credibility of "auto-grown eval set".)
- Public-results post: do we publish the audit chain export as a sidecar artifact, or embed it in the post? (Affects reviewer reproduction speed.)

---

## Where the work lives

| Phase / Track deliverable | Repo path |
|---|---|
| `AgentEvent` schema | `packages/trace-format/src/` |
| Domain types, sandbox, skill registry, gate, eval-cases, audit, clustering, NL-gen, agent tools | `apps/kernel/src/ownevo_kernel/` |
| M5 dataset + harness + failure analyzer | `apps/kernel/src/ownevo_kernel/benchmarks/m5/` |
| τ³-bench dataset + per-domain templates + harness | `apps/kernel/src/ownevo_kernel/benchmarks/tau3/` |
| Reusable approvers (LLM-judge stub) | `apps/kernel/src/ownevo_kernel/approvers/` |
| Approval UX, lift chart, audit trail, workspace dashboards, NL-gen flow UI | `apps/web/app/`, `apps/web/components/` |
| Local docker stack | `infra/docker-compose.yml` |
| Reproducibility rig (`make m5-replay`, `make tau3-replay`) | Top-level `Makefile` |
| Architecture notes, ADRs, this plan | `docs/` |
| **Benchmark plans (source of truth)** | `../../ownevo_docs/benchmarks/` |
| **Benchmark results posts (immutable run records)** | `../../ownevo_docs/benchmarks/<benchmark>-results-<date>.md` |
| **MVP doc (source of truth)** | `../../ownevo_docs/ownEvo_MVP.md` |

---

## Cross-references

- [`../../ownevo_docs/ownEvo_MVP.md`](../../ownevo_docs/ownEvo_MVP.md) — source of truth for scope, stack, sequencing
- [`../../ownevo_docs/benchmarks/m5-code-gen-loop.md`](../../ownevo_docs/benchmarks/m5-code-gen-loop.md) — M5 plan
- [`../../ownevo_docs/benchmarks/tau3-bench.md`](../../ownevo_docs/benchmarks/tau3-bench.md) — τ³ plan
- [`../../ownevo_docs/benchmarks/README.md`](../../ownevo_docs/benchmarks/README.md) — benchmark index
- [`../../ownevo_docs/competitors/code-gen-loop-landscape.md`](../../ownevo_docs/competitors/code-gen-loop-landscape.md) — competitive framing for code-gen-under-regression-gate
- [`../../ownevo_docs/competitors/neosigma.md`](../../ownevo_docs/competitors/neosigma.md) — auto-harness reference architecture (lift evolution loop semantics from here)
- [`../CLAUDE.md`](../CLAUDE.md) — repo-level conventions for future sessions
