<p align="center">
  <a href="https://ownevo.ai">
    <img src="https://ownevo.ai/logo-shield.svg" alt="ownEvo" width="96">
  </a>
</p>

# ownEvo — AgentOS

The improvement loop for core agents: every production failure becomes an eval case, every proposed fix is regression-tested against every prior fix, and a domain expert approves changes in plain language.

Build plan, scope decisions, and stack rationale: [`ownevo_docs/ownEvo_MVP.md`](../ownevo_docs/ownEvo_MVP.md). Release history: [`CHANGELOG.md`](CHANGELOG.md). Deferred work: [`TODOS.md`](TODOS.md). Live demo walk-through: [`docs/W6_DEMO_STORYBOARD.md`](docs/W6_DEMO_STORYBOARD.md).

## Layout

```
apps/
  kernel/        Python — agent runtime, eval harness, failure clustering, regression gate
    src/ownevo_kernel/
      agent_tools/   read_skill / write_skill / run_pipeline / read_metrics / analyze_failures
      api/           REST + SSE seam for the approval/diff surface
      approvals/     approval service (queue, decisions, expert sign-off)
      audit/         append-only audit log writer (WORM-enforced in DB)
      benchmark/     M5BenchmarkRunner Protocol + synthetic fixture
      clustering/    failure-clustering pipeline (B3.x — embed, UMAP, HDBSCAN, label LLM)
      datasets/      M5 loader + WRMSSE metric
      eval_cases/    eval case registry
      eval_runner/   workflow runner (agent solver, fixture/cases mode, OpenAI + Anthropic paths)
      evolution/     tracker → reflector → curator → proposer (improvement-loop core)
      gate/          3-step regression gate (regression / no-improvement / sandbox-error)
      middleware/    Claude Agent SDK middleware (trace + tool plumbing)
      nl_gen/        NL → WorkflowSpec / SimulationPlan / EvalCases / Metric (A3.x + A4.x)
      observability/ loop-stuck Slack alerter + learnings writer
      sandbox/       LocalDockerSandbox + SandboxRuntime Protocol
      skills/        skill registry, SKILL_FORMAT retention contracts
      traces/        trace collector
    baselines/
      m5_lightgbm/   LightGBM demand-forecast baseline (6-module skill: data_loader /
                     outlier_handler / feature_engineer / model_trainer / predictor / ensemble)
  web/           TS / Next.js — approval UX, diff viewer, lift chart, audit trail (W3+)
packages/
  trace-format/  Typed AgentEvent schema — Pydantic impl + canonical SPEC.md
infra/           Docker compose for local Langfuse + Postgres + ClickHouse + collector
docs/            PLAN.md, SCHEMA.md, SKILL_FORMAT.md, STATE_MACHINES.md, api/openapi.yaml
```

## Stack split

Python owns the core algorithms (improvement loop, eval, clustering, regression gate). TS owns the product surface (approval UX, real-time UI, customer-facing dashboards). Joined by a REST + SSE seam.

## Status

**W7 Track 1 complete (workspace customer skin) — pending merge.** The non-engineer demo flow is wired end-to-end: open a workspace, see the lift chart climb, click into Failures, follow a cluster to its proposal, approve, watch the audit chain extend.

**W7 Track 3 — τ³-bench retail kernel migration + first autonomous lift (in flight on `feat/tau3-local-bench`).** P1.5 kernel migration done (M1–M10): `SandboxedTauBenchRunner`, `tau3-retail-v1` workflow + 40-task retail test fold seeded, `failure_analyzer.py` parses tau2 traces, `run_tau3_loop.py` drives one improvement-loop iteration end-to-end. P2 batch-1 (Sonnet 4.6 as loop agent, autonomous) produced **val_score 0.85 → 0.95 (+10pp absolute / +11.8% relative)** at iter 11 on a prompt-only change in skill v38. Per-task trace persistence shipped mid-batch so future failures can be re-analyzed without re-running. Local-loop-agent variant (qwen/qwen3.6-35b-a3b on LMS) reproduces the 0.85 baseline twice across multi-cycle runs — preliminary evidence that a free 35B model can drive the loop. prior-art reproduction (full benchmarks doc + Pass³ stretch) is the remaining Track 3 work.

