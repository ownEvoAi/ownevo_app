<p align="center">
  <a href="https://ownevo.ai">
    <img src="https://ownevo.ai/logo-shield.svg" alt="ownEvo" width="96">
  </a>
</p>

# ownEvo — AgentOS

The improvement loop for core agents: every production failure becomes an eval case, every proposed fix is regression-tested against every prior fix, and a domain expert approves changes in plain language.

Build plan, scope decisions, and stack rationale: [`ownevo_docs/ownEvo_MVP.md`](../ownevo_docs/ownEvo_MVP.md).

## Layout

```
apps/
  kernel/        Python — agent runtime, eval harness, failure clustering, regression gate
    src/ownevo_kernel/
      agent_tools/   read_skill / write_skill / run_pipeline / read_metrics / analyze_failures
      audit/         append-only audit log writer (WORM-enforced in DB)
      benchmark/     M5BenchmarkRunner Protocol + synthetic fixture
      datasets/      M5 loader + WRMSSE metric
      eval_cases/    eval case registry
      gate/          3-step regression gate (regression / no-improvement / sandbox-error)
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

Python owns the IP (improvement loop, eval, clustering, regression gate). TS owns the product surface (approval UX, real-time UI, customer-facing dashboards). Joined by a REST + SSE seam.

## Status

**W2 complete — v0.1.0 (2026-05-04).** All W2 rows green on main. W1 substrate (DB schema, sandbox, skills, traces, M5 loader) + W2: eval cases, audit log, agent tools, regression gate, loop-stuck observability, M5 LightGBM baseline + sandbox image + nightly replay CI, Claude Agent SDK middleware, approval service + REST API + Next.js approval queue UI, non-M5 substrate proof (labour shift validator). Next: bootstrap loop seeding (BL.1-3), then W3 failure clustering pipeline (sentence-transformers + UMAP + HDBSCAN).

## A4.4 NL-gen smoketest — three-way model comparison (2026-05-05)

The Phase-2 quality gate (`make nl-gen-smoketest WORKFLOW=all SMOKE_ARGS='--from-fixtures'`) drives a Claude agent to predict the redacted bool label for each generated eval case, then scores via the workflow's metric. Calibration: target value = Sonnet 4.6 reference baseline minus 10pp margin.

|   | demand-prediction (recall, target 0.50) | credit-risk (balanced_acc, target 0.40) | contract-review (f1, target 0.75) |
|---|---:|---:|---:|
| haiku 4.5  | 0.20 ❌ | 0.25 ❌ | 0.91 ✅ |
| **sonnet 4.6** | **0.60 ✅** | **0.50 ✅** | 0.77 ✅ |
| opus 4.7   | 0.20 ❌ | 0.42 ✅ (+1.7pp, thin) | 1.00 ✅ |

Sonnet 4.6 is the only model that clears every gate by a clear margin. Opus is more capable on the leak-trivial workflow (contract-review) but more conservative on multi-step inference under partial info, costing recall on demand-prediction and giving credit-risk only 1.7pp margin (within run-to-run noise). Run cost: Sonnet ~$0.50, Opus ~$2 per full-suite run. See [PR #44](https://github.com/ownEvoAi/ownevo_app/pull/44) for the full diagnosis (sim-difficulty inspection, prompt-fix iteration, calibration story).
