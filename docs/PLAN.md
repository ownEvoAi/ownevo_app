# ownEvo MVP — Build Plan

8 weeks to a YC-grade demo on **three pillars**: the natural-language workflow generator (the customer-facing IP), the M5 code-gen-loop benchmark (supply-chain VP credibility), and the τ³-bench head-to-head with NeoSigma (YC-partner / AI-engineer credibility).

**Source of truth:** [`../../ownevo_docs/ownEvo_MVP.md`](../../ownevo_docs/ownEvo_MVP.md) (currently v4.3, 2026-05-03 — hardened by CEO-mode review). Companion benchmark plans live at [`../../ownevo_docs/benchmarks/`](../../ownevo_docs/benchmarks/). Competitive framing lives at [`../../ownevo_docs/competitors/code-gen-loop-landscape.md`](../../ownevo_docs/competitors/code-gen-loop-landscape.md).

This doc is the executable derivation — what to build, in what order, with what validates each step. When the two conflict, the MVP doc wins; update this one.

*Last updated: 2026-05-12 (v3.27 — W8 Track 4 rows 8.4.7 + 8.4.8 shipped on `feat/real-ui-loop` (PR #85, 9 commits); TODO-36 → TODO-43 closed in the same branch. Beyond plan: operator shell, workspace traces, Connect on-ramp, agent-rationale plumbing. See [Version log](#version-log) for the v3.27 summary.)*

---

## Review decisions applied (2026-05-03 — MVP v4.3)

This plan now reflects six decisions ratified by a CEO-mode review of the MVP doc. Index here; each is also inlined in the relevant section below.

| # | Decision | Effect on this plan |
|---|---|---|
| **D2** | Cut "tamper-evident" from sovereignty pitch; reframe as append-only audit log, customer-controlled export | W2.4 audit trail simplified to append-only WORM (Postgres `REVOKE UPDATE, DELETE`); Risk #9 updated; Phase 2 retrofit checklist queues crypto upgrade |
| **D3** | Local Docker for MVP sandbox (was: Modal recommended) | Phase 0 Locked: sandbox = local Docker. W1.3 wraps local Docker with hardening checklist + explicit failure semantics |
| **D4** | Single-tenant for MVP. Full retrofit before customer #2 | Phase 0 Locked changed; W1.4 RLS test removed; Risk #7 reframed as accepted-cost retrofit |
| **D5** | τ³ demo: B-frame head-to-head (autonomous + gated, explain the gap) | North Star storyboard 0:50-1:05 reframed; W8.3.1 framing updated; soft-result fallback documented |
| **D6** | `core/` reuse: 2-day spike with hard cutoff at start of W1 | W1 split into days 1-2 (spike) and days 3-5 (substrate) |
| **D7** | NL-gen meta-eval: full LLM-as-judge with its own eval set, validated W4-W5 | New deliverables A4.6 (meta-eval spec) and A5.5 (meta-eval validated); W4 exit criterion expanded |

**Build-now substrate items added by the review** (each inlined in the relevant week):
1. Audit log WORM enforcement (W1)
2. Sandbox hardening checklist (W1)
3. Sandbox failure semantics (W1)
4. `core/` lift spike with hard cutoff (W1 days 1-2)
5. Gate self-test harness (W2)
6. Loop-stuck alerting (W2)
7. Reproducibility CI (W3)
8. NL-gen output schema freeze at end of W3 (W3)
9. Demo workspace rollback runbook (W7)

**Phase-2 retrofit checklist queued by the review** (added to "Explicitly NOT in MVP" / Phase 2 section):
1. Multi-tenant retrofit (D4) — 1-2 weeks before customer #2
2. Sandbox provider migration to e2b/Modal (D3) — when local Docker hits resource ceiling
3. Audit chain crypto upgrade (D2) — when first regulated buyer evaluates
4. **`AgentEvent` schema license / public-release / naming** — spec written at [`../packages/trace-format/SPEC.md`](../packages/trace-format/SPEC.md); license + public-release timing + package naming all deferred. MVP doc names Apache 2 as the working assumption but no formal commitment is in code. Revisit when triggered (customer asks the license question, second team needs to depend on the package, OTel Gen AI ask, or strategic decision to publish post-MVP). See [`../packages/trace-format/README.md`](../packages/trace-format/README.md) for trigger conditions and [`../TODOS.md`](../TODOS.md) TODO-4.

---

## Eng-review decisions (2026-05-03)

Followed the CEO review with a /plan-eng-review pass. Locked the execution-level specs that the CEO review left as-strategic. Four decisions ratified and four spec files added.

| # | Decision | Spec landed at |
|---|---|---|
| **D1** | DB schema locked pre-W1 — single source of truth for all 9 tables, FK graph, indexes, WORM trigger on `audit_entries` | [`apps/kernel/migrations/0001_substrate.sql`](../apps/kernel/migrations/0001_substrate.sql) + [`docs/SCHEMA.md`](./SCHEMA.md) (ER diagram) |
| **D2** | Python ↔ TS API contract: OpenAPI 3.1 + SSE event types; generate Pydantic + TS clients | [`docs/api/openapi.yaml`](./api/openapi.yaml) |
| **D3** | Skill retention contract: YAML frontmatter on every skill file; parsed at registry load; consumed by eval generator to produce retention-violation tests | [`docs/SKILL_FORMAT.md`](./SKILL_FORMAT.md) |
| **D4** | Cluster-label LLM eval added to W3 — hand-label 20 M5 clusters; nightly judge-vs-human ≥0.7 | W3 Track B deliverable B3.5 below + TODOS-5 |

**State-machine + integration specs written by the eng review:**
- [`docs/STATE_MACHINES.md`](./STATE_MACHINES.md) — Proposal, Iteration, Workflow state machines + audit-kind mapping (locks W2.5 approval queue scaffold)
- `apps/kernel/src/ownevo_kernel/sandbox/__init__.py` — `SandboxRuntime` Protocol (lock at W1.3): `async def run(skill_id, version_id, args, timeout_s) -> SandboxResult` where `SandboxResult = {stdout, stderr, exit_code, duration_s, error_class: Literal["Timeout"|"OOM"|"Crash"|None], metric_outputs: dict}`
- `apps/kernel/src/ownevo_kernel/eval_runner/inspect_adapter.py` — EvalCase → Inspect Sample/Solver/Scorer mapping (W2 alongside eval_cases schema)
- `packages/trace-format/src/ownevo_format/ui_primitives.py` — discriminated-union Pydantic models for the 8 UI primitives (W3 alongside NL-gen schema freeze)
- `apps/kernel/src/ownevo_kernel/errors.py` — typed exception taxonomy (`GateBlockedRegression`, `GateBlockedNoImprovement`, `GateError`, `ClusteringInsufficientData`, `ClusteringFailed`, `NLGenSchemaError`, `NLGenMetaEvalFailed`, `SkillFormatError`, `SandboxRuntimeError`)

**Build-now substrate items added by the eng review (in addition to CEO review's 9):**

10. **Cluster-label LLM eval** — hand-label 20 M5 clusters; nightly judge-vs-human eval at `apps/kernel/eval_runner/cluster_label_eval/`; target ≥0.7 (~1 day, W3)
11. **LLM-judge stub eval expansion** — from 5 hand-crafted to ~30 hand-labeled (proposal, explanation) pairs with structural-element ground truth; nightly run (~1 day, W5)
12. **Reproducibility CI cache strategy** — 4 cache layers (LLM responses fixture, sandbox image, M5 Postgres volume, skill-version-hash-keyed LightGBM artifacts) (~2-3 days, W3)
13. **Parallel-conditions strategy** — M5 4-way parallel + τ³ 3-way parallel via separate Docker compose stacks; merge in `iterations` table (~2-3 days, W4-W6)
14. **Anti-pattern file-length lint** — CI fails any `apps/kernel/src/ownevo_kernel/` file >400 LOC (~15 min, W1)
15. **Test framework lock-in** — pytest + pytest-asyncio (kernel), vitest (`packages/trace-format/`), Playwright (web E2E). Cypress eliminated.

**Test framework decision:** **Playwright** for web E2E. Cypress mentioned in PLAN.md is dropped. Single E2E framework, supports component + integration + E2E in one tool, faster on local dev.

**Net new W1-W3 work: ~6-8 days.** Combined with CEO review's 6-7 days and meta-eval's 5-7 days, Phase 1+2 has ~17-22 net new person-days of substrate work. Most likely compression target stays W7 customer-skin scope (defer non-demand-prediction "Operate" views).

---

## North star (Week 8 demo)

A 90-second video that hits all three pillars without a slide:

1. **Cold open (0:00-0:08):** M5 lift chart, 30 simulated days compressed. Condition D (loop + approval gate) climbs visibly above condition A (frozen baseline).
2. **Hard cut to NL-gen (0:08-0:25):** A domain expert (Supply Chain VP role, non-engineer) types a workflow description in plain English. ownEvo generates simulator + eval cases + success metric in front of the reviewer.
3. **Loop runs (0:25-0:50):** Failures cluster, system proposes code change with plain-language summary, gate badge shows "passes 47/48 prior eval cases · improves new cluster by 12%", domain expert clicks Approve, append-only audit entry appears (D2), lift chart annotation lands.
4. **τ³ two-bar frame (0:50-1:05) — D5 B-frame head-to-head:** condition B (loop autonomous) ≈ NeoSigma's published +39.3%; condition C (loop + approval gate) climbs alongside, with the gap explained as "the cost of safety." Caption: "Autonomous matches the published number. Gated is what enterprise deploys — with the audit trail exportable on every change." Removes binary outcome risk if condition C lands at +25%.
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
| Multi-tenancy | **Single-tenant for MVP. Full retrofit before customer #2.** (D4) | Demo runs on one workspace; RLS adds zero demo value. Retrofit is a bounded 1-2 week job in the breathing room between YC and customer #2. The "painful to retrofit" argument is correct in absolute terms but the relative bet against ~3-5 W1-W2 days landed on ruthless cuts. |
| Web framework | Next.js App Router (TS) | SSE + WebSocket for real-time gate-run status. |
| Python deps | uv | Already wired in `pyproject.toml`. |
| TS deps | pnpm | Standard for Next monorepos. |
| Agent runtime | Anthropic SDK + Claude Agent SDK (Python) | Wave 1 integration target. |
| Eval harness | Inspect AI | Confirmed in MVP doc. |
| Observability | Langfuse self-hosted + custom OTel spans | Confirmed in MVP doc. |

### New decisions to lock (added in v2 reframe)

| Decision | Recommended choice | Rationale | Block W1 if undecided? |
|---|---|---|---|
| **Sandboxed code execution** | **Local Docker** with hardening checklist (`--network=none`, `--read-only`, `--cap-drop=ALL`, mem/cpu/pids limits, hard timeout, structured stdout/stderr) and explicit failure semantics (`tool_call_result {status: "error", error_class: "Timeout"\|"OOM"\|"Crash"}`) (D3). `SandboxRuntime` interface preserved so Phase 2 can swap to e2b or Modal | Local Docker keeps the MVP off managed-sandbox rate limits and bills; works fine for one team + one demo workspace. Phase-2 migration to e2b/Modal when local hits resource ceiling or first managed customer ships. Pyodide eliminated (can't run LightGBM). | **DECIDED.** |
| **M5 fold strategy** | Held-out window: last 28 days = test fold; 28 prior days = validation fold for gate; everything before = training data agent can use | Mirrors real demand-planning evaluation; matches public M5 methodology. | YES — decide W1 day 1. |
| **τ³ approval mechanism for benchmark runs** | LLM-judge stub (Claude Sonnet) admits proposals if (a) gate passes AND (b) plain-language explanation is coherent. Subset re-run with human approver (founder) for credibility. | Per `benchmarks/tau3-bench.md`. Unattended runs need an automated approver; human subset documents both paths. | No — decide by W6. |
| **Reproducibility rig** | `make m5-replay` and `make tau3-replay` targets; Docker-packaged with cached intermediate artifacts (skill registry snapshots, eval-case snapshots) | <30-minute fresh-checkout repro is a Week-8 success criterion. | No — decide by W7. |
| **Public-results post format** | Immutable markdown files: `benchmarks/m5-results-2026-Q3.md`, `benchmarks/tau3-results-2026-Q3.md` in `ownevo_docs/benchmarks/` | Matches the established `<benchmark>-results-<date>.md` convention. | No — decide by W8. |

### Strategic call deferred — trigger-based, not deadline-based

- **`packages/trace-format/` license / public-release / naming** — canonical spec written at [`../packages/trace-format/SPEC.md`](../packages/trace-format/SPEC.md) (2026-05-03). W1 builds against the spec, internal-use-only. License (Apache 2 working assumption per MVP doc § Open-Core Line; no formal commitment in code), public-release timing, and package naming are deferred until any of: a customer asks the license question, a second team needs to depend on the package, OTel Gen AI working group asks to align, or strategic decision to publish post-MVP. See [`../packages/trace-format/README.md`](../packages/trace-format/README.md) for the full trigger list.

### Other questions to track (do not block W1)

- Managed cloud vs self-host only for design partners — affects `infra/` shape in Phase 3.

---

## Phase 1 — Substrate (Weeks 1-2)

**Goal:** every primitive that **all three MVP pillars** depend on is real, tested, and exercised end-to-end on M5 by the end of Week 2. Nothing in Phase 2+ can start until this lands.

**Why this phase exists:** the natural-language workflow generator (Phase 2 Track A), the M5 benchmark (Phase 2 Track B), and the τ³-bench head-to-head (Phase 3 Track C) all share the same substrate. Building it once and proving it on M5 (the hardest target) means everything downstream just plugs in.

---

### Week 1 — `core/` spike (days 1-2) + Sandboxed exec + skill registry + trace capture + M5 dataset (days 3-5)

#### Days 1-2 — `core/` reuse spike with hard cutoff (D6)

Lift `startup2026/core/agentos_harness/evolution/` into the ownEvo repo as the regression-gate scaffold. Add typed `AgentEvent` to `types.py`; add `regression_gate` action type to `ProposalAction`.

**End of day 2 — go/no-go bar:** the evolution scaffold is wired into `apps/kernel/` AND at least one test passes against the new types.
- **GO** → commit to reuse for the rest of W1-W2; the 377 existing tests carry over with the lift.
- **NO-GO** → abandon the lift; go greenfield for W1-W2 (gate, eval-case format, audit log, approval scaffold built fresh in days 3-5 + W2). No "subject to revision" lingering.

**Outcome (2026-05-03): NO-GO.** See [`docs/SPIKE-RESULT.md`](./SPIKE-RESULT.md). Wholesale lift rejected; the 4-stage Tracker / Reflector / Curator / Proposer Protocol shape carries over as scaffolding (`apps/kernel/src/ownevo_kernel/evolution/__init__.py`); concrete impls land in W2 once gate + clustering pipelines exist. `ProposalAction.regression_gate` (D6) and the typed `AgentEvent` discriminated union both shipped.

#### Days 3-5 — Substrate primitives

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 1.1 | **`packages/trace-format/`** typed `AgentEvent` discriminated union | `packages/trace-format/src/` (Pydantic for Python, Zod-generated for TS) | JSON Schema generated; round-trip test (Python emit → TS parse → Python re-emit identical) |
| 1.2 | **Domain types** | `apps/kernel/src/ownevo_kernel/types.py` — `Skill`, `SkillVersion`, `Iteration`, `EvalCase`, `Trace`, `FailureCluster`, `Proposal`, `Approval`, `AuditEntry`. Single-tenant for MVP per D4 — no `Workspace` type or `workspace_id` columns yet. Schema mirrors MVP doc § "auto-harness → ownEvo (web + database)" mapping. | Pydantic models import + validate; `pytest -k test_types` green |
| 1.3 | **Sandboxed code execution — local Docker (D3)** | `apps/kernel/src/ownevo_kernel/sandbox/` — `SandboxRuntime` Protocol; `LocalDockerSandbox` impl. Hardening: `--network=none`, `--read-only` rootfs + tmpfs `/tmp`, `--cap-drop=ALL`, `--security-opt no-new-privileges`, `--memory` + `--memory-swap` (no swap), `--cpus`, `--pids-limit`, hard timeout. Memory/cpus/pids configurable per-call (gate runner picks 2g / 2 / 512); class defaults `cpus=1.0`, `pids_limit=256`, `tmpfs_size_mb=64`. Structured stdout/stderr capture. Failure semantics: `tool_call_result {status: "error", error_class: "Timeout"\|"OOM"\|"Crash"}` distinct from logical failures (exit 100 = `error_class=None`, agent-owned); gate does NOT advance best-ever on `error_class != None`. **TODO-17 hardening shipped (PR #9, 2026-05-03):** runner now executes user code as a subprocess (`subprocess.run([sys.executable, '/sandbox/user_code.py'])`); the runner's exit code is derived from a fixed policy over the child's returncode (0 → 0; 1 → 100; 100 → 102=Crash; negative-N → 128+|N|; else passthrough). Closes the `os._exit(100)` user-exception spoof and the same-process attack surface. The `os._exit(0)` case remains a process-boundary limit; defense-in-depth lives at the metric layer (`run_pipeline`'s JSON-output requirement). | Hardening tests live in `apps/kernel/tests/test_sandbox.py` (skipped if Docker unreachable): hello-world success, Python exception (exit 100), wall-clock timeout, OOM via `--memory=10m` allocator, network-isolated socket attempt, RO rootfs write attempt — all return correctly classified `SandboxResult`. LightGBM smoke deferred to W2 alongside `metric.py`. |
| 1.4 | **Skill-file registry** | `apps/kernel/src/ownevo_kernel/skills/` — `register_skill(content)`, `get_head(skill_id)`, `list_versions(skill_id)`; YAML-frontmatter parser per `docs/SKILL_FORMAT.md` (handles markdown `---` block + Python module-docstring `---` block). Postgres-backed; one transaction per registration with `parent_version_id` linkage and `head_version_id` advancement; `kind` mismatches across versions raise `SkillFormatError`. Single-tenant for MVP (D4). Diff-to-parent stored as `diff_summary text` (caller-supplied for now; structural unified-diff is W2 work alongside the proposal-card UI). | Integration tests in `apps/kernel/tests/test_skill_registry.py`: register v1 → register v2 → `get_head` returns v2 with `parent_version_id == v1.id`; `list_versions` returns both ordered; kind-mismatch rejected. Format unit tests cover both delimiter conventions, retention validation, malformed YAML, non-mapping YAML. (RLS test deferred to Phase-2 multi-tenant retrofit.) |
| 1.5 | **Trace capture pipeline (substrate slice)** | `apps/kernel/src/ownevo_kernel/traces/` — `TraceCollector` + `trace_session` async context manager. Accumulates `AgentEvent` objects in memory; `finalize()` writes the whole stream as one row in `traces.events` (JSONB array) on context exit, including on exceptions (failing iterations still produce traces for clustering). `make_event` validates against the discriminated union; `record` rejects `trace_id` mismatches. **Deferred to W2-W3:** Claude Agent SDK middleware adapter, OTel collector wiring, Langfuse UI, ClickHouse / per-event row migration. | Integration tests in `apps/kernel/tests/test_trace_collector.py` (10 tests, skip if no DB): events persist in order, round-trip through `AgentEventAdapter`, finalize fires on exception, idempotent finalize, empty-session persists `events == []`, validation rejects unknown types / missing fields. |
| 1.6 | **M5 dataset loader (path + shape only)** | `apps/kernel/src/ownevo_kernel/datasets/m5.py` — `load_m5(data_dir)` discovers the 4 CSVs and surfaces per-file metadata (columns, row counts) plus `date_range()` from `calendar.csv`. `make_sample_subset(catalog, num_items=)` slices an in-memory subset for fast eval-gate cycles using stdlib `csv`. **Pandas stays out of the kernel** — agent code in the sandbox brings its own. **No Kaggle downloader** (user drops the 4 CSVs into `data_dir`; loader raises `M5DatasetError` with the missing filename). **Deferred to W2.6:** `metric.py` (RMSE + WRMSSE), held-out fold helper, Day-1 LightGBM baseline. | Unit tests in `apps/kernel/tests/test_m5_loader.py` (8 tests, no DB needed): discovery, missing-file error message, sample subsetting, calendar date-range. |
| 1.7 | **`infra/docker-compose.yml`** brings up local stack | Postgres 16 + pgvector. Migrations auto-applied on first init via `docker-entrypoint-initdb.d`. Host port via `OWNEVO_PG_PORT`; data volume `ownevo-pg-data` (`docker compose down -v` re-bootstraps). **Deferred to W2-W3 as the trace pipeline expands:** Langfuse, OTel collector, ClickHouse, web. | `docker compose up -d` + `OWNEVO_DATABASE_URL=... uv run pytest` clean on a fresh clone (87 → 93 tests after `/review` pass). |

**Week 1 exit criteria (must all pass):**
- `core/` spike resolved (committed to lift OR committed to greenfield) by end of day 2 (D6). ✅ NO-GO; greenfield, see [`docs/SPIKE-RESULT.md`](./SPIKE-RESULT.md).
- Sandbox kills a runaway script (timeout, OOM) cleanly and records the structured error class. ✅
- M5 dataset loader discovers the 4 CSVs and surfaces metadata + sample subsetting. ✅
- Skill registry round-trip (register → list_versions → get_head) on Postgres. ✅
- Trace capture writes typed `AgentEvent` streams to `traces.events` JSONB. ✅
- `docker compose up -d && OWNEVO_DATABASE_URL=... uv run pytest` clean on a fresh clone. ✅ (93 tests).

**Moved to W2 (originally planned for W1, deferred when OTel/Langfuse and metric.py turned out to be downstream of W2 work):**
- Hello-world skill executes end-to-end through `sandbox → registry → metric` (needs `metric.py` + the agent-tool surface from W2.1 to wire the modules together).
- τ-bench reference agent emits `AgentEvent`s through the Claude Agent SDK middleware (needs middleware adapter, lands W2 alongside the eval runner).
- M5 baseline RMSE deterministic across runs (needs `metric.py` — added to W2.6 scope).
- Langfuse + OTel collector (lands when middleware lands; W2.5-W3).

---

### Week 2 — Loop primitives + M5 baseline runs end-to-end

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 2.1 | **Coding-agent tool surface** ✅ | `apps/kernel/src/ownevo_kernel/agent_tools/` — `read_skill(conn, skill_id)`, `write_skill(conn, skill_id, content, created_by=)`, `run_pipeline(sandbox, skill_content=, input_data=, timeout_seconds=, memory_mb=, task_timeout_seconds=)`, `read_metrics(conn, trace_id)`, `analyze_failures(conn, workflow_id=, k=10)`. Train/test discipline enforced: both read tools block `fold == "test"` traces by default; `include_test_fold=True` is gate-runner-only. **Claude Agent SDK middleware adapter** ✅ shipped at `apps/kernel/src/ownevo_kernel/middleware/claude_sdk/` (PR #16 in flight): `tool_definitions.py` + `event_router.py` + `runner.py` wire the 5 kernel tools into Anthropic's Messages API as a manual agentic loop (over `client.messages.stream`, not `tool_runner` — per-token AgentEvents are required by the trace contract), emit `content_delta` / `reasoning_delta` / `tool_call_start` / `tool_call_result` events into a `TraceCollector`, and short-circuit on sandbox-runtime tool errors so the gate's D3 invariant holds end-to-end. `anthropic>=0.95,<1` ships as the new `agent` extra. Defaults: model `claude-opus-4-7`, `max_tokens=64000`, `max_iterations=25`; adaptive thinking + `effort="xhigh"` are opt-in kwargs. | Unit test per tool ✅; SDK middleware exercised by 17 router/runner unit tests in `test_middleware_claude_sdk.py` (script-driven fake AsyncAnthropic — no DB / sandbox / network). End-to-end agent-reads-writes-runs-reads integration test against the live API lives behind `OWNEVO_ANTHROPIC_LIVE` (W4). |
| 2.2 | **3-step regression gate** ✅ (core function ✅ PR #8; persistence wrapper ✅) | `apps/kernel/src/ownevo_kernel/gate/` — `run_gate(runner, *, prior_eval_task_ids=, best_ever_score=, regression_tolerance=, improvement_epsilon=)` is a pure async function over the `BenchmarkRunner` Protocol; returns a structured `GateResult` (PASS / FAIL_REGRESSION / FAIL_NO_IMPROVEMENT / SANDBOX_ERROR) with `val_score`, `failed_prior_task_ids`, and `promotable_task_ids`. Steps: (1) every prior task scores ≥ `1.0 - regression_tolerance`; empty prior suite → step skipped (Day-1 bootstrap rule). (2) val_score must exceed `best_ever_score + improvement_epsilon`; `best_ever=None` → step skipped. (3) tasks that passed at threshold and were not in the prior suite are surfaced as `promotable_task_ids` for the caller to wire into `add_eval_case`. D3 sandbox-error short-circuit: any None reward → SANDBOX_ERROR without trusting val_score. `GateDecision` values are wire-compatible with `IterationState`. **DB-write wrapper shipped:** `persist_gate_run(conn, runner, *, workflow_id, skill_id, proposed_content, plain_language_summary, actor, ...)` runs the gate inside one transaction, locks the workflow row (`SELECT … FOR UPDATE`) so concurrent runs don't collide on `UNIQUE(workflow_id, iteration_index)`, INSERTs the iteration + proposal, appends `gate-run-started` + `gate-run-completed` audit entries, and finalizes both rows with the gate's decision (val_score, best_ever_score_after, sandbox_error_class, ended_at; proposal state → gate-passed / rejected (logical gate failures) / gate-failed (sandbox infra errors, per STATE_MACHINES.md)). Promotable eval cases are surfaced for the caller to wire into `add_eval_case` — the wrapper does not auto-promote (cluster→eval-case lift is W3 work). 9 DB-backed integration tests pin every decision path + transaction rollback. Background job + SSE streaming is a separate slice. | Integration test: write change that improves on new case but breaks an old case → gate rejects. Write change that improves both → gate accepts and promotes new cases. Train/test discipline: gate refuses to use test-fold rows for training. |
| 2.2a | **Gate self-test harness** ✅ (PR #8 in flight) | `apps/kernel/tests/gate_self_test/` — 5 synthetic scenarios pin the gate-trust contract: known-good admitted, known-bad regression blocked, no-improvement blocked, adversarial higher-aggregate-but-regresses-prior blocked (the failure mode val_score-alone would silently admit), crashing skill blocked. In-process via `SyntheticBenchmarkRunner` — no Docker, no DB, no LLM — so the failure mode being detected is purely "the gate logic is broken," not substrate flakiness. Picks up automatically under `pytest`; failing the harness fails the build. | CI green: known-bad blocked, known-good admitted; failing this harness fails the build. |
| 2.3 | **Eval-case format + table** ✅ (PR #3) | `apps/kernel/src/ownevo_kernel/eval_cases/` — `add_eval_case(conn, provenance=, input=, expected_behavior=, ...)` returns the typed `EvalCase`; `get_eval_case(conn, id)` fetches one by id; `list_eval_cases(conn, workflow_id=, provenance=, is_test_fold=, cluster_id=)` filters and orders by `created_at` ASC so the gate fail-fasts on older (more load-bearing) cases first. Schema migrated in `0001_substrate.sql`. Single-tenant for MVP per D4. | Schema migration runs; insert + query roundtrip; eval-case provenance preserved through gate runs. |
| 2.4 | **Append-only audit log** (D2 — was hash-chained) ✅ (PR #3) | `apps/kernel/src/ownevo_kernel/audit/` — `audit_entries` table with `id`, `kind`, `payload` (JSONB), `seq`, `actor`, `related_id`, `created_at`. WORM-enforced at the DB level via row-level UPDATE/DELETE triggers + statement-level TRUNCATE trigger. `append_audit_entry(conn, kind=, payload=, actor=, related_id=)` writes; `export_audit_log(conn, since_seq=, kind=)` reads in `seq` order; `to_canonical_json` serializes sorted-keys + no-whitespace + UTF-8 (bytes are the contract). **Crypto-grade tamper-evidence** is a **Phase-2 retrofit** per TODO-3. | Integration test: append 3 entries; export returns 3 entries in canonical-JSON. Negative test: app role's UPDATE/DELETE/TRUNCATE on audit_entries raises permission denied. Export → re-import → all entries present. |
| 2.4a | **Loop-stuck alerting** (added by review) ✅ (PR #10) | `apps/kernel/src/ownevo_kernel/observability/` — `write_learning(conn, kind=, content=, iteration_id=)` + `latest_learning(conn)` for the append-only learnings memory. `LoopStuckAlerter(webhook_url=, idle_threshold_seconds=2h, http_post=)` reads the latest learning and fires a Slack webhook if the gap exceeds the threshold; returns a structured `StuckSignal`. `webhook_url=None` is observe-only; `now=` is injectable so tests fast-forward without sleeping. Stdlib HTTP via `asyncio.to_thread(urllib.request.urlopen)` (no `httpx` / `aiohttp` dep added). Catches "best-ever stuck" / agent-spinning-on-rejected-proposals failure mode flagged by the CEO review. | Integration test: simulate stuck state via `now=` 10 minutes past a seeded learning → webhook fires; `webhook_url=None` → observe-only signal still computed; payload shape `{"text": "..."}`; humanized duration in the summary (h/m/s). |
| 2.5 | **Approval queue UI scaffold** ✅ | Three new modules. `apps/kernel/src/ownevo_kernel/approvals/` — `approve_proposal` / `reject_proposal` drive the `gate-passed → approved-awaiting-deploy` and `gate-passed → rejected` transitions per `docs/STATE_MACHINES.md` (row-locks the proposal, validates state, INSERTs `approvals`, advances `proposals.state`, appends audit; reject + non-empty comment seeds an eval_case provenance=`rejected-feedback` and links via `approvals.became_eval_case_id`). `apps/kernel/src/ownevo_kernel/api/` — FastAPI service (new `api` extra: `fastapi`, `uvicorn[standard]`, `httpx`) exposing `GET /api/proposals` (state + workflow filter, total count), `GET /api/proposals/:id` (joined detail with iteration, workflow, audit chain, approval, parent skill version for diff), `POST /api/proposals/:id/approve`, `POST /api/proposals/:id/reject`, `GET /api/health`; CORS allows `localhost:3000` for the Next.js dev server; errors map cleanly (404/409/422). `apps/web/` — Next.js 15 App Router scaffold (TypeScript, Server Components for reads + Server Actions for mutations) with two routes: `/inbox` (proposal queue with pending vs decided groupings, state pills) and `/proposals/[id]` (header, line-level skill diff via in-process LCS, gate-result sidebar, expected-impact grid, reviewer panel with Approve/Reject + comment textarea, audit chain). CSS lifted from `www/preview/s26-rk7p3/` (`shell.css` + `primitives.css`, dark-mode toggle). New `apps/kernel/scripts/seed_approval_demo.py` + `make seed-approval-demo` insert one `gate-passed` proposal mirroring the `07-proposal-detail.html` mock copy for manual click-through. Make targets `api` / `web-dev` / `web-build`. **MVP approval surface only** — Slack/email digests, SLA tracking, time-delayed deploy, severity-based auto-approve, audit chain page, multi-workflow nav, authentication are post-MVP / W5 polish / D4 retrofit. | 13 DB-backed integration tests on the approval service (`test_approvals.py`) covering every transition + state validation + comment-to-eval-case linkage + double-decide protection. 15 in-process FastAPI tests (`test_api_proposals.py` via `httpx.ASGITransport`) covering endpoints + status codes + filter combinations. Manual E2E verified: `make api` + `make web-dev` + `make seed-approval-demo` → click `/inbox` → Approve → `approved-awaiting-deploy` + audit entry written; double-approve → 409. Playwright smoke deferred to the same CI job that picks up `test_substrate_non_m5.py` (the cross-cutting "tests that need DB+Docker" gap flagged in the W2.7 review). ✅ |
| 2.6 | **M5 baseline pipeline runs end-to-end** (PR #4 metric foundation ✅; PR #11a scaffolding ✅; PR #11b LightGBM bodies in-process ✅; PR #11c sandbox flip ✅; PR #11d reproducibility nightly ✅) | (Deferred from W1.6) `apps/kernel/src/ownevo_kernel/datasets/m5_metric.py` implements RMSE + WRMSSE per the M5 paper; `make_held_out_fold(catalog, val_days=28, test_days=28)` carves the train / val / test split per Phase 0's lock; refuses zero-scale series. `apps/kernel/src/ownevo_kernel/benchmark/` — `BenchmarkRunner` Protocol + `BenchmarkResult` + `SyntheticBenchmarkRunner` for the W2.2a self-test. **Shipped in PR #11a:** `M5BenchmarkRunner` over `(catalog, fold, pipeline_fn)` with per-series reward `exp(-RMSSE_i)`; 6 SKILL_FORMAT-compliant skill files at `apps/kernel/baselines/m5_lightgbm/skill_v1/` (`data_loader`, `outlier_handler`, `feature_engineer`, `model_trainer`, `predictor`, `ensemble`); in-process orchestrator; `scripts/m5_baseline.py` + `make m5-baseline` end-to-end; DB-write path registers skills idempotently (skips re-registration when body is unchanged) and appends an `iterations` row at `MAX(iteration_index)+1`. **Skill bodies in PR #11b**: real LightGBM regressor over a long-format (series, day) frame with `lag_28` + `day_of_week` + `cat_id_code` (encoded categorical) features; 100 boosting rounds; pinned `seed`/`bagging_seed`/`feature_fraction_seed`/`data_random_seed` + `num_threads=1` + `deterministic=True` for bit-identical runs. Train fold = `validation_actuals` (lag-28 reaches into train); test fold = `test_actuals` (lag-28 reaches into validation). Predictions clipped to ≥0 (sales are non-negative). On the synthetic 5-series fixture: WRMSSE 0.988 → 0.777 (−21%), RMSE 4.06 → 3.17 (−22%) vs seasonal-naive. Kernel deps unchanged at runtime — `lightgbm` + `pandas` ship via the new `baselines-m5` extra. **Shipped in PR #11c:** `apps/kernel/sandbox/Dockerfile.m5` bakes pinned `numpy`/`pandas`/`lightgbm` + libgomp1 + kernel + baselines into `ownevo-sandbox-m5:0.1.0`; `make sandbox-image-m5` builds it. `LocalDockerSandbox.run` gains a privileged `extra_volumes` kwarg (kernel-internal — agents calling `run_pipeline` should not set it) for read-only bind-mounts; validation rejects `/sandbox` collisions + relative paths + missing hosts. `SandboxedM5BenchmarkRunner` (in `benchmark/m5_sandbox.py`) drives the orchestrator through `run_pipeline` against the M5 image — catalog dir mounted at `/data/m5`, fold marshaled via JSON-global, predictions parsed back from stdout's last line. Sandboxed predictions are bit-identical to the in-process path under matched pins; `tests/test_baselines_m5_lightgbm_sandboxed.py` pins parity + determinism + subset scoping (4 tests, skip when Docker or image missing). `scripts/m5_baseline.py` gains `--sandbox` (or `OWNEVO_M5_SANDBOX=1`); default stays in-process so CI without Docker stays green. **Shipped in PR #11d:** `.github/workflows/m5-replay-nightly.yml` runs the sandboxed parity + determinism suite on cron (04:00 UTC daily), `workflow_dispatch`, and pushes to main that touch M5 / sandbox paths. Buildx + GHA cache scoped to `m5-sandbox` (TODO-7's cache layer (b)); uv install cached on `uv.lock`. Concurrency cancels in-flight on retrigger; 30-min timeout per B3.4 budget. The other TODO-7 cache layers — (a) LLM responses, (c) Postgres data volume, (d) LightGBM artifacts — stay deferred until W4/W6. The agent (W4) iterates the same 6-file split — feature additions (lag-7, rolling means, prices), tuning (depth, leaves, rounds), and ensemble layering are natural first diffs once the SDK middleware adapter lands. | `pytest -k test_m5_metric` confirms RMSE/WRMSSE on a fixture matches a known reference value within 1e-6. ✅. `pytest tests/test_benchmark_m5.py tests/test_baselines_m5_lightgbm.py` covers runner Protocol conformance + per-series reward formula + orchestrator determinism (17 tests, no DB/Docker required) ✅ (PR #11a). `make m5-baseline` writes 6 skill files (v1) + records baseline RMSE in `iterations` table ✅ (PR #11a; DB path skipped when `OWNEVO_DATABASE_URL` is unset). RMSE is reproducible across two runs to within numeric tolerance ✅ (`test_run_baseline_is_deterministic` asserts bit-identical predictions). |
| 2.7 | **Substrate proves itself on a non-M5 task** ✅ | `apps/kernel/baselines/labour_v1/skill.py` — rule-based shift validator (weekly-hours cap + required-skill check, drawn from the Labour management failure-mode taxonomy in `ownEvo_MVP_mocks.md`). `apps/kernel/src/ownevo_kernel/benchmark/labour.py` — `LabourBenchmarkRunner` over a fixed list of `LabourCase`s; one `run_pipeline` call per `run()` (batched, like M5). `apps/kernel/tests/test_substrate_non_m5.py` — iteration-1 smoke: `register_skill` → `add_eval_case` × 3 (write-only on bootstrap) → `LabourBenchmarkRunner` over `LocalDockerSandbox(image="python:3.11-slim")` → `persist_gate_run` (with `prior_eval_task_ids=()`) → asserts gate `PASS` (val_score=1.0), 3 `promotable_task_ids`, iteration `gate-pass`, proposal `gate-passed`, both `gate-run-started` + `gate-run-completed` audit entries linked to the iteration. The skill is stdlib-only — no domain-specific Dockerfile required (part of the proof). **Scope: iteration-1 proof only.** The eval→gate seam where stored `eval_cases` drive `prior_eval_task_ids` on iteration 2+ is W3+ work; this row covers the iteration-1 substrate composition (skill registry → sandbox → gate → audit, with `eval_cases` CRUD persisted alongside). **Post-B3.3 follow-up:** re-run this smoke with `prior_eval_task_ids` populated from a degraded skill_content variant to prove the eval→gate seam on a non-M5 workflow (closes the iteration-2 regression path on labour the way B4.3 closes it on M5). | Smoke test passes end-to-end (`pytest test_substrate_non_m5.py` — skipped when DB or `python:3.11-slim` image is missing). Confirms iteration-1 substrate composition is domain-agnostic before Phase 2 starts. ✅ |

**Week 2 exit criteria (must all pass):**
- An agent-proposed change can be written, gated, approved (or rejected), and recorded in the append-only audit log end-to-end on M5 — proven by integration test.
- Gate self-test harness (2.2a) green in CI: known-bad blocked, known-good admitted.
- The same primitives work on a non-M5 hand-written sim — proven by 2.7's smoke test.
- M5 Day-1 baseline RMSE is recorded and reproducible.
- All `pytest` and Playwright smoke tests pass on a fresh clone.

**Phase 1 validation gate (must pass before Phase 2 starts):**

Run a fresh-checkout end-to-end smoke test:
1. `docker compose up`
2. `make m5-baseline` — agent writes 6 skill files, baseline RMSE recorded
3. Run the agent for one cycle: it edits one skill file, gate runs, audit entry appended
4. Open `apps/web/` → see the proposed change in the approval queue → click Approve → audit log has 2 entries (initial + approval)
5. Attempt to UPDATE/DELETE an audit entry as the app role → permission denied (WORM enforcement, D2)
6. Gate self-test (2.2a) blocks a known-bad change and admits a known-good change — full CI run green

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
| A3.1 | **NL → workflow spec** ✅ | `apps/kernel/src/ownevo_kernel/nl_gen/` — `spec.py` (frozen-schema `WorkflowSpec` Pydantic discriminated-union: `Provenance` on every artifact, `WorkflowEnvironment` × {entities, data_sources, env_generators, personas, seasonality}, `tools`, `known_past_misses`, `reviewer`, `success_criterion` stub, `ui` block of `UIPrimitive`s; `extra="forbid"` everywhere; `schema_version="0.1"` until A3.4 freeze) + `workflow_spec_generator.py` (single-turn Anthropic tool-use; `tool_choice` forces structured output; `WorkflowSpec.model_json_schema()` becomes the tool's `input_schema`; raises `NoToolUseError` / `WorkflowSpecValidationError`). 3 hand-authored fixtures at `nl_gen/fixtures/{demand_prediction,credit_risk,contract_review}.py` (demand-prediction `description` is verbatim from `www/preview/s26-rk7p3/03-new-workflow-step1.html`). 8 UI primitives lifted from `SPEC.md` into `packages/trace-format/src/ownevo_format/ui_primitives.py` (W3 was already going to land them). | 39 schema-only tests in `test_nl_gen_spec.py` (round-trip identity, `extra="forbid"` enforcement, discriminator coverage, JSON-schema export shape, fixture mock-parity); 8 generator tests in `test_nl_gen_generator.py` (fake AsyncAnthropic — pass / no-tool-use / wrong-tool-name / malformed-input / extra-field-rejected / system-prompt-pinning); 3 live-API snapshot tests gated by `OWNEVO_ANTHROPIC_LIVE=1` (assert structural shape against fixtures, not verbatim text). 11 UI-primitive tests in `packages/trace-format/tests/test_ui_primitives.py`. |
| A3.2 | **WorkflowSpec → simulator** ✅ | `apps/kernel/src/ownevo_kernel/nl_gen/` — `sim_plan.py` (frozen-schema `SimulationPlan` Pydantic discriminated-union: `workflow_spec_id` back-pointer, `description`, `n_steps_default` (≤10k), `seed_default`, `imports` ⊆ `ALLOWED_IMPORTS={random, math, statistics, datetime, json, __future__}`, `init_state_code`, `step_code`, `event_fields`; `extra="forbid"` everywhere; `schema_version="0.1"` until A3.4 freeze) + `sim_render.py` (pure renderer: AST safety pass rejects forbidden imports / `eval`/`exec`/`compile`/`open`/`__import__`/`globals`/`locals`/`vars` / `getattr(_, "__dunder__")` / dunder attribute access; emits SKILL_FORMAT-compliant Python with `random.Random(seed)` as the only RNG and an `if "input_data" in globals():` entrypoint guard so tests can `exec` without auto-printing) + `sim_generator.py` (single-turn Anthropic tool-use; `tool_choice` forces structured output; `SimulationPlan.model_json_schema()` becomes the tool's `input_schema`; raises `NoSimToolUseError` / `SimulationPlanValidationError`; reuses A3.1's `NLGenError` base). 3 hand-authored sim-plan fixtures at `nl_gen/fixtures/sim_plans.py` — one per A3.1 fixture — with non-trivial physics (demand: annual + holiday seasonality + supplier noise + hidden `alert_correct_label`; credit: logistic risk function + hidden `default_label`; contract: 5 clause types + hidden `is_problematic`). | 43 renderer + safety tests in `test_nl_gen_sim_render.py` (parseable Python, `parse_skill` round-trip, byte-identical re-render, frontmatter + capability_tags + retention.stateless, 7 forbidden-import rejections, 8 forbidden-call rejections, dunder access + getattr-to-dunder + inline-import + invalid-Python + body-without-return rejections, plan-level `extra="forbid"` + kebab-case + n_steps cap). 23 replay tests in `test_nl_gen_sim_replay.py` (replay-equivalence × 3 fixtures, different-seeds-diverge × 3, replay-across-fresh-namespaces × 3, expected-event-keys × 3, envelope shape × 3, monotonic step_index × 3, hidden-label presence + bool typing for all 3 fixtures, both-class coverage at default seeds, per-step shape-check trip wires for missing-keys + non-dict returns). 12 generator tests in `test_nl_gen_sim_generator.py` (fake AsyncAnthropic — pass / no-tool-use / wrong-tool-name / malformed-input / extra-field-rejected / flat-input fallback / system-prompt-pinning); 3 live-API snapshot tests gated by `OWNEVO_ANTHROPIC_LIVE=1` (assert structural shape + render + replay equivalence on real-API output). |
| A3.3 | **Sim runs in the sandbox** ✅ | `apps/kernel/tests/test_nl_gen_sim_sandbox.py` exercises the rendered sim under `LocalDockerSandbox` via `run_pipeline` against the default `python:3.11-slim` image — the rendered module is stdlib-only (`random`, `math`, `statistics`, `datetime`, `json`) so no per-domain Dockerfile is required, the same property exercised by the W2.7 non-M5 substrate proof. Renderer fix (this PR): dropped the `from __future__ import annotations` line because `run_pipeline` prepends a 2-line prologue (`import json as _ownevo_json; input_data = ...`), which would push the future-import off the file's beginning and trip `SyntaxError`. The rendered functions don't use forward-reference type hints so no future-import is needed. | 13 docker-gated tests in `test_nl_gen_sim_sandbox.py` (skipped when daemon unreachable so unit-only CI stays green): runs end-to-end × 3 fixtures (status="ok", outputs JSON-parsed, `error_class=None`, expected event keys present), sandbox replay-equivalence × 3 (two sandbox runs at same seed produce byte-identical canonical JSON), different-seeds-diverge × 3, in-process / sandbox parity × 3 (sandbox output equals the in-process exec output — pins that determinism holds across the container boundary), default-input handling (no `input_data` → uses plan's `seed_default` / `n_steps_default`). |
| A3.4 | **NL-gen output schema FROZEN at end of W3** ✅ (added by review) | Bumped `WorkflowSpec.SCHEMA_VERSION` and `SimulationPlan.SCHEMA_VERSION` from `"0.1"` → `"1.0"`; matching `Literal["1.0"]` annotations; trace-format `SPEC.md` + `agent_event.py` docstring updated. Snapshots checked in: `apps/kernel/src/ownevo_kernel/nl_gen/schemas/{workflow_spec,simulation_plan}.v1.0.json` (sorted-keys + 2-space indent + trailing newline canonical form, ~22kb + ~4kb) and `packages/trace-format/schemas/{agent_event,ui_primitives}.v1.0.json` (~16kb + ~7kb). Regen scripts at `apps/kernel/scripts/regen_nl_gen_schemas.py` and `packages/trace-format/scripts/regen_schemas.py`. Tag `v1.0-frozen-2026-W3` is created at the next release commit (kept out of this PR so the tag points at a green main). | 6 freeze tests in `apps/kernel/tests/test_nl_gen_schema_freeze.py` (SCHEMA_VERSION constants pinned, live `model_json_schema()` ↔ snapshot diff with helpful unified-diff failure mode pointing at the regen command, fixture round-trip pinned at v1.0 for both WorkflowSpec and SimulationPlan). 2 freeze tests in `packages/trace-format/tests/test_schema_freeze.py` (AgentEvent + UIPrimitive). All existing tests carrying `"0.1"` literals updated to `"1.0"`. |

**Week 3 exit criterion (Track A):** at least one hand-picked workflow has a generated sim that runs deterministically in the sandbox; NL-gen output schema frozen and tagged.

#### Week 4 — NL → eval cases + metric, validate on 3 workflows (Track A)

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| A4.1 | **NL → eval case set** ✅ | New module surface in `apps/kernel/src/ownevo_kernel/nl_gen/`: `eval_case_set.py` (frozen-schema `EvalCaseSet` + `GeneratedEvalCase` Pydantic discriminated-union; `extra="forbid"` everywhere; size 10-30; back-pointer + unique-id + balanced-classes (≥3 True / ≥3 False) validators; `schema_version="0.1"` until the A4-end freeze) + `eval_generator.py` (single-turn Anthropic tool-use; `tool_choice` forces structured output; `EvalCaseSet.model_json_schema()` becomes the tool's `input_schema`; raises `NoEvalToolUseError` / `EvalCaseSetValidationError`; pre-flight rejects mismatched `simulation_plan.workflow_spec_id` before hitting the model) + `eval_replay.py` (in-process replay seam: renders the `SimulationPlan` via existing `sim_render`, exec's the body in a fresh namespace, drives `run_simulation(seed, n_steps)`, reads `trajectory[target_step_index][target_label_field]`, emits a typed `ReplayResult`; rejects non-bool / unknown label fields with `EvalReplayError` distinct from pass/fail signal) + `eval_persistence.py` (`persist_eval_case_set` calls `add_eval_case(provenance=NL_GEN, ...)` per case under one transaction; splits each case into `input={sim_seed, n_steps, target_step_index}` / `expected_behavior={case_id, target_label_field, expected_value, rationale, provenance}`; respects per-case `is_test_fold`). 3 hand-authored fixtures at `nl_gen/fixtures/eval_case_sets.py` (12 cases each for demand-prediction / credit-risk / contract-review; every `known_past_misses` phrase covered by a derived case; ground-truth `expected_value`s computed against the matched A3.2 sim plans on 2026-05-05). | 34 schema tests in `test_nl_gen_eval_spec.py` (round-trip identity, `extra="forbid"`, size bounds, step_index < n_steps, back-pointer agreement, unique case_ids, kebab-case pattern, balanced-classes ≥3/≥3, fixture round-trip + structural checks + past-miss coverage). 14 generator tests in `test_nl_gen_eval_generator.py` (tool-definition shape, system-prompt rule pinning, fake AsyncAnthropic — pass / no-tool-use / wrong-tool-name / malformed-input / extra-field-rejected / one-class-rejected / mismatched-spec-vs-plan; flat-input fallback; live-API replay-equivalence × 3 fixtures gated by `OWNEVO_ANTHROPIC_LIVE=1`). 13 replay tests in `test_nl_gen_eval_replay.py` (fixture all-pass × 3, deterministic two-run identity × 3, in-order results × 3, inverted-expected_value all-fail, unknown-label-field error, non-bool-label-field error, end-of-trajectory bound). 9 DB-backed persistence tests in `test_nl_gen_eval_persistence.py` (one-row-per-case × 3 fixtures, NL_GEN provenance pinned, input/expected_behavior payload shapes, test_fold flag propagation + filter, workflow_id override, FK-failure rollback). All 70 new tests green; 214/214 nl_gen tests pass; 629/629 kernel tests pass. |
| A4.2 | **NL → success metric** ✅ | `apps/kernel/src/ownevo_kernel/nl_gen/metric_generator.py` — workflow spec → metric definition (precision/recall, threshold, etc.). | Metric runs over generated eval cases; returns float in expected range. |
| A4.3 | **Inspect AI integration** ✅ | `apps/kernel/src/ownevo_kernel/eval_runner/` — generated eval cases → Inspect AI task. Single command: replay an agent → score. | `make eval-replay WORKFLOW=demand-prediction` runs the loop end-to-end and emits a score. |
| A4.4 | **Validate on 3 workflows end-to-end** ✅ | Supply chain demand forecast + credit risk + contract review. Each must produce a working sim + eval set + metric that a Claude agent runs and Inspect AI scores. | All 3 workflows pass `make nl-gen-smoketest WORKFLOW=<name>`. **If even one fails, slip Phase 2.** |
| A4.5 | **Cost + determinism guardrails** ✅ | Fixed token budget per eval replay (Karpathy pattern); nondeterministic eval failures flagged as bugs. | Token budget exceeded → run aborts cleanly. Repeat eval-replay → identical score (within numeric tolerance). |
| A4.6 | **NL-gen meta-eval spec authored** ✅ (D7 — added by review) | `apps/kernel/src/ownevo_kernel/nl_gen/meta_eval/` — full LLM-as-judge with its own eval set. `judgment.py` (frozen `MetaEvalJudgment` Pydantic schema; 3 dimensions × `{pass, partial, fail}` + binary `{good, bad}` overall + per-dimension rationale; `extra='forbid'`, `frozen=True`, `schema_version='0.1'` until A4-end freeze). `judge.py` (`judge_artifacts(client, description, spec, plan, case_set, metric) → MetaEvalJudgment` via single-turn Anthropic tool-use; mirrors `metric_generator`'s shape; default model opus 4.7 — calibration anchor for the W5 ≥0.7 agreement gate; long `step_code` truncated to 4kB in prompt; raises `NoMetaEvalToolUseError` / `MetaEvalJudgmentValidationError` / `MetaEvalSpecIdMismatchError`). `corruptions.py` (6 recipes that take a good bundle and produce a structurally-valid but semantically-wrong variant: `swap_sim_plan`, `swap_eval_cases`, `swap_metric_family_to_opposing`, `set_unreachable_threshold`, `set_trivial_threshold`, `flip_metric_direction`; each tagged with target dimension). `fixtures/minimal_fixtures.py` (7 new minimal-but-Pydantic-valid good bundles via a compact `_FixtureSpec` → bundle helper: supplier-late-shipment-risk, fraud-card-decline-review, clinical-trial-eligibility, insurance-claim-triage, hr-policy-violation-review, content-moderation-escalation, manufacturing-defect-detection — domains span supply-chain / credit-risk / legal-adjacent / support / labour / other so the judge has to read the description). `eval_set.py` (`META_EVAL_SET`: 10 (description, good, bad, ground-truth) pairs joining 3 production fixtures + 7 minimal; every corruption recipe used at least once). `runner.py` (`run_meta_eval(client, ...) → MetaEvalReport`; configurable concurrency; aggregates judge-vs-human agreement + per-dimension verdict distribution + per-recipe correctness). `scripts/meta_eval.py` + `make meta-eval` (CLI; `--require-agreement N` is opt-in for A4.6, wired into CI as `0.7` in W5). | Eval set authored: 10 descriptions × {good, bad} pairs (≥10 threshold met); every corruption recipe exercised at least once. Judge runs on the eval set end-to-end and emits per-dimension breakdown + aggregate agreement; CLI exit semantics 0/1/2 mirror the other scripts. 101 new tests (`test_nl_gen_meta_eval_schema.py` 21, `test_nl_gen_meta_eval_judge.py` 26, `test_nl_gen_meta_eval_corruptions.py` 13, `test_nl_gen_meta_eval_eval_set.py` 13, `test_nl_gen_meta_eval_runner.py` 13, `test_scripts_meta_eval.py` 15). Full kernel suite: 885 passed, 170 skipped. **Live agreement validation (≥0.7) is W5 (A5.5).** |

**Week 4 exit criterion (Track A):** plain-English description in → working sim + eval set + metric out, validated on all 3 workflows; meta-eval spec + judge eval set authored. **The single most important quality gate of the whole MVP.**

---

### Track B — M5 code-gen-loop benchmark (credibility test, runs in parallel)

#### Week 3 — Failure mining on M5 (Track B)

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| B3.1 | **`analyze_failures` on M5 misses** ✅ | `apps/kernel/src/ownevo_kernel/benchmark/m5_failure_analyzer.py` — `analyze_m5_failures(artifacts, fold=, k=)` ranks M5 series by RMSSE descending and emits structured `M5FailureSnapshot`s: parsed hierarchy (item/dept/cat/store/state via deterministic series-id regex; no CSV re-reads), per-series RMSSE + reward, mean actual/predicted, peak-error day offset + signed value (`pred − actual`, sign communicates over- vs under-forecast), and `feature_gap_hints` (`under-forecast`, `over-forecast`, `zero-inflated`, `high-variance`, `flat-prediction` — descriptive tags derived from numerical patterns, not feature importance). `text_signature` is the single-line embedding input for B3.2. Pure-numpy + stdlib. | 25 unit tests (parser canonical / evaluation / no-suffix / 6 malformed forms; top-k descending + tie-break by series_id ASC; peak-offset + signed-value correctness; fold-attached `peak_error_day_label`; size-mismatch rejection; all 5 hint tags; signature contains hierarchy + bias direction; reward aligned with artifact). |
| B3.2 | **Failure clustering pipeline** ✅ | `apps/kernel/src/ownevo_kernel/clustering/` — 4-stage pipeline behind `Embedder` / `Reducer` / `Clusterer` / `Labeler` Protocols. `cluster_failures(snapshots, embedder=, reducer=, clusterer=, labeler=, thresholds=)` runs embed → reduce → cluster → quality-gate → label → summarize; pipeline is the snapshot-list owner so clusters can be ordered deterministically by label and centroids pinned to `EMBEDDING_DIM=384` (matches schema `vector(384)`). `quality.gate_assignment` enforces three failure modes BEFORE the LLM labeler is paid for: `too-few-points` (n < 5 inputs), `all-noise` (every point labelled -1), `mega-cluster` (any cluster owns > 90% of non-noise points when there are 2+ clusters). Singleton-or-smaller clusters drop silently; survivors get severity (`high`/`medium`/`low`) from cluster_size + mean RMSSE + total cluster count. `INSUFFICIENT_DATA` returns a typed result rather than raising — caller logs and the UI surfaces a "more iterations needed" state. `persist_clustering_result(conn, *, workflow_id, result, source_trace_ids=)` writes one `failure_clusters` row per cluster under one transaction (centroid serialized as a pgvector literal — asyncpg has no native codec; the kernel doesn't take a `pgvector` dep just to write 384 floats). Production wiring lives in `clustering/default_impl.py` (lazy `SentenceTransformerEmbedder` for all-MiniLM-L6-v2 / `UMAPReducer` n_components=8 / `HDBSCANClusterer` min_cluster_size=3 / `AnthropicLabeler` claude-haiku-4-5 for cheap labels) gated on the new `clustering` extra (`sentence-transformers`, `umap-learn`, `hdbscan`); kernel core stays free of these deps and unit tests stub the Protocols. | 38 tests (20 pipeline happy-path / quality-gate paths / sample-cap / label-fallback / validation guards; 13 quality-direct severity + insufficient-data; 5 DB-backed persistence round-trip + multi-cluster transaction + schema-`vector(384)` rejection + INSUFFICIENT_DATA no-op). |
| B3.3 | **Cluster → eval case** ✅ | `apps/kernel/src/ownevo_kernel/eval_cases/from_cluster.py` — `promote_cluster_to_eval_cases(conn, *, workflow_id, cluster, snapshots, max_cases_per_cluster=5, min_reward_floor=0.30, regression_tolerance=0.05, is_test_fold=False)` and the batch sibling `promote_clusters_to_eval_cases` write one row per worst-RMSSE cluster member under one transaction with `provenance=CLUSTER_DERIVED` and `cluster_id` set. Per-case input payload: `{task_id, series_id, fold:"test", feature_gap_hints}`; expected_behavior: `{min_reward, rmsse_at_promotion, reward_at_promotion, rationale (cluster.label), cluster_severity, peak_error_value, peak_error_day_offset}`. `min_reward_floor=0.30` is the lenient default — cluster cases describe failures the agent currently fails, so `min_reward = current_reward + epsilon` would block every iteration; the lenient floor encodes "don't make it worse" while letting iteration continue. `plan_cluster_promotion` previews without writing. New `scripts/cluster_m5_failures.py` + `make m5-cluster-failures` ties the chain end-to-end (in-process LightGBM baseline → analyzer → clustering → persistence → cluster-derived eval cases) with deterministic stubs by default + `--real` for ST + UMAP + HDBSCAN + Anthropic. | 11 promotion tests (4 plan-only no-DB + 7 DB-backed: provenance/cluster_id/workflow_id pinning, expected_behavior payload shape, max_cases cap, min_reward_floor validation, multi-cluster batch, FK-violation rollback, test_fold flag) + 9 CLI smoke tests (stub stages deterministic + alignment guard, JSON-serializable summary, arg parser, end-to-end pipeline run with stubs). |
| B3.4 | **Reproducibility CI** (added by review) | `.github/workflows/m5-replay-nightly.yml` — runs `make m5-replay` from a fresh container nightly. Fail-fast on drift (RMSE delta > tolerance vs prior night's baseline). **Cache strategy required for the <30 min budget:** (a) LLM responses replayed from a fixture file (not live API); (b) sandbox Docker image pre-built and cached as GHCR layer; (c) M5 data pre-loaded into a Postgres volume snapshot; (d) LightGBM training artifacts cached keyed by skill-version-hash. Without all four, CI hits live APIs and misses the budget by 10x. | Workflow green; <30 min wall time on a cold run with caches warmed. |
| B3.5 | **Cluster-label LLM eval** ✅ | `apps/kernel/src/ownevo_kernel/clustering/label_eval/` — `ClusterLabelJudgment` Pydantic schema (binary `agree`/`disagree` + ≤400-char rationale + echoed `cluster_id`; `extra='forbid'`, frozen, `schema_version="0.1"`). `LABELED_CLUSTER_CASES` — 20 hand-authored M5 fixtures spanning the failure-mode taxonomy (under-forecast / over-forecast / zero-inflated / high-variance / flat-prediction × FOODS / HOUSEHOLD / HOBBIES × CA / TX / WI), each carrying 3-8 plausible `text_signature` strings (matching the `m5_failure_analyzer._text_signature` format) + `domain_context` + `dominant_hint` for per-bucket slicing + ground-truth label. `judge_label_match(client, case, candidate_label)` via single-turn Anthropic forced tool-use; default model `claude-sonnet-4-6` (D4 contract: different from haiku-4.5 labeler; sonnet is strictly stronger but cheaper than opus); typed errors mirror A4.6 (validation / no-tool-use / id-mismatch). `run_cluster_label_eval(client, label_fn, ...)` drives labeler + judge across the fixture set in parallel (configurable `concurrency`, default 1; aggregates judge-vs-human agreement, per-`dominant_hint` slicing, verdict histogram). `wrap_sync_labeler` adapts the sync `AnthropicLabeler` to the async `LabelFn` shape via `asyncio.to_thread`. New `scripts/cluster_label_eval.py` + `make cluster-label-eval` CLI: `--judge-model`/`--labeler-model`/`--concurrency`/`--max-retries-per-call`/`--anthropic-base-url`/`--include-records`/`--pretty`/`--require-agreement`. Preflight refuses when `--judge-model == --labeler-model` (D4 contract). **The ≥0.7 gate runs on demand**, not in GitHub Actions — project policy is that CI does not consume API keys. Cost ~$1.20/run on default models (20 haiku labeler + 20 sonnet judge calls). | 20 ground-truth labels written ✅; CLI emits agreement score ✅; `--require-agreement 0.7` exits 1 on miss ✅. 64 new tests across schema (13) + fixtures (12) + judge (11) + runner (13) + CLI (15); kernel suite 1009 passing. Run before each W3-impacting release: `make cluster-label-eval LABEL_EVAL_ARGS='--require-agreement 0.7 --concurrency 4 --max-retries-per-call 1'`; record the result in the release notes. **2026-05-07 (pre-v0.4.0):** agreement 0.85 (17/20), judge `claude-opus-4-7` vs labeler `claude-sonnet-4-6`, 33.9s wall. |

**Week 3 exit criterion (Track B):** running M5 baseline + 1 simulated week → ≥3 failure clusters surface → ≥3 eval cases generated, all without human intervention. Reproducibility CI green with all 4 cache layers. Cluster-label eval ≥0.7. (B3.1 + B3.2 + B3.3 ✅; B3.4 ✅; B3.5 ✅ — code shipped; live ≥0.7 confirmation runs locally via `make cluster-label-eval` before tagging.)

#### Pre-W3 — Bootstrap loop (no clustering, auto-harness style)

**Decision (2026-05-03):** run one round of benchmark improvement before investing in the clustering pipeline. Validates the full loop fires end-to-end; gives the agent traces to read directly (auto-harness pattern). Clustering (B3.1-3) backfills regression protection afterward.

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| BL.1 | **DB seed script** | `scripts/seed_m5_baseline.py` — registers the 6 baseline skill files into the skills table + creates a workflow row; idempotent (skips re-registration if content unchanged). Sets `best_ever_score=None` (bootstrap). | `python scripts/seed_m5_baseline.py` runs clean; `psql` confirms 6 skill_versions + 1 workflow row. |
| BL.2 | **Agent system prompt** | `apps/kernel/scripts/m5_agent_prompt.md` — equivalent of auto-harness `PROGRAM.md`. You own the 6 skill files; use `read_skill` / `write_skill` / `run_pipeline` / `analyze_failures`; make one focused change per iteration; `run_pipeline` to validate before committing. | Prompt reviewed by hand; covers the 6-file split + loop shape. |
| BL.3 | **Loop entrypoint** | `scripts/run_improvement_loop.py` — wires `AsyncAnthropic` + `KernelContext(conn, sandbox, actor)` + `run_agent_turn` + `persist_gate_run(runner=SandboxedM5BenchmarkRunner)`. Bootstrap mode: `prior_eval_task_ids=[]`, `best_ever_score=None` on first run; reads DB-authoritative `best_ever` on subsequent runs. Prints iteration result to stdout. | One iteration completes: agent proposes a change, gate runs, iteration + proposal rows written to DB. |

**Bootstrap exit criterion:** ≥1 iteration recorded in `iterations` table; gate decision logged in audit; agent's proposed skill diff is visible. Gate runs in bootstrap mode — no regression protection yet (that comes from B3.3).

**What bootstrap mode does and doesn't do:** gate always passes on the first run (no prior eval suite, no best-ever to beat). From the second run onward, `best_ever_score` is DB-authoritative and the gate enforces improvement. Regressions on specific tasks are NOT caught until B3.3 seeds `prior_eval_task_ids`. This is exactly auto-harness behavior before `suite.json` is populated.

#### Pre-W3 (cont.) — Local-model sweep methodology (BL.3+ dogfood track)

**Why:** BL.3 wires to `AsyncAnthropic` against any compatible endpoint. Running the loop end-to-end against local models (LM Studio Anthropic-compat / Ollama OpenAI-compat) lets us dogfood, control cost during the substrate-quality phase, and avoid single-vendor risk before customer #1. Source of truth: **[`docs/local-model-testing.md`](local-model-testing.md)** (methodology + findings + candidate model lists).

**Four-tier funnel** — sequential, only one model active at a time (VRAM constraint):

| Tier | What | Cost / model | Pass criterion |
|---|---|---|---|
| **Phase 0 — pre-flight probes** (added 2026-05-04) | `apps/kernel/scripts/probe_tool_calling.py` (single-turn `read_skill` call check) + `probe_skill_quality.py` (file-rewrite + AST parse + em-dash/smart-quote detection). Triages the ~37 untested candidates without paying for Postgres + Docker + sandbox image. | ~30s + ~60s | Both probes exit 0. |
| **Phase 1 — synthetic-fixture compatibility scan** | Full `run_improvement_loop.py` against the synthetic M5 fixture (`/tmp/m5_synth_smoke/` — 5 series × 100 days). Catches multi-turn read-loop stalls (F4 in the testing guide — 8B models pass probes but never commit to `write_skill`). | ~2-15 min | ≥1 `iterations` row written + `val_score` recorded + no adapter-side rejection. |
| **Phase 2 — single full-real-M5 baseline** | Model-irrelevant; baseline is fixed code. Run once, produces the real-M5 baseline `val_score` Phase 3 lifts against. | ~33s wall | val_score recorded. **DONE 2026-05-04: `val_score = 0.330988` (RMSE 2.57142, WRMSSE 1.300426, n_series 30490, 28 test days).** |
| **Phase 3 — full improvement loop, top 1-2 models, real M5** | The load-bearing claim. Iterations cap 50, hard timeout 2h. Per-iter sandbox uses Phase-2 resource bumps (tmpfs 4GB, mem 16GB, timeout 1800s). | ~2-4 hours | Any iteration's `val_score < 0.330988`. |

**Status (2026-05-04, plus 2026-05-06/07 broader sweep):**
- Phase 0 / 1 sweep across 14 models surfaced F1-F5 (full text in `docs/local-model-testing.md`). qwen3-coder-30b on LMS Anthropic streaming is the only end-to-end driver across 10 candidates 8B-32B. ~37 candidates still untested.
- **A4.4 broader local-model sweep (2026-05-06/07, F14a-j):** different track from the multi-turn improvement loop above — A4.4 tests the single-turn forced-tool-use classification gate (`predict_label`) per fixture. 19+ models pass 3/3 across desktop LMS / laptop LMS / desktop Ollama. Top desktop iteration picks (sub-1-min wall): `granite-4.1-8b` 33s, `google/gemma-4-e4b` 34s, `mistralai/ministral-3-14b-reasoning` 47s. Top Ollama desktop pick: `qwen3-coder:30b` 82s (via `/no_think` auto-injection from F14i). Granite-4.1-8b is desktop-only (F14j: Apple Metal vs CUDA produces ~0.17 systematic credit-risk gap that flips desktop's reliable 3/3 into laptop's reliable 2/3 on the same Q4_K_S blob). A4.4 sweep results inform "which local model can drive the gate" — distinct from F4-F12's "which can drive the multi-turn loop."
- Phase 2 baseline locked: `val_score = 0.330988`.
- Phase 3 v1-v3 burned iteration budget on `SkillFormatError` variants (PR #26-#28 fixed; PR #30 eliminated the format surface entirely via structured `write_skill` tool args). v5 was the first run where `write_skill` succeeded on the structured surface; LMS server-side rejected a later tool call (`anthropic.APIStatusError: Failed to generate a valid tool call`) before the gate could run. Mid-debug.
- **Phase 3 closed on Sonnet 4.6 / Anthropic cloud (2026-05-04):**
  - v10 produced the first lift: `val_score=0.395143` (+19% over baseline 0.331). **B4.2 ✅.**
  - v12 (same workflow + DB) showed the regression-blocking path: `val_score=0.385126`, gate-blocked-no-improvement. **B4.3 ✅.**
  - **Stage C 7-iter replay** (post F9 prompt fix, PR #35) produced **first compound lift**: iter 0 `0.3859` → iter 2 `0.3988`, with iter 1 / iter 3 / iter 5 correctly gate-blocked and iter 4 / iter 6 gate-rejected (sandbox-error). 2 gate-passes, 5 correct rejections, 0 false promotions across 7 iterations on real M5. Total cost $1.86 with caching (PR #33).
  - Total Phase-3 spend on Sonnet across all replays: ~$4.50.
- **Local-model lift on real M5 remains open.** TODO-20 (qwen3-coder-30b retest with F6 mitigation prompt) — bug deterministic at 14/14 attempts. TODO-21 (devstral OOM bump) — OOM cleared at 1024MB but devstral codegen still doesn't produce a clean candidate. Gemma-4-26B-A4B retest — F4 read-loop stall (96% cache_read, 31 tok/turn).

**M5 performance reference points (from the 2020 competition, for honest framing):**

| Reference | WRMSSE | Source |
|---|---|---|
| M5 winning team | ~0.520 (22.4% better than top benchmark) | [M5 results paper](https://www.sciencedirect.com/science/article/pii/S0169207021001874) |
| Top 50 cutoff | ~0.55–0.65 (>14% better than top benchmark) | M5 results paper |
| Top benchmark (CRO / classical) | ~0.67 | M5 results paper |
| Naive baseline (prev month + prev year) | **0.939** | participant report |
| **Our static Phase-2 baseline** | **1.300** | `m5_baseline.py --sandbox` (3 features: lag_28 + dow + cat_id; 100 LightGBM rounds; default hyperparams) |

**What this means honestly:** our static baseline (WRMSSE 1.30) is *below the naive baseline* — by design. The agent needs room to improve, so we deliberately use a minimal baseline. The lift the loop produces (val_score 0.331 → 0.399, +20.5% relative on our val_score metric) is measurable and reproducible, but is not competitive with the M5 leaderboard. **The claim is loop semantics (promote / reject / compound), not absolute M5 performance.** A future Stage D run with `--sandbox-mem-mb 1024` + a stronger starting baseline + more iterations is the path toward absolute-WRMSSE numbers a domain expert would call competitive — but that's not the YC-application bar; the loop itself is. Cross-iteration failure memory shipped in PR #40 (TODO-23 ✅) — Stage D can now run with the loop's memory in-context.

This methodology compresses to ~1 hour for a fresh ~37-model probe sweep + ~5 hours of Phase 1 on the ~5 probe-passers, and informs which model to put through Phase 3 when budget is tight.

#### Week 4 — First end-to-end M5 loop cycle (Track B)

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| B4.1 | **M5 proposer agent** | Agent reads `analyze_failures` output + raw traces; proposes a skill diff targeting a specific failure pattern. One hypothesis per iteration. Three failures on the same hypothesis → abandon. (In bootstrap mode before B3.3, agent reads raw traces directly — auto-harness style.) | Agent proposes a code change; gate runs against the change; audit entry appended. |
| B4.2 | **First lift on M5** | At least one agent-proposed change passes the gate end-to-end and lifts a held-out metric measurably. | RMSE on held-out fold strictly improves after the change is approved; lift recorded in `iterations` table. |
| B4.3 | **First gate-blocked regression** | At least one proposed change is correctly rejected by the gate. Requires B3.3 to have seeded `prior_eval_task_ids` — until then the gate blocks only on val_score regression vs best-ever. | Audit log shows ≥1 reject entry with structured rationale. |
| B4.4 | **Day-7 milestone review** (added by review) | At end of W4, review the 7-day cumulative lift: if Day-7 lift is below +10% RMSE vs Day-1 baseline, escalate before W4 budget is gone. | Lift report generated; either lift is on-track (≥+10% by Day 7) or an explicit escalation/correction has been made. |

**Week 4 exit criterion (Track B):** at least one agent-proposed change passes the gate and lifts a metric; at least one regression is caught by the gate; Day-7 milestone reviewed.

#### Week 5 — Approval surface polish + 7-day M5 replay

Track A and Track B converge in W5 because **both tracks share the approval surface**. Polish it once.

| # | Track | Deliverable | Files / location | Validation |
|---|---|---|---|---|
| 5.1 | **Shared** | **Approval surface — full polish** ✅ | `apps/web/app/approvals/[id]/page.tsx` — plain-language summary on top, side-by-side diff (Monaco or similar), gate-results badge with per-eval-case breakdown, expected-impact estimate, Approve/Reject with comment-to-eval-case flow. | Cypress flow: open card → approve with comment → state transitions → audit entry → if rejected, comment becomes a new eval case. Same UX serves NL-gen-flow and M5 approvals. |
| 5.2 | **Shared** | **LLM-judge stub approver** ✅ (tightened by review + eng-review eval expansion) | `apps/kernel/src/ownevo_kernel/approvers/llm_judge.py` — admits proposals if (a) gate passes AND (b) plain-language explanation contains a structural element: references the cluster name AND names the change AND states an expected metric direction. Rejects everything else. Used for unattended benchmark runs. **Eng-review eval expansion (added 2026-05-03):** hand-label ~30 (proposal, explanation) pairs with structural-element ground truth (good: structural; bad: vague-but-positive, structural-but-wrong-direction, hand-wavy). Run nightly. Surface drift in CI. | Smoke test: 5 hand-crafted proposals → judge admits 3, rejects 2. **Eval test:** ~30 hand-labeled pairs → judge agreement with ground truth ≥0.85 (higher bar than cluster-label since false-positives drift M5 lift the wrong direction). Adversarial test: vague-but-positive → rejected. |
| 5.3 | **A** | **NL-gen failure clustering** ✅ | Track A's generated-sim traces flow through the W3 clustering pipeline (Track B's clustering, reused). | Run NL-gen workflow → cluster traces → at least 3 NL-gen-derived clusters appear. |
| 5.4 | **B** | **7-day M5 replay** ✅ | Replay 7 simulated days of M5. Each day: agent proposes → gate runs → LLM-judge admits or rejects → audit log grows → eval set grows. | `make m5-replay-7day` produces a visibly climbing lift curve over 7 cycles; audit log has 7+ entries; eval set grew from clusters. |
| 5.5 | **A** | **NL-gen meta-eval validated** ✅ (D7 — added by review) | `apps/kernel/src/ownevo_kernel/nl_gen/pipeline.py` — `generate_full_pipeline(...)` gains opt-in W5.5 gate: `meta_eval_gate=True` runs the A4.6 judge after the four generators and gates on `overall_verdict == "good"`. `meta_eval_min_aggregate_score` (numeric floor, default unset) guards the `(partial, partial, partial)` edge case; `meta_eval_model` / `meta_eval_max_tokens` are independent of the generator overrides so cheap-NL-gen + frontier-judge is one flag. `MetaEvalGateFailedError(NLGenError)` carries the judgment + threshold so audit-log consumers can record the rejection without re-running the judge. Gate uses a 1-retry wrapper on `MetaEvalJudgmentValidationError` (matches runner's documented ~5-10% transient rate). `apps/kernel/scripts/nl_gen_smoketest.py` — `--meta-eval-gate` / `--meta-eval-min-aggregate-score` / `--meta-eval-model` wire the gate end-to-end; JSON output gains a `meta_eval` block (`overall_verdict`, `aggregate_score`, per-dimension `coverage`) when the gate passes; gate failure emits a structured `error: "meta_eval_gate_failed"` payload (exit 1, agent call skipped). `--from-fixtures` ignores the gate flag. Default off — existing A4.4 callers keep the 4-call shape. **Judge agreement ≥0.7:** already at 0.85 from A4.6 — no additional work required. **UI coverage badge** (PLAN.md exit criterion) deferred to W7 — no live workflow detail page exists yet; kernel-side gate + CLI JSON output are the W5.5 deliverable. | Judge agreement 0.85 ≥ 0.7 ✅ (established at A4.6). Gate wired in `generate_full_pipeline` ✅. Structured `meta_eval` block in smoketest JSON output ✅. Gate failure short-circuits agent call ✅. `--from-fixtures` bypass ✅. 10 new tests in `test_nl_gen_pipeline_gate.py` (back-compat, happy path, fail paths, floor rejection, override propagation, frozen result) + 6 new CLI tests in `test_scripts_nl_gen_smoketest.py`; kernel suite 999 passing. Live gate: `make nl-gen-smoketest WORKFLOW=demand-prediction SMOKE_ARGS='--meta-eval-gate --pretty'` (manual; requires ANTHROPIC_API_KEY). |

**Week 5 exit criteria:**
- (Shared) Approval surface usable by a non-engineer in under 1 minute per card (dogfood test with a non-engineer reviewer).
- (Shared) LLM-judge stub rejects vague/structural-empty explanations on adversarial test.
- (Track A) Generated-sim traces flow through clustering successfully.
- (Track A) NL-gen meta-eval validated (judge ≥0.7 agreement) and wired as quality gate.
- (Track B) 7-day M5 replay produces a visibly climbing lift curve.

#### Week 6 — Full M5 30-day replay + NL-gen end-to-end demo

| # | Track | Deliverable | Files / location | Validation |
|---|---|---|---|---|
| 6.1 | **A** | **NL-gen end-to-end live demo** ✅ (2026-05-09 dry-run, PR #81 — TODO-28) | The full Track A flow runs in <5 minutes from "type description" to "lift chart climbs". On a hand-picked workflow (supply-chain demand-forecast). Storyboard at `docs/W6_DEMO_STORYBOARD.md`; CLI at `apps/kernel/scripts/nl_gen_demo_loop.py`; UI at `/workspaces/acme/workflows/new` (W7 slice 5 moved it from `/workflows/preview`). | **Validation gate cleared:** total dry-run wall ~2 min (page render + 2 loop runs), well inside the 5-min budget. Storyboard's recommended command (`--cycles 2 --agent-model claude-haiku-4-5 --include-instructions --pretty --progress`) lands `[0.20, 1.00]`, `is_climbing=True`, +0.80 lift in 15.2 s. Four UX gaps surfaced + patched on PR #81: storyboard URL, disabled-button tooltip, cycle-2 regression risk, silent CLI. Full report at `docs/W6_PREVIEW_DRYRUN.md`; raw run logs at `docs/W6_PREVIEW_DRYRUN_artifacts/`. |
| 6.2 | **B** | **Full 30-day M5 replay across 4 conditions (parallel — added by eng review)** ✅ (v6 complete 2026-05-09 — `ownevo_30day_v6_sonnet` 30+30+30 ✓; details: `docs/W6_30DAY_REPLAY_NOTES.md`) | Per [`benchmarks/m5-code-gen-loop.md`](../../ownevo_docs/benchmarks/m5-code-gen-loop.md): A (frozen baseline), B (static LLM single-shot, sanity check), C (loop autonomous), D (loop + approval gate). **Run all 4 conditions in parallel on separate Docker compose stacks** (each with its own Postgres + sandbox); merge results in `iterations` table at the end. Sequential = ~150 hours wall time; 4-way parallel ≈ 37 hours. Without parallel strategy, W6 budget is too tight. | `make m5-replay-30day` launches 4 parallel stacks; each writes to a stack-namespaced workspace_id (prefix-hack for the merge), hero chart generated from merged `iterations`; per-cluster lift report generated; gate-blocked-regression count emitted; total wall time <40 hours. |
| 6.3 | **B** | **M5 success thresholds met** ✅ (waived — see Validation) | Per `benchmarks/m5-code-gen-loop.md` § Success Criteria: ≥+25% RMSE lift Day-1→Day-30 in condition D; ≥50 eval cases generated; ≥15 approved revisions; ≥5 gate-blocked regressions; reproducible from fresh checkout. | **v6 result:** Condition C WRMSSE 1.046 (−19.5% vs baseline 1.300) / +23.2% val_score. ≥+25% WRMSSE threshold not met; decision: proceed with −19.5% — demonstrates substantial agent-driven lift. Condition D: 7 gate-passes all judge-rejected ("cost of safety" frame). Remaining threshold counts (≥50 eval cases, ≥15 approved revisions, ≥5 gate-blocked regressions) require DB audit read from v6 run; waived for YC demo. |

**Week 6 exit criteria (Phase 2 validation gate, must pass before Phase 3):**
- (Track A) NL-gen end-to-end demo runs live in <5 minutes for an external reviewer.
- (Track B) M5 30-day replay completes; hero chart emitted; all success thresholds met or explicitly waived.
- Both tracks produce audit chains that pass `verify_audit_chain` end-to-end.

---

## Phase 3 — Customer skin + τ³-bench head-to-head + demo materials (Weeks 7-8)

**Goal:** by end of Week 8, the workspace UI from `www/preview/yc-s26-rk7p3/` is wired to the live demand-prediction backend, the τ³-bench head-to-head against NeoSigma is published with the human-approval gate engaged, and the YC video + reproducibility artifacts are shipped.

**Substrate inheritance from W6:** PR #67 (BL.3 in-call conversation compaction) landed during W6 and applies to every multi-turn agent run going forward. The τ³-bench tasks below (W7.3 + W8.3) are 10-30 tool calls per case in the same shape that crashed the M5 30-day replay v1/v2/v3 with `Context size has been exceeded`. The compaction substrate makes those runs viable; per-iter caps like v3's `--max-iterations 12` workaround are no longer needed. Validated on the M5 30-day v4 (27 iterations completed, zero context errors); see `docs/W6_30DAY_REPLAY_NOTES.md`.

---

### Week 7 — Customer skin (Track 1) + τ³-bench template (Track 3, parallel)

#### Track 1 — Customer-facing workspace skin

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 7.1.1 | **Wire workspace UI to live backends** ✅ (slice 1, partial — full Eval-cases / Skills / Operate panes deferred to W8) | `apps/web/app/workspaces/[wsId]/...` — workspace shell + Health + Failures + Audit + New Workflow routes wired against new kernel endpoints (`/api/workflows`, `/api/workflows/{id}/iterations`, `/api/workflows/{id}/failure_clusters`, `/api/audit`, `/api/audit/verify`). W2.5/W5.5 routes preserved under a `(legacy)` route group. Match the visual target in `www/preview/s26-rk7p3/`. | Workspace shell renders at `/workspaces/acme`; failure-cluster cards render against live `failure_clusters` rows; audit list reads `audit_entries`; gracefully degrades to empty states + a banner when the kernel API is unreachable. |
| 7.1.2 | **Lift chart UI** ✅ (slice 2 — the YC closer) | `apps/web/app/workspaces/[wsId]/lift-chart.tsx` — pure-SVG line chart, two series (best-ever val_score in accent + per-iteration val_score in faint) + annotated dots wherever `has_approved_proposal=true`. Iteration-keyed per W7_SLICE.md decision (not day-keyed). | Renders Track B's per-iteration val_score curve from `/api/workflows/{id}/iterations`. Empty state copy points the reviewer at `scripts/run_improvement_loop.py`. |
| 7.1.3 | **Failure cluster card UI** ✅ (slice 3) | `apps/web/app/workspaces/[wsId]/workflows/[wfId]/failures/failure-cluster-card.tsx` — matches `16-failures.html § .cluster`. Severity-grouped (High / Medium / Low) on the page, sorted high → low → cluster_size DESC by the API. | All clusters under a workflow render as cards with severity pill + cluster_size + first-seen date + label-eval / quality-score chips. Empty state points at `make m5-cluster-failures`. |
| 7.1.4 | **Proposal review card UI** ✅ (slice 7 — PR #68) | Body shipped at W5.1 in `(legacy)/proposals/[id]/`; W7 polish pass moved it under the workspace shell at `apps/web/app/workspaces/[wsId]/proposals/[id]/page.tsx` + new `latest_proposal_id` field on `FailureClusterSummary` drives a one-click Failures-card → proposal link. Legacy URL `307`s to the workspace path. | All 15+ approved Track B proposals viewable historically + any pending proposal viewable in queue; clicking a failure cluster with a spawned proposal opens the review card. |
| 7.1.5 | **Audit trail UI** ✅ (slice 4 — append-only per D2; crypto chain TODO-3) | `apps/web/app/workspaces/[wsId]/audit/page.tsx` — chronological list (newest seq first) with expandable `<details>` per row. "Verify chain" button is a Server Action wrapping `POST /api/audit/verify`; surfaces seq gaps + duplicates + canonical export bytes inline. | Verify-chain button renders a structural-integrity panel; entries expand to show full JSON payload + related_id. |
| 7.1.6 | **Health page (default landing)** ✅ (slice 2) | `apps/web/app/workspaces/[wsId]/page.tsx` — glance metrics + lift chart for the primary workflow + workflow-rows table over `/api/workflows` (only demand-prediction is live; other 3 are positioning mocks). | Default landing for the workspace; lift chart visible above the fold; rows table groups all workflows; "New workflow" CTA points at `/workspaces/[wsId]/workflows/new`. |
| 7.1.7 | **"New Workflow" entry point active** ✅ (slice 5) | NL-gen flow moved from `/workflows/preview` into `/workspaces/[wsId]/workflows/new` so the surface lives inside the workspace shell. Legacy URL 307s to the new path. WorkspaceNav sidebar item + Health-page page-actions both link there. | Sidebar "New workflow" is one click from Health; the existing W5.5 four-artifact preview + meta-eval coverage badge renders unchanged inside the workspace nav. |
| 7.1.8 | **Three other workflows as positioning mocks** ✅ (slice 6) | `app/workspaces/[wsId]/workflows/[wfId]/{layout,page,mocks}.tsx` — hand-authored copy for `labour` / `contract` / `support` (4 metrics + 3 recent-activity entries each + distinct buyer + role + version). Inline "STATIC MOCK · same loop, NL-gen the rest" banner on every mock surface. Visual parity with demand-prediction Overview placeholder. | Tab strip shows 4 workflows; clicking each mock renders unique buyer + title + status pill + glance metrics + activity feed; demand-prediction shows the W8.1.1-pending placeholder pointing at Failures + Audit. |
| 7.1.9 | **Per-trace step inspection** ✅ (slice 8 — PR #68) | `apps/web/app/workspaces/[wsId]/traces/[traceId]/page.tsx` + `workflows/[wfId]/traces/page.tsx` (per-workflow list) — chronological agent steps (skill_loaded → reasoning_delta → tool_call_start → tool_call_result → content_delta → citation), with per-step input/output expand via native `<details>`. Mock parity: `15-traces.html` right pane. Reads from substrate `traces` table populated W1.5. New endpoints `GET /api/workflows/{id}/traces` + `GET /api/traces/{id}`. | Click a trace in the list → see ≥6 step types rendered chronologically with timestamps, durations, inputs, outputs. LangSmith / LangFuse parallel — table-stakes inspectability. |
| 7.1.10 | **Per-skill detail · prompt variant** ✅ (slices 9-10 — PR #68) | `apps/web/app/workspaces/[wsId]/skills/[skillId]/page.tsx` — SKILL.md content + retention contract + version history + related retention-violation eval cases + capability tags. Mock parity: `18-skill-detail.html`. | All instruction-style skills (NL-gen-emitted) render with content visible + retention contract + retention-violation eval cases (or empty-state copy when none) linked. |
| 7.1.11 | **Per-skill detail · code variant** (M5 Python skills) ✅ (slices 9-10 — PR #68) | Same route as 7.1.10, branches on `kind`: renders Python source with regex-extracted function signatures + version-to-version inline diff via `SkillDiff` (LCS) + "eval cases that moved" table joined through `iterations.cluster_id → proposals.skill_id`. Mock parity: `18a-skill-detail-code.html`. | All M5 code-skills (`feature_engineer.py`, `model_trainer.py`, `outlier_handler.py`, `ensemble.py`, `predictor.py`, `data_loader.py`) render with code + diff to prior version + linked eval cases that moved. |
| 7.1.12 | **Workflow Agent-anatomy pane** ✅ (slice 11 — PR #68) | `apps/web/app/components/agent-anatomy.tsx` rendered above-the-fold on workflow Overview page (`workflows/[wfId]/page.tsx`). Three columns: Skills active (linked to skill-detail) · Tools available (signatures from `WorkflowSpec.tools`) · Topology + reviewer + success criterion + environment. Mock parity: section in `05-workflow-overview.html`. New endpoint `GET /api/workflows/{id}` returning `WorkflowAnatomyResponse` with the raw NL-gen `spec` JSONB. | "What the agent CAN do" view is visible above the fold on every workflow Overview; live workflows read from kernel, mock workflows feed the same shape from `mocks.ts`. |
| 7.1.13 | **Demo workspace rollback runbook** ✅ (slice 12 — PR #68) | `docs/runbooks/demo-rollback.md` + `make revert-skill SKILL=<id> TO_VERSION=<n>` target wrapping `apps/kernel/scripts/revert_skill.py` (optimistic-concurrency guard against concurrent gate-pass; single-transaction `UPDATE skills SET head_version_id` + audit `proposal-rolled-back` write; `DRY_RUN=1` produces no DB writes). | Runbook exists; dry-run produces clean rollback in <5 minutes; audit entry preserves chain (`verify-chain` button still returns valid). |

#### Track 3 — τ³-bench template + reproduce-NeoSigma sanity check

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 7.3.1 | **τ³-bench dataset + harness** ✅ done (2026-05-09, PR #77) | `apps/kernel/src/ownevo_kernel/benchmark/tau3/` — `SandboxedTauBenchRunner` + `failure_analyzer.py`; Sierra dataset baked into `ownevo-sandbox-tau3:0.1.0`; retail test split (40 tasks) seeded as eval cases under `tau3-retail-v1`; scoring via existing 3-step gate. | Sonnet 4.6 baseline = 0.85 on full retail test fold (post-substrate-fix), within ±2pp of the auto-harness fork's 0.80 on the same model — gap explained by 5 sims that previously crashed in the eval path now evaluating to real rewards (the json.loads / `@dataclass` fixes in `tau2_patches.py`). |
| 7.3.2 | **Per-domain agent templates** | Retail shipped (`apps/kernel/baselines/tau3_retail_v1/agent.py` — HarnessAgent baseline in SKILL_FORMAT, the file the loop optimizes). Airline + telecom deferred to post-W8 (single-domain story is sufficient for the YC video; Sierra's three domains share the same harness so adding them is mechanical). | Each template runs end-to-end on training subset; emits structured AgentEvents. |
| 7.3.3 | **Condition B autonomous loop on retail test fold** ✅ done batch-1 (2026-05-09, PR #77) | Sonnet 4.6 as loop agent, 10-cycle autonomous run via `scripts/tau3_p2_sonnet_loop.sh`. **val_score 0.85 → 0.95 (+10pp absolute / +11.8% relative)** at iter 11 on a prompt-only change in skill v38. 10-cycle batch-2 added no break-through past 0.95 — saturation evidence. Local-loop-agent variant `qwen/qwen3.6-35b-a3b` on LMS reproduced val=0.85 across two PASSes — preliminary free-loop driver. | **D5 B-frame:** absolute +10pp lift on a baseline already 24pp above NeoSigma's GPT-5.4 starting line (0.56). Reframe holds — "the loop picks up reliability past the strong-model baseline." NeoSigma reproduction (running their published 18-iter loop on GPT-5.4 to confirm their 0.56→0.78 number on our substrate) remains open and is the last Track 3 item before W8. |

**Week 7 exit criteria:**
- (Track 1) A non-engineer can open the workspace UI, type a workflow description, watch ownEvo generate sim+evals+metric, see the M5 lift chart climbing for demand-prediction, click into a failure cluster, approve a proposed change in plain language, and watch the audit trail update — all live, no manual fixup.
- (Track 3) Condition B reproduces NeoSigma's published number to within ±5pp on the training subset.

---

### Week 8 — Full τ³ replay + demo materials + reproducibility + onboarding

#### Track 3 — τ³-bench completion

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 8.3.1 | **Condition C with gate engaged on full test set (parallel — added by eng review)** | LLM-judge stub approver (W5.2, eval-expanded) admits proposals; subset re-run with human approver (founder/advisor) for credibility. **Demo framing per D5 B-frame:** record condition B (autonomous, ≈NeoSigma) AND condition C (gated) head-to-head, with the gap explained as "the cost of safety." Demo holds even if condition C lands at +25% — removes binary outcome risk. **Run conditions A/B/C in parallel** on separate Docker compose stacks (same pattern as M5 W6.2); merge in `iterations` table. | Threshold: ≥+35% lift A→C. Stretch: ≥+40% (beats NeoSigma's autonomous +39.3%). **Soft-result fallback:** if condition C is below +35%, the B-frame demo still ships honestly: "autonomous matches the public number; gated is the enterprise tradeoff." All approved changes have an append-only audit entry. Total wall time <2 days for the full test set across 3 conditions. |
| 8.3.2 | **`benchmarks/tau3-results-2026-Q3.md`** | `ownevo_docs/benchmarks/tau3-results-2026-Q3.md` — immutable run record, three conditions plotted, B-frame head-to-head with NeoSigma (D5), append-only audit log exportable. | File written; reviewer can clone the repo and re-derive the chart from the audit log. |
| 8.3.3 | **Sample human-approved subset documented** | ≥5 changes from condition C re-approved by a human (founder/advisor) instead of the LLM-judge stub. Document any divergence between human and LLM-judge decisions. | Subset documented in tau3-results post; honesty about any divergences preserved. |

#### Track 0 — UI primitive renderers (workspace visual fidelity, precursor to demo materials)

**Context:** `packages/trace-format/.../ui_primitives.py` defines 8 typed primitives (MetricCards, TimeSeriesChart, TableView, AlertList, KanbanBoard, ConversationView, SideBySideView, DocumentReader); marketing preview `www/preview/s26-rk7p3/27-primitives.html` shows a 9th (ScheduleGrid) not yet in the Python schema. Real NL-gen fixtures (`demand_prediction`, `credit_risk`, `contract_review`) populate `WorkflowSpec.ui.tabs[].primitives` with these types, but the Next.js app renders none of them — only two bespoke components exist (`LiftChart` ≈ TimeSeriesChart; `SkillDiff` ≈ SideBySideView). All CSS is already shipped in `apps/web/public/styles/primitives.css` (shared with `www/preview`, no duplication). The runtime "agent output → primitive data" resolver (layer D) is Phase-2 work; A + B + C unblock the demo-quality workspace for the video.

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 8.0.1 | **A — 8 leaf primitive React components** | `apps/web/app/components/primitives/{metric-cards,time-series-chart,table-view,alert-list,kanban-board,conversation-view,side-by-side-view,document-reader}.tsx`. Each takes pure data props matching the shapes in `27-primitives.html` § Input shape; uses existing CSS classes (`.metric`, `.chart`, `.alert`, `.kanban`, `.convo`, `.side-by-side`, `.doc-reader`). Server components, no client JS, inline SVG for charts (matches `lift-chart.tsx` pattern). New showcase page at `/workspaces/[wsId]/primitives` renders each with hand-curated demo data lifted from the HTML mock. | Visual parity with `27-primitives.html` light + dark mode; each component renders with mock data on the showcase page; no console errors; CSS classes reuse `primitives.css` (no new tokens). |
| 8.0.2 | **B — ScheduleGrid schema + component** | Add `ScheduleGrid` Pydantic class to `packages/trace-format/src/ownevo_format/ui_primitives.py` (`type: Literal["ScheduleGrid"]`, `rows_source: str`, `cols_source: str`, `cells_source: str`); add to discriminated union + `__init__.py` exports; mirror in Zod schema. Add `apps/web/app/components/primitives/schedule-grid.tsx`. Bump `schema_version` per `packages/trace-format/README.md` if applicable. | 9/9 parity between Python schema and HTML mock; schema round-trip test added; component renders on showcase page with the labour-style demo data shape from HTML lines 417-484. |
| 8.0.3 | **C — Wire MetricCards + TimeSeriesChart + TableView + AlertList into demand-prediction Overview** | Update `apps/web/app/workspaces/[wsId]/workflows/[wfId]/page.tsx` to render the workflow's `WorkflowSpec.ui.tabs[0].primitives` with a mock data resolver (`apps/web/lib/primitives-mock-data.ts` — keyed off the primitive's `source` field, returns hand-curated objects per workflow). Replace the "W8.1.1-pending" placeholder banner. Mock workflows (labour / contract / support) read from existing `mocks.ts` and render the same primitives with their hand-authored data. | Visiting `/workspaces/acme/workflows/m5-demand-prediction` (when seeded) renders the four primitives above the Agent Anatomy pane; mock workflows visually parity with `05-workflow-overview.html` / `20-labour-overview.html` / `22-contract-overview.html`. Demo data is clearly labelled "preview / mock" so reviewers know layer D isn't live yet. |
| 8.0.4 | **Skills library page + sidebar Library section build-out** | New route `apps/web/app/workspaces/[wsId]/skills/page.tsx` — workspace-scoped list of all skills across all workflows, matching `www/preview/s26-rk7p3/11-skills-registry.html`: skill name + capability tags + parent workflow + active version + total version count + last-activity timestamp + per-row click into existing `/skills/[skillId]` detail. New kernel endpoint `GET /api/skills` (workspace-scoped, returns `SkillRegistrySummary[]`); existing `GET /api/workflows/{id}/skills` stays for the per-workflow view. Sidebar Library section gains two links above Audit: **Skills** (→ `/skills`) and **Views** (→ `/primitives`, the Track 0.1 showcase). Mock workflows surface their static skills via existing `mocks.ts`. | `/workspaces/acme/skills` lists every skill with capability tags; clicking a row opens existing skill-detail page; sidebar shows Skills / Views / Audit under Library; visual parity with `11-skills-registry.html` light + dark mode. |

**Track 0 exit gate (shipped 2026-05-11, PR #84):** A non-engineer reviewer opens `/workspaces/acme/workflows/m5-demand-prediction` (Docker stack up, M5 seeded) and sees a live-looking forecast workspace — metric tiles, lift chart, SKU-at-risk table, markdown-alert list — instead of the current placeholder banner. **Important caveat:** Track 0 wired the primitives with hand-curated mock data (`apps/web/lib/primitives-mock-data.ts`) plus three positioning-mock workflows (`workflows/[wfId]/mocks.ts`) and mock skill rows in `skills/page.tsx`. **Track 4 below replaces this with real product code** — the mocks come out, demo content moves to a `make seed-demo` script, layer D ships as 8.4.6, and the full gen → eval → propose loop becomes clickable in the UI.

#### Track 4 — Real-product UI loop (rip mocks, seed DB, gen → eval → propose in UI)

**Context:** Track 0 closed the visual-fidelity gap but did so with mocks. To move toward a real usable product with no demo leftover in runtime code, the three mock surfaces come out, replaced by (a) a one-shot DB seed script (`make seed-demo`) that writes real workflow rows, (b) the missing live endpoints that close the gen → eval → propose loop in the UI, and (c) the layer-D resolver that produces real primitive data from iteration outputs (previously Phase-2 deferred). The seed script lives outside runtime code — production code reads from DB only. To go to clean product: don't run the seed script.

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 8.4.1 | **Rip mock infrastructure** | Delete `apps/web/app/workspaces/[wsId]/workflows/[wfId]/mocks.ts`, `apps/web/lib/primitives-mock-data.ts`, the `WORKFLOW_MOCKS` merge + `isMock` plumbing in `apps/web/app/workspaces/[wsId]/skills/page.tsx`, and the `<MockBanner />` + `buyer` / `buyerRole` / `version`-pill rendering in `workflows/[wfId]/layout.tsx`. Drop the `getWorkflowOverviewPrimitives` call site in `workflows/[wfId]/page.tsx`. Replace primitive data with honest empty states ("no data yet — run an iteration to populate") until 8.4.6 lands. | `grep -rni "MOCK\|hand-curated\|positioning copy\|STATIC MOCK" apps/web/app apps/web/lib` returns zero hits in runtime code. Visiting `/workspaces/acme/` on a fresh DB shows zero workflows (or just `m5-demand-prediction` if M5 seeded). |
| 8.4.2 | **`make seed-demo` writes labour/contract/support as real workflow rows** | `scripts/seed_demo.py` — idempotent INSERT of three workflow rows + their initial skills via the same kernel-internal code path that `POST /api/nl-gen/generate` will use (8.4.3). No reference to the deleted `mocks.ts`. New `Makefile` target `seed-demo` running `uv run python scripts/seed_demo.py`. | Fresh DB → `make seed-demo` → `/workspaces/acme/` lists 3 (or 4 incl. M5) workflows; each renders the standard Overview shell (header, anatomy pane, primitives in empty state); no demo banner anywhere. Re-running `make seed-demo` is a no-op. |
| 8.4.3 | **Live `POST /api/nl-gen/generate` + wire to `/workflows/new`** | New endpoint in `apps/kernel/src/ownevo_kernel/api/routes/nl_gen.py`: accepts `{description: str, workflow_id?: str}`, calls existing `generate_workflow_spec(description)` (already proven via `make nl-gen-smoketest`), persists the WorkflowSpec + skills via the workflow-creation code path, returns the new workflow id. `apps/web/app/workspaces/[wsId]/workflows/new/page.tsx` switches the disabled button to a live Server Action that calls the endpoint and `redirect()`s to `/workflows/[newId]`. Delete the `"CLI demo: make nl-gen-demo-loop — UI wire-up planned for W8"` tooltip + comment. | Empty DB → user types description in `/workflows/new` form → clicks Generate → ~30 s later lands on `/workflows/[newId]` showing the spec NL-gen produced. New workflow visible in `/workspaces/acme/` workflow list. |
| 8.4.4 | **Eval cases UI surface** | New route `apps/web/app/workspaces/[wsId]/workflows/[wfId]/eval-cases/page.tsx` — lists `EvalCase` rows (id, cluster_label, prompt, expected_label) with link to a per-case detail page. New kernel endpoint `GET /api/workflows/{id}/eval-cases` if not already present. Sidebar `Eval cases` link in the workflow nav (next to Failures / Traces / Audit). | Workflow created via 8.4.3 has eval cases NL-gen generated; the page lists them with cluster + prompt + expected_label; clicking a row shows full case body. |
| 8.4.5 | **"Run iteration" button → proposal in existing UI** | "Run iteration" button on workflow Overview that POSTs to new `POST /api/workflows/{id}/iterations/run` kernel endpoint; endpoint enqueues one iteration cycle (proposer → gate → audit) as a background task and returns a `task_id`. UI polls task status; on completion the proposal appears in `/proposals` list. Existing `/proposals/[id]` page (already shipped) handles approve/reject. | Click "Run iteration" on a seeded workflow → status indicator goes RUNNING → after N minutes, a proposal row exists in `/proposals` → approve/reject from existing UI → audit entry written. End-to-end clickable. |
| 8.4.6 | **Layer-D resolver: real primitive data from iteration outputs** (promoted from "Phase 2 deferred") | Kernel-side resolver (new `GET /api/workflows/{id}/overview-data` or fields on `WorkflowAnatomy`) that joins `WorkflowSpec.ui.primitives[].source` against the latest iteration's structured outputs. Renders MetricCards from `iterations.metrics_json`, TimeSeriesChart from cross-iteration lift history, TableView / AlertList from structured AgentEvents the agent emits. Replaces empty states from 8.4.1. | A workflow with ≥1 iteration shows real numbers in the Overview primitives — no empty states, no mock data, no hard-coded fallbacks. The "preview / mock" labels from Track 0 are gone. |
| 8.4.7 | ✅ DONE 2026-05-12 — **Iteration runner populates audit_entries + failure_clusters + traces** (PR #85, `aa92a88`). | `iteration_runner.py` writes per-case traces (synthetic AgentEvent: `tool_call_start` + `tool_call_result`), one `failure_clusters` row per cluster with per-cluster `sample_trace_ids`, and audit entries for `gate-run-started` / `gate-run-completed` / `cluster-created` (per cluster) / `skill-version-created` / `proposal-created`. `iterations.cluster_id` anchors the dominant cluster. | Iteration #7 on `credit-risk` produced 1 cluster (`failure pattern: false-negative`, 5 traces) + 12 traces + 4 new audit rows; Failures / Traces / per-workflow Audit tabs all populate. |
| 8.4.8 | ✅ DONE 2026-05-12 — **Per-iteration drill-down: case-level predictions** (PR #85, `5fc5d68`). Plus `176cd37` plumbed agent rationale through to each row. | Route `/workflows/[wfId]/iterations/[idx]/page.tsx` shows the failed-first case roster (case_id · predicted · expected · fold · trace link · rationale text). Kernel: `GET /api/workflows/{id}/iterations/{idx}` returns `IterationDetailFull` with per-case rows joined out of `traces.metric_outputs`. Iterations list on Overview links to it. | Iteration #8 on `credit-risk` shows 8 failed + 4 passed cases with the agent's domain-reasoning rationale visible per row ("APP_00009 has moderate credit score (659), very low DTI ratio (0.137)..."). |

**Track 4 exit gate:** A reviewer clones the repo on a fresh machine, runs `make dev-up` + `make seed-demo`, lands at `/workspaces/acme/`, sees seeded workflows with empty Overview (honest "no data yet"), clicks `New workflow`, types a description, watches NL-gen create a workflow live, opens its Eval cases page, clicks `Run iteration`, sees a proposal appear in the inbox, opens it, approves it, sees the audit row update — the entire gen → eval → propose → approve → audit loop runs in the UI with zero hard-coded demo content in runtime code.

**Sequencing note:** 8.4.1 + 8.4.2 ship together (rip + seed are inseparable). 8.4.3 → 8.4.4 → 8.4.5 are strict order. 8.4.6 can land any time after 8.4.5. **8.4.7 + 8.4.8 shipped together in PR #85** (`feat/real-ui-loop`) — alongside lifecycle controls (workflow delete, description edit, deploy/rollback, eval-case manual curation), Health polish, the Operate sub-tab, the operator shell, the Connect on-ramp, workspace Traces, and per-case rationale plumbing. **All TODO-36 → TODO-43 closed in the same PR.** Remaining gaps (per-cluster reasoning summary, stale-running sweep, new-workflow review step, etc.) are captured in [TODOS.md](../TODOS.md) TODO-44+.

**Tradeoff vs Track 1 demo materials:** Track 1 rows (8.1.1 YC video, 8.1.4 website screenshots) currently assume the hand-curated demo data shipped in Track 0. After Track 4 lands, video / screenshots show real product state — more credible, but requires running real iterations against seeded workflows ahead of recording. The seed script + 8.4.5 iteration trigger make that loop cheap. Track 1 timing depends on Track 4 reaching 8.4.6 (or accepting empty-state Overview in the video).

#### Track 1 — Demo materials (M5 + τ³ + NL-gen together)

| # | Deliverable | Files / location | Validation |
|---|---|---|---|
| 8.1.1 | **Record 90-second YC video** | Per North Star storyboard. Live demand-prediction workspace (rendered via Track 0 primitives — hand-curated demo data acceptable for video) + real M5 results from W6 + real τ³ split-screen from W8 + live NL-gen flow scene. | Single take or minimal cuts; reviewer who watches understands all 3 pillars without a slide. |
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
- **Track 4 exit gate met:** zero hard-coded demo content in runtime code; `make seed-demo` is the only path to demo data; full gen → eval → propose → approve loop is clickable in the UI on a fresh DB.

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
- **Post-MVP benchmark pipeline** (SkillsBench, Claw-Eval Pass^3, MCPMark, Tool Decathlon, VITA-Bench) — see [`ownevo_docs/benchmarks/README.md` § Post-MVP Benchmark Pipeline](../../ownevo_docs/benchmarks/README.md). Sequence after the 30-day M5 replay result is published. SkillsBench is the most ready to promote to a full plan (the "self-generated skills don't help" published finding is the cleanest rebuttal to the main product objection).
- Self-evolving the harness itself (we evolve skills/prompts/code only)
- Custom Rust gateway (LiteLLM is enough; revisit if local-model latency becomes a problem)
- **Multi-tenant scaffolding** (D4) — `workspace_id` columns, RLS policies, audit triggers, workspace-scoped query helpers, workspace switcher, billing UI, org admin. Single-tenant for MVP; full retrofit before customer #2.
- Knowledge ingestion from Slack/email/docs/runbooks — Q3 2026 per existing roadmap
- Mobile UI
- Built-in skills marketplace
- Re-running tau-bench in original prompt-only form (auto-harness already proved that loop; we run code-gen-under-gate on M5 + τ³ instead)
- **Multi-agent topology graph view** (n8n / Google Opal style visualization) — Phase 2; MVP workflows are single-agent loops, the Workflow Agent-anatomy pane (7.1.12) is enough for single-agent inspection
- **Visual workflow composition / node-graph editor** for hand-building workflows manually (n8n / Google Opal style) — Phase 2 deferred indefinitely; NL-gen (Track A) IS the composition surface, a visual builder competes with our own thesis
- **Vellum-style prompt A/B variant workbench** — Phase 2; the regression gate IS the A/B test, the proposal review card already shows before/after with gate results
- **Crypto-grade audit chain** (Merkle + signed root + transparency log) (D2) — append-only WORM ships in MVP. Crypto upgrade queued for Phase 2 when first regulated-industry buyer evaluates.
- **Managed-sandbox provider** (e2b/Modal) (D3) — local Docker for MVP. Migration queued for Phase 2 when local Docker hits resource ceiling or first managed customer ships.
- **Approval-process enterprise polish** — Slack/email digests, SLA tracking, time-delayed deploy, severity-based auto-approve. MVP approval surface is Approve/Reject + comment + audit-row.

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
| 7 | **Multi-tenant retrofit cost (accepted, D4).** Single-tenant for MVP per D4. Customer #2 onboarding will block on the retrofit (1-2 weeks), happening in the breathing room between YC and customer #2. | Medium | Medium | Schema design in W1-W2 stays "retrofit-friendly" (no patterns that fight a future `workspace_id` column). Phase-2 retrofit checklist budgeted. The decision was a deliberate trade against ~3-5 W1-W2 days; the retrofit cost is accepted. |
| 8 | **Demo data feeling fake (Phase 3 mock workflows only).** Demand-prediction uses real M5 data, but the 3 positioning-mock workflows still need believable UX props. | Low | Low | Light prop-grade synthetic data for the 3 mocks; don't over-invest. |
| 9 | **Audit log export-import asymmetry (Phase 1 W2 — simplified by D2).** Append-only WORM (D2) avoids hash-chain pitfalls. The remaining risk is canonical-JSON serialization drift on export. | Low | Medium | Round-trip test in W2.4; canonical-JSON format (sorted keys, no whitespace) avoids serialization drift. Crypto-grade tamper-evidence (Merkle + signed root) is a Phase-2 retrofit when first regulated buyer requires it. |
| 10 | **Sandbox dependency drift across replays.** A LightGBM version bump changes M5 results between replays, breaking reproducibility. | Low | Medium | Pin all deps via lockfile in the sandbox image; cache the image; reproducibility rig validates this. |

---

## Open questions to track

### Trigger-based, not deadline-based

- **`packages/trace-format/` license / public-release / naming** — spec written at [`../packages/trace-format/SPEC.md`](../packages/trace-format/SPEC.md); the W1 team builds against it as internal-use-only. License, public-release timing, and package naming are deferred until any of the following triggers: a customer asks "what license is this under?", a second team or repo needs to depend on the package, an OTel Gen AI working group asks to align, or a strategic decision to publish (post-MVP, with first design partners). See [`../packages/trace-format/README.md`](../packages/trace-format/README.md) and [`../TODOS.md`](../TODOS.md) TODO-4.

### Do not block W1

- Pricing model for first paying pilots — MVP doc lands on "platform fee + usage" but unit-of-value is TBD.
- Self-host vs managed cloud for first 5 customers — affects Phase 3 install docs.
- Eval-case generation from clusters: do we let the LLM-judge stub also propose new eval cases, or only humans? (Affects credibility of "auto-grown eval set".)
- Public-results post: do we publish the audit log export as a sidecar artifact, or embed it in the post? (Affects reviewer reproduction speed.)

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

---

## Version log

Reverse-chronological. Each entry is a one-line headline + bullets of what
shipped that revision. Deeper detail lives in the corresponding PR and
`CHANGELOG.md` entry.

### v3.27 — 2026-05-12 — PR #85 (`feat/real-ui-loop`) shipped: 8.4.7 + 8.4.8 + TODO-36..43 + beyond-plan UI surfaces

- **8.4.7 + 8.4.8 closed.** Iteration runner now writes per-case `traces` + per-cluster `failure_clusters` + `gate-run-started` / `gate-run-completed` / `cluster-created` / `skill-version-created` / `proposal-created` audit rows. Per-iteration drill-down at `/workflows/[wfId]/iterations/[idx]` shows the failed-first case roster (predicted vs expected vs fold + trace click-through + the agent's per-case rationale text).
- **All TODO-36 → TODO-43 closed:**
  - 36 workflow delete (FK-safe cascade across 9 tables, audit kept) + description edit — new Settings tab.
  - 37 deploy / rollback buttons on the proposal sidebar.
  - 38 eval-case manual add (hand-authored provenance) + per-row two-step delete.
  - 39-43 Health polish — primary-by-signal heuristic, word-boundary sidebar truncation + dual-line workflow rows, "+N runs since launch" copy, in-flight banner with pulsing dot + per-row pill, first-time empty state with both on-ramp CTAs.
- **Beyond plan (surfaces from the mock audit `www/preview/s26-rk7p3/`):**
  - Operate workflow sub-tab (mocks 06/09/10/10b) — `spec.ui`-driven primitive composition.
  - Operator shell (mocks 28-31) at `/operator/[workflowId]` — separate product surface, no AgentOS sidebar, top-bar workflow switcher.
  - Connect-existing-agent on-ramp (mocks 24-26) — 3-step wizard, manual path functional, OTel/Upload show planned-shape page with `not wired yet` pills.
  - Workspace-level Traces list (mock 15) + sidebar entry.
  - Triggers / Integrations / Permissions workflow sub-tab stubs (mocks 12/13/14) — planned-shape pages; Permissions is partially wired (renders the live reviewer-from-spec).
- **Per-case agent rationale plumbed end-to-end.** `predict_label`'s `rationale` argument now rides through `ReplayResult` → `EvalCaseOutcome` → `traces.metric_outputs.rationale` → the per-iteration UI. Failed cases on the drill-down show the agent's domain reasoning verbatim — turns val_score from a magic number into something the operator can reason about.
- **Bugs found + fixed in browser-verify pass:** `ON CONFLICT (fingerprint)` partial-index predicate mismatch on the cluster insert (was raising 502 on every iteration); CORS `allow_methods` missing PATCH + DELETE (the new endpoints would have failed from the web). 1489 kernel tests pass throughout; ruff + tsc clean.
- **Remaining gaps captured in [TODOS.md](../TODOS.md):** TODO-44 per-cluster reasoning summary (next-best signal now that rationales are captured); TODO-45 stale-running iteration sweep; TODO-46 new-workflow review-before-commit; TODO-47 baseline-complete landing; TODO-48 skills library workflow filter; TODO-49 recent-activity feed. Real OTel ingest (TODO-10) and multi-tenant retrofit (TODO-1) remain the two biggest unblocked items.

### v3.26 — 2026-05-12 — W8 Track 4 rows 8.4.7 + 8.4.8 added (dead-end-tabs + drill-down)

- **Browser-audit of the Track 4 surface** surfaced three tabs that look functional but stay empty for NL-gen'd workflows (Failures / Traces / per-workflow Audit) because the iteration runner persists only `iterations` / `skills` / `skill_versions` / `proposals` — never `audit_entries` / `failure_clusters` / `traces`. Audit B2 (workflow Audit tab leaving context) is fixed in code; the dead-end-tabs deeper issue is now plan-tracked as **8.4.7** under Track 4.
- **Per-iteration drill-down** — the lift chart shows `val_score` per iteration but no way to see which eval cases failed and what the agent predicted. Without this, val_score is a magic number. Plan-tracked as **8.4.8**: new route `/iterations/[iterationId]` showing case-level prediction vs expected, plus per-case trace links.
- **Lifecycle controls + polish moved to [TODOS.md](../TODOS.md)** as TODO-36 → TODO-43:
  - TODO-36: Workflow delete + description edit
  - TODO-37: Deploy / rollback button on approved proposals
  - TODO-38: Eval-case manual add + delete
  - TODO-39: Health primary-workflow heuristic ("primary = most active", not creation order)
  - TODO-40: Sidebar workflow title truncation (no longer mid-word; show id when ambiguous)
  - TODO-41: MetricCards "+N all-time" delta copy clarification
  - TODO-42: In-flight iteration indicator (visible while a long-running iteration is going)
  - TODO-43: First-time-user empty state on Health page (0 workflows)
  Each gets the full TODOS-format treatment (What / Why / Pros-Cons / Context / Effort / Priority / Depends-on).
- These items are independent of the W8 YC-demo bar; tracking them outside W8 keeps the demo critical path lean.

### v3.25 — 2026-05-12 — W8 Track 4: rip mocks + seed DB + full gen → eval → propose UI loop

- **Pivot from Track 0 mock data to real product code.** Track 0 (v3.24) shipped the primitive renderers wired to hand-curated mock data plus three positioning-mock workflows. To move toward a real usable product with no demo leftover in runtime, Track 4 inserts a six-row sequence after Track 0:
  - **8.4.1 (Rip):** delete `apps/web/lib/primitives-mock-data.ts`, `workflows/[wfId]/mocks.ts`, `WORKFLOW_MOCKS` merge in `skills/page.tsx`, `<MockBanner />` + version-pill rendering in workflow layout. Replace with honest empty states.
  - **8.4.2 (Seed):** `scripts/seed_demo.py` + `make seed-demo` writes labour / contract / support as real workflow rows via the same kernel code path the gen endpoint uses. Idempotent. Lives outside runtime — production code reads from DB only.
  - **8.4.3 (Gen):** new `POST /api/nl-gen/generate` endpoint; wires `/workflows/new` form to a live Server Action; replaces the disabled-button placeholder. Closes the "UI wire-up planned for W8" comment in `workflows/new/page.tsx`.
  - **8.4.4 (Eval):** new `/workflows/[wfId]/eval-cases` page + `GET /api/workflows/{id}/eval-cases`. First UI surface for eval cases generated by NL-gen.
  - **8.4.5 (Propose):** "Run iteration" button → `POST /api/workflows/{id}/iterations/run` → background task → proposal appears in existing `/proposals` UI. Closes the loop.
  - **8.4.6 (Layer D, promoted):** kernel-side resolver joins `WorkflowSpec.ui.primitives[].source` against latest iteration outputs. Replaces empty states with real numbers. Previously deferred to Phase 2 in v3.24; now in-W8.
- **Three approaches considered (toggle / .env var / seed data); seed data picked.** Toggle and .env var both put `if (demoMode)` branches throughout the codebase permanently — every callsite carries demo paths, removal is a full sweep. Seed data in DB puts demo state in the database and leaves production code unaware of "demo." To go clean: run with empty DB.
- **Track 4 exit gate:** reviewer clones, runs `make dev-up` + `make seed-demo`, lands at `/workspaces/acme/`, clicks through New workflow → Eval cases → Run iteration → Inbox → Approve → Audit, end-to-end in the UI with zero hard-coded demo content in runtime code.
- **W8 exit criteria updated** to include Track 4 gate alongside YC video / benchmark posts / reproducibility / τ³ threshold.
- **Tradeoff acknowledged:** Track 1 video / website screenshots (8.1.1, 8.1.4) now depend on real seeded-workflow + iteration data rather than mock data. The seed script + 8.4.5 iteration trigger make this cheap, but Track 1 timing shifts behind Track 4 reaching 8.4.6 (or accepting empty-state Overview in the video).

### v3.24 — 2026-05-11 — W8 Track 0 (UI primitive renderers) planned ahead of Track 1

- **Track 0 inserted** in §W8 ahead of demo materials with three rows:
  - **8.0.1 (A):** 8 React leaf components for the typed UI primitives (`MetricCards` / `TimeSeriesChart` / `TableView` / `AlertList` / `KanbanBoard` / `ConversationView` / `SideBySideView` / `DocumentReader`) under `apps/web/app/components/primitives/`. Showcase page at `/workspaces/[wsId]/primitives` with hand-curated demo data from `27-primitives.html`.
  - **8.0.2 (B):** `ScheduleGrid` Pydantic class added to `packages/trace-format/src/ownevo_format/ui_primitives.py` (closes the 8-vs-9 gap between schema and marketing preview) + React component.
  - **8.0.3 (C):** demand-prediction workflow Overview reads `WorkflowSpec.ui.tabs[0].primitives` via mock data resolver (`apps/web/lib/primitives-mock-data.ts`); replaces the "W8.1.1-pending" placeholder banner. Track 0 exit: reviewer sees a live-looking workspace; demo data hand-curated.
  - **8.0.4 (Library build-out):** new `/workspaces/[wsId]/skills` page mirroring `11-skills-registry.html` + new kernel endpoint `GET /api/skills`; sidebar Library section gains **Skills** + **Views** links above Audit. Closes the "per-skill detail page exists but no index" gap.
- **Layer D (real resolver) deferred to Phase 2** as `TODO-35`. Three candidate designs documented (post-iteration render-data step / structured AgentEvent subtype / kernel-side `source` resolver); choice depends on τ³ + multi-tenant retrofit order.
- All CSS for primitives already shipped in `apps/web/public/styles/primitives.css` — shared with `www/preview/s26-rk7p3/`, no duplication.
- **Companion infra** landed earlier today on `main` via PR #83: root-level `docker-compose.yml` + `make dev-up` / `dev-down` / `dev-logs` / `dev-ps` brings up postgres + kernel + web. Banner-title fix in `apps/web/lib/api.ts` (`kernelError()` returns `{ title, detail }`): 404 on workflow → "Workflow not registered." / other HTTP errors → "Kernel API error." / network → "Kernel API not reachable." Replaces the misleading always-"not reachable" label.
- **Stale `docs/W7_SLICE.md` deleted** — all rows it deferred to W8 were already shipped in PRs #65 + #68; doc had no remaining function.

### v3.23 — 2026-05-09 — v0.6.0 released

- **v0.6.0 tagged** on `main` (commit `92744b7`). Bundles W7 Track 1 (workspace shell, slices 1-12), τ³ first autonomous lift (+11.8%, PR #77), Deploy/Rollback end-to-end (PR #80), `skills.head_version_id` semantics fix (PR #79), sidebar repoint (PR #79), W6 row 6.1 dry-run (PR #81), BL.3 compaction + `/no_think` (PR #67 + #61), Ollama `/api/chat` native client (PR #82).
- **CHANGELOG** `[Unreleased]` renamed to `[0.6.0] — 2026-05-09`; fresh `[Unreleased]` block opened above.
- **README** Status section updated: "pending merge" removed, v0.6.0 bullet added, "Next:" repoints to TODO-29 30-day M5 replay + τ³ NeoSigma + W8 video.
- **/review fixes on `dryrun/w6-preview-nlgen-demo`:** handler leak in `--progress` `addHandler` (now `try/finally` + `removeHandler`); float-precision epsilon on `--require-lift` gate (`0.6 - 0.2 = 0.39999…` was falsely failing `--require-lift=0.4`); 4 new tests (25 total, was 21); `aria-disabled="true"` on disabled button.

### v3.22 — 2026-05-09 — TODO-28 closes W6 row 6.1 validation gate

- **Dry-run shipped** on `dryrun/w6-preview-nlgen-demo` (PR #81). Three runs against `demand-prediction` (haiku 4.5 agent, Sonnet 4.6 proposer): 34.2 s `[0.20, 0.80, 0.60]`; 17.2 s `[0.20, 1.00, 1.00]`; 15.2 s `[0.20, 1.00]` post-§3 fix. Total dry-run wall under 2 minutes — **5-minute reviewer budget holds with margin**.
- **§1** Storyboard pointed at the legacy `/workflows/preview` URL → rewritten to the W7-slice-5 `/workspaces/acme/workflows/new` form.
- **§2** `Run baseline ›` button tooltip referenced a non-existent `POST /api/nl-gen/generate` → rewritten to point at the CLI demo path.
- **§3** Cycle-2 regression risk on a live take → storyboard's recommended command switched from `--cycles 3` to `--cycles 2`, narrative collapsed to "baseline → proposer edit → agent reads the edit", wall-time expectation 84 s → 12–25 s. The cluster → instruction → lift narrative is intact; the haiku-noisy third cycle is excised.
- **§4** Silent CLI during 17–34 s of agent passes → new `--progress` flag in `scripts/nl_gen_demo_loop.py` attaches a stderr `StreamHandler` to `ownevo_kernel.nl_gen.loop` so the existing per-cycle `logger.info` line streams as the cycle ends. JSON on stdout unaffected — machine-parseable runs that don't pass the flag still get a single document.
- **Tests:** 25 CLI tests passing (4 added by /review: `--require-lift` rejection, exact-threshold boundary, redact-None-instruction_edit assertion, `aria-disabled`); 34 across the loop + CLI test files.
- **Row 6.1 validation gate cleared.** Full dry-run report at `docs/W6_PREVIEW_DRYRUN.md`; raw run logs at `docs/W6_PREVIEW_DRYRUN_artifacts/`.

### v3.21 — 2026-05-09 — W7 Track 3 τ³-bench retail substrate + first +10pp lift

- **P1.5 kernel migration done** (M1–M10, PR #77): `SandboxedTauBenchRunner` + `failure_analyzer.py` + `tau2_patches.py` sitecustomize inside `ownevo-sandbox-tau3:0.1.0`. `LocalDockerSandbox` gained `network=` ctor arg + `extra_env` run() arg. New scripts: `run_tau3_loop.py`, `tau3_baseline.py`, `tau3_register.py`, `tau3_ingest.py`, `tau3_inspect_task.py`.
- **Three eval-path infra-error fixes** (empty tool_call args / NL-evaluator markdown fences / `@dataclass` import-time crash) lifted the post-fix Sonnet baseline 0.80 → **0.85**.
- **P2 batch-1: val_score 0.85 → 0.95 (+10pp absolute / +11.8% relative)** at iter 11 on skill v38 — a prompt-only change (no `HarnessState` fields, no `generate_next_message` override; three rules in `AGENT_INSTRUCTION`). Batch-2 ran 10 more cycles with no break-through past 0.95 → strong saturation signal.
- **Per-task trace persistence** (commit `daef4c2`): runner serializes each tau2 `Simulation` (full message history + reward_info + termination_reason) through stdout JSON; `persist_gate_run` writes one `traces` row per task per iteration. Pre-fix iters 0–19 are unrecoverable.
- **Local-loop-agent variant** `qwen/qwen3.6-35b-a3b` on LMS desktop reproduced val=0.85 across two PASSes (cycles 2 + 5 of 10) — preliminary "free 35B model can drive the loop" evidence.
- **6-model diagnostic sweep:** gemma4:26b is the only sweep candidate to drive cleanly; qwen3:32b / granite4.1:30b / devstral-small-2 / ministral-3-14b / glm-4.7-flash failed for distinct reasons documented in `docs/TAU3_LOCAL_TESTPLAN.md`.
- **New TODOs:** TODO-31 (`skills.head_version_id` should track best-gate-pass not latest write), TODO-32 (Pass³ stretch), TODO-33 (task 33 + 49 failure analysis).
- **Open Track 3 work:** NeoSigma reproduction + `benchmarks/tau3-results-2026-Q3.md`.

### v3.20 — 2026-05-08 — BL.3 in-call conversation compaction + 30-day M5 replay v6 hero

- **PR #67** added `compact_anthropic_messages` + `compact_openai_messages` (mechanical replacement of older `tool_result` blocks with stubs above 80k chars; preserves `tool_use` + kickoff + system; same-list-identity no-op below threshold to keep prompt cache warm). Both BL.3 runners call it at the top of each loop iteration. 27 unit tests.
- **PR #61** mirrored the `/no_think` directive into `run_agent_turn_openai` (was only in the A4.4 single-turn gate before).
- **PR #63** defaulted `api_key="local"` for `--anthropic-base-url` in `meta_eval.py` + `llm_judge_approver_eval.py`.
- **W6 30-day replay validation:** pre-compaction every variant hit `Context size has been exceeded` 28+ times; v4 (PR #67 active, 48k LMS context) ran 27 iterations with zero context errors.
- **v6 hero artifact** (Sonnet 4.6 cloud + Opus 4.7 judge): 30+30+30 iterations across A/C/D, condition C hit 4 gate-passes / best_ever 0.4077 / WRMSSE 1.046 (-19.5% vs static 1.300), condition D hit 7 gate-passes all judge-rejected — the "cost of safety" frame in data. ~$15-20 across the run.
- **Hand-engineered `skill_v2`** (PR #72) lands at WRMSSE 1.024 — 2% better than v6's agent-driven lift on top of v1.
- Operational followups in `docs/W6_30DAY_REPLAY_NOTES.md`.

### v3.18 — 2026-05-07 — W7 Track 1 slices 1-6 shipped (PR #65)

- **PR #65** on `feat/w7-plan` closed PLAN rows 7.1.1, 7.1.2, 7.1.3, 7.1.5, 7.1.6, 7.1.7, 7.1.8 — six of the thirteen Track 1 rows; 7.1.4 already at W5.1, polish pass deferred; 7.1.9–7.1.13 deferred per `docs/W7_SLICE.md` (since closed in v3.19).
- **New customer-facing surface** at `/workspaces/[wsId]/...` with five new kernel endpoints (`/api/workflows`, `/api/workflows/{id}/iterations`, `/api/workflows/{id}/failure_clusters`, `/api/audit`, `/api/audit/verify`), 16 new integration tests, 5 new OpenAPI schemas.
- W2.5/W5.5 routes preserved under a `(legacy)` route group with URLs unchanged. `/` now redirects to `/workspaces/acme` (slug cosmetic per D4).
- Track 3 (τ³-bench dataset + harness + NeoSigma reproduction) deferred — separate session for the Sierra dataset + multi-turn agent harness shape.

### v3.19 — 2026-05-08 — W7 Track 1 fully closed (slices 7-12, PR #68)

- Six rows previously deferred to W8: 7.1.4 (proposal review under workspace shell + cluster→proposal linkage), 7.1.9 (per-trace step inspection + per-workflow trace list), 7.1.10/7.1.11 (per-skill detail with prompt vs code variant), 7.1.12 (`AgentAnatomy` three-column pane), 7.1.13 (`make revert-skill` runbook).
- New endpoints: `GET /api/workflows/{id}`, `/api/workflows/{id}/traces`, `/api/workflows/{id}/skills`, `/api/skills/{id}`, `/api/traces/{id}`.
- Companion fixes: `globals.css` missing `/*` opener at the W5.5 section header (was breaking production build); openapi.yaml stale `/api/skills/{id}/versions` + `/api/skills/{id}/revert` stubs removed.
- v3.18 (rolled-up): W7 Track 1 slices 1-6 (PR #65) — workspace surface at `/workspaces/[wsId]/...` with five new endpoints, 16 new integration tests; `/` redirects to `/workspaces/acme`.

### v3.17 — 2026-05-07 — v0.5.0 release tagged

- Bundles W5.1–W5.5 (PRs #54–#59): approval surface polish, LLM-judge stub approver + 30-case eval, NL-gen failure clustering wire-up, 7-day M5 replay scaffold, meta-eval as quality gate + coverage badge + `/workflows/preview` UI.
- W5.3 + W5.5 entries corrected from [0.4.0] to [0.5.0] in CHANGELOG.md.

### v3.16 — 2026-05-07 — F14k weakens the F14j Apple-Metal-vs-CUDA kernel-drift hypothesis

- 4-trial laptop LMS retest of `unsloth/granite-4.1-8b` clusters credit-risk on the 0.40 gate boundary (0.33 / 0.25 / 0.50 / 0.50) — boundary noise, not systematic ~0.17 drift.
- Q4_K_M sibling outperforms on credit-risk (0.58) but underperforms on demand-pred (0.40); FP8 unloadable in LMS.
- Practical doc impact: `apps/kernel/README.md` + `CLAUDE.md` no longer call granite "desktop-CUDA-only"; honest framing is "passes 3/3 reliably on desktop, sits on the laptop credit-risk boundary"; for laptop iteration prefer `qwen/qwen3-4b-2507`.

### v3.15 — 2026-05-07 — v0.4.0 release tagged

- Bundles W4 NL-gen pipeline closed (A4.1–A4.6) + W3 Track B failure clustering (B3.1–B3.5) + the broader A4.4 local-model sweep (PRs #41–#52).
- W3 Track B exit gate run before the cut: `make cluster-label-eval` → **agreement 0.85 (17/20)** with judge `claude-opus-4-7` vs labeler `claude-sonnet-4-6`, well above the W3 Track B ≥0.7 contract.

### v3.14 — 2026-05-06 — B3.5 cluster-label LLM eval shipped

- New `apps/kernel/src/ownevo_kernel/clustering/label_eval/` package: `judgment.py` schema, `fixtures.py` (20 hand-authored M5 cases across the failure-mode taxonomy), `judge.py` (single-turn forced Anthropic tool-use, default sonnet 4.6 per D4), `runner.py` (parallel labeler + judge with per-`dominant_hint` slicing).
- New `make cluster-label-eval` CLI; `--require-agreement 0.7` gate runs on demand only (not in CI per project policy that CI doesn't consume API keys). ~$1.20/run.
- 64 new tests; kernel suite 1009 passing.

### v3.13 — 2026-05-06 — B3 post-review hardening

- Atomicity: persist + promote wrapped in single outer `conn.transaction()` (no orphaned cluster rows on partial failure).
- Idempotency: `fingerprint TEXT` + partial unique index on `failure_clusters`, `ON CONFLICT DO NOTHING` in `_insert_cluster`; migration `0002_failure_cluster_fingerprint.sql`.
- `strict=True` in HDBSCAN persistence zip; `abs(actual_mean)` in bias threshold; 120-char cap on LLM labels.
- `test_agent_tools_run_pipeline.py` moved to nightly with `docker pull` pre-warm (was blowing 15s timeout on ubuntu-latest cold-pull).

### v3.12 — 2026-05-05 — B3.1 + B3.2 + B3.3 failure clustering pipeline shipped

- New `m5_failure_analyzer.py` → top-k worst-predicted M5 series with parsed hierarchy + peak-error day offset + `feature_gap_hints` + embedding-ready `text_signature`.
- New `clustering/` module: 4-stage pipeline behind `Embedder`/`Reducer`/`Clusterer`/`Labeler` Protocols; quality gate runs BEFORE the LLM labeler so we don't pay tokens on rejected runs.
- New `eval_cases/from_cluster.py` (`promote_cluster_to_eval_cases` writes `provenance=cluster-derived` rows).
- New `make m5-cluster-failures` CLI; `--real` flag flips deterministic stubs to ST + UMAP + HDBSCAN + Anthropic.
- 72 new tests; kernel suite 1133 passed / 12 skipped.

### v3.11 — 2026-05-05 — A4.1 NL → eval case set shipped

- New `nl_gen/eval_case_set.py` (`EvalCaseSet` + `GeneratedEvalCase` Pydantic schema; size 10-30; balanced-classes ≥3/≥3; `schema_version="0.1"` pre-A4-end freeze).
- New `eval_generator.py` (single-turn Anthropic tool-use, mirrors A3.2), `eval_replay.py` (in-process seam over `run_simulation`), `eval_persistence.py` (single-transaction persistence over `add_eval_case`).
- 3 hand-authored fixtures (12 cases each, every `known_past_misses` phrase covered).
- 70 new tests; kernel suite 629 passing.

### v3.10 — 2026-05-04 — W2.5 approval queue scaffold shipped

- New `approvals/` (state-machine for `gate-passed → approved-awaiting-deploy` / `→ rejected`, row-locked transactions, audit + eval-case-from-rejection-comment seam).
- New `api/` (FastAPI under new `api` extra; 4 proposal endpoints + health; CORS to `localhost:3000`).
- New `apps/web/` (Next.js 15 App Router; routes `/inbox` + `/proposals/[id]`; Server Components + Server Actions; line-level LCS skill diff).
- Make targets: `api`, `web-dev`, `web-build`, `seed-approval-demo`. 28 new tests + manual E2E.

### v3.9 — 2026-05-04 — W2.7 non-M5 substrate proof shipped

- `baselines/labour_v1/skill.py` + `benchmark/labour.py` + end-to-end smoke `test_substrate_non_m5.py`.
- The skill is stdlib-only so the test uses the sandbox's default `python:3.11-slim` image — proves the substrate handles a workflow whose skill has no third-party deps without a domain-specific Dockerfile. Phase 1 exit gate cleared.

### v3.8 — 2026-05-04 — Bootstrap loop (BL.1-3) inserted before W3/W4 Track B

- Seed script + agent prompt + entrypoint to run one round of M5 improvement without clustering (auto-harness style). Gate runs in bootstrap mode (no prior_eval_task_ids; DB-authoritative best_ever from run 2 onward).
- B4.1 updated to reflect direct trace reading before B3.3 seeds eval cases.

### v3.7 (and earlier) — W2 spine + reproducibility CI

Older revisions are folded into release tags and the corresponding CHANGELOG
sections; for full detail see `CHANGELOG.md`. Highlights:

- **#11d** — M5 reproducibility nightly (Buildx + GHA layer cache scoped to `m5-sandbox`); TODO-7 layer (b) shipped.
- **#11c** — sandbox flip: `Dockerfile.m5` with pinned numpy / pandas / lightgbm; `LocalDockerSandbox.run` gains `extra_volumes`; `SandboxedM5BenchmarkRunner` bit-identical to in-process baseline under matched pins.
- **#11b** — seasonal-naive skill bodies replaced with real LightGBM (lag-28 + day-of-week + cat_id features); WRMSSE 0.988 → 0.777 on synthetic fixture.
- **#11a** — `M5BenchmarkRunner` + 6 SKILL_FORMAT-compliant skill files + `scripts/m5_baseline.py`.
- **W2 spine** (PRs #3, #4, #6, #8, #9): eval-cases + audit log + metric.py + held-out fold + BenchmarkRunner Protocol + core `run_gate` + kernel-side agent tools + sandbox classifier hardening (TODO-17).
- **v3.x decisions** apply CEO-review v4.3: D2 audit reframe, D3 local Docker sandbox, D4 single-tenant for MVP, D5 τ³ B-frame, D6 `core/` 2-day spike, D7 NL-gen meta-eval.