- **W1-W2 substrate** (v0.1.0–v0.1.1): DB schema, hardened LocalDockerSandbox, skill registry, trace collector, M5 loader, eval cases, audit log, agent tools, 3-step regression gate, loop-stuck observability, M5 LightGBM baseline + sandbox image + nightly replay CI, Claude Agent SDK middleware, approval service + REST API + Next.js approval queue UI.
- **W2-W3 Phase 3 lift** (v0.2.0): first agent-driven gate-pass on real M5 (Sonnet 4.6, +19% lift); first compound 2-step lift (+20.5% across iters 0→2). Cross-iteration failure memory (TODO-23) shipped to break repeat-failure loops.
- **W3 NL-gen pipeline** (v0.3.0, A3.x): NL description → `WorkflowSpec` → `SimulationPlan` (renderer + AST safety) → sandbox-runnable sim. Schemas frozen at v1.0.
- **W4 NL-gen pipeline closed** (v0.4.0, A4.1–A4.6): NL → eval cases (A4.1), NL → success metric (A4.2), Inspect AI integration + `make eval-replay` (A4.3), `make nl-gen-smoketest` validates 3 workflows end-to-end with a Claude agent in the loop (A4.4), token budget + determinism guardrails (A4.5), and the LLM-as-judge meta-eval with a 10-pair ground-truth set + `make meta-eval` — agreement 0.85 on the live opus 4.7 smoke (A4.6).
- **W3-W4 Track B failure clustering** (v0.4.0, B3.1–B3.5): embedding + UMAP + HDBSCAN clustering pipeline over `AgentEvent` failures, plus LLM-judge cluster-label evaluation — B3.5 live gate **0.85 agreement (17/20)** at the v0.4.0 cut (2026-05-07), well above the W3 Track B ≥0.7 contract.
- **W5 approval surface + benchmark infra** (v0.5.0, W5.1–W5.5): side-by-side diff + per-eval-case gate breakdown (W5.1), LLM-judge stub approver with 30-case ground-truth eval + ≥0.85 gate (W5.2), NL-gen failure clustering wire-up (W5.3), 7-day M5 replay scaffold (W5.4), and meta-eval as quality gate with coverage badge + `/workflows/preview` UI (W5.5).
- **W6 NL-gen end-to-end demo loop** (row 6.1): demo-day NL-gen surface live at `/workflows/preview` with the meta-eval gate inline.
- **W7 Track 1 customer-facing workspace skin** (slices 1-12, pending merge on `w7-track1-rest`): app shell + nav under `/workspaces/[wsId]/`, Health page with LiftChart, Failures view, Audit trail + verify-chain, "New workflow" entry, three positioning mocks (labour / contract / support); plus slices 7-12 — proposal moved into the workspace shell with cluster→proposal click-through, per-trace step inspection (all seven AgentEvent variants), per-skill detail (prompt + code variants), Workflow Agent-anatomy pane, and the `make revert-skill` demo rollback runbook.

Next: W7 Track 3 closeout (prior-art reproduction → Pass³ → benchmarks doc) and W8 polish.

## A4.4 NL-gen smoketest — model comparison (2026-05-05)

The Phase-2 quality gate (`make nl-gen-smoketest WORKFLOW=all SMOKE_ARGS='--from-fixtures'`) drives a Claude agent to predict the redacted bool label for each generated eval case, then scores via the workflow's metric. Calibration: target value = Sonnet 4.6 reference baseline minus 10pp margin.

| backend | model | demand-pred (recall ≥0.50) | credit-risk (balanced_acc ≥0.40) | contract-review (f1 ≥0.75) | cost |
|---|---|---:|---:|---:|---:|
| Anthropic | haiku 4.5 | 0.20 ❌ | 0.25 ❌ | 0.91 ✅ | ~$0.10 |
| Anthropic | **sonnet 4.6** | **0.60 ✅** | **0.50 ✅** | 0.77 ✅ | ~$0.50 |
| Anthropic | opus 4.7 | 0.20 ❌ | 0.42 ✅ (thin) | 1.00 ✅ | ~$2 |
| Ollama @ localhost | qwen2.5-coder:32b | 1.00 ✅ (always-True bias) | 0.50 ✅ | 0.89 ✅ | $0 |
| Ollama @ localhost | qwen3-coder:30b | 0.40 ❌ | 0.25 ❌ | 0.89 ✅ | $0 |
| Ollama @ localhost | **devstral-small-2** (24B) | **0.80 ✅** | **0.42 ✅** | 0.89 ✅ | $0 |
| Ollama @ localhost | gpt-oss:20b | err (max_tokens) | — | — | $0 |

**Two reference baselines:**
- **Sonnet 4.6** is the cloud reference. Only frontier model that clears every gate by a clear margin (Opus is more conservative; Haiku is too biased toward False).
- **devstral-small-2** is the local reference. 24B open-weight model running on a home Ollama matches/beats Sonnet across all 3 workflows — catches `winter-boot-spike-week-47` (the canonical past-miss Sonnet missed). Local proof that the gate isn't a frontier-only artifact.

Repro the local run: `OWNEVO_OLLAMA_HOST=http://<ollama-host>:11434 bash apps/kernel/scripts/run_a4_4_local_smoke.sh`. Config in `infra/litellm/ollama.yaml`. See [PR #44](https://github.com/ownEvoAi/ownevo_app/pull/44) and [`docs/local-model-testing.md` § F13](docs/local-model-testing.md) for the full diagnosis (sim-difficulty inspection, prompt-fix iteration, calibration story, LiteLLM gotchas).

**Broader local-model sweep (F14, 2026-05-06/07):** 19+ models pass 3/3 across LM Studio (desktop + laptop) and Ollama. Top desktop picks: `granite-4.1-8b` (33s, fastest), `google/gemma-4-e4b` (34s, smallest 3/3 at this tier), `mistralai/ministral-3-14b-reasoning` (47s), `qwen/qwen3.5-9b` via **Anthropic API** (52s — only passes through `/v1/messages`, see F14g), `qwen2.5-coder-32b-instruct` (98s). Laptop picks: `qwen/qwen3-4b-2507` (152s), `qwen/qwen3-1.7b` (826s, smallest 3/3). Full results + recommendations by class in [`apps/kernel/README.md`](apps/kernel/README.md) and [`docs/local-model-testing.md` § F14a-k](docs/local-model-testing.md). F14k (2026-05-07 evening) re-tested granite-4.1-8b on laptop and weakened the F14j "Apple Metal kernel drift" finding to "boundary noise on credit-risk" — desktop pick is unchanged, laptop should still default to `qwen/qwen3-4b-2507`.
