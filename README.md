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

**Week 2 (W2.6).** W1 substrate complete (DB schema, sandbox, skills, traces, M5 loader). W2 spine complete: eval cases, audit log, agent tools, regression gate, loop-stuck observability, M5 `BenchmarkRunner`, and LightGBM baseline skills. Next: sandbox flip to `LocalDockerSandbox` + reproducibility CI (W2.6c/d), then W3 clustering pipeline.
