# docs

Repo-specific architecture notes. Use this directory for things that only make sense once you're inside the code: schema diagrams, runbooks, dev setup.

## Contents

**Start here**

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — one-document system tour.

**Data & schema**

- [`SCHEMA.md`](SCHEMA.md) — Postgres schema (workflows, iterations, audit_entries, etc.) with retrofit-friendly notes.
- [`MIGRATIONS.md`](MIGRATIONS.md) — why each migration exists, in dependency order. The companion to `SCHEMA.md`.
- [`ENV_VARS.md`](ENV_VARS.md) — every environment variable read by the kernel, web app, sandbox images, or dev scripts.

**Subsystems**

- [`AUTH.md`](AUTH.md) — authentication + per-request workspace resolution (signed identity assertion → membership check → RLS).
- [`AUDIT_HARDENING.md`](AUDIT_HARDENING.md) — append-only audit log + SHA-256 hash chain (WORM grants, canonical export, `/api/audit/verify`).
- [`EVOLUTION_PROTOCOLS.md`](EVOLUTION_PROTOCOLS.md) — tracker / reflector / curator / proposer contracts for the improvement loop.
- [`HARNESS.md`](HARNESS.md) — improvement-loop harness design rules (proposer, agent, gate).
- [`SKILL_FORMAT.md`](SKILL_FORMAT.md) — `SkillRecord` spec + retention contract conventions.
- [`STATE_MACHINES.md`](STATE_MACHINES.md) — proposal lifecycle + gate-runner state diagrams.
- [`TRAIN_TEST_DISCIPLINE.md`](TRAIN_TEST_DISCIPLINE.md) — train/test fold discipline for the eval metrics.
- [`MULTI_METRIC_GATE_GAP.md`](MULTI_METRIC_GATE_GAP.md) — gap analysis: multi-metric gate vs. the current single pass/fail gate.
- [`OTEL_EMITTER_CONVENTIONS.md`](OTEL_EMITTER_CONVENTIONS.md) — OpenTelemetry span conventions the kernel emits for its own analysis events.

**Benchmarks & local models**

- [`BENCHMARK_ARCHITECTURE.md`](BENCHMARK_ARCHITECTURE.md) — multi-benchmark substrate design (M5 + τ³).
- [`local-model-testing.md`](local-model-testing.md) — methodology + findings for the local-model dogfood track. The source of truth for "which local model can drive this gate / loop". Read this before any local-model sweep.

**Deploy & operate**

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — all deployment paths (local dev, Docker compose, GHCR images, Fly.io), env-var reference, migration table, health checks, and DEMO_MODE behaviour.
- [`api/openapi.yaml`](api/openapi.yaml) — OpenAPI spec for the kernel REST + SSE seam.
- [`runbooks/demo-rollback.md`](runbooks/demo-rollback.md) — 5-minute operator playbook for reverting a bad skill HEAD before a live demo. Backed by `make revert-skill`.
- [`runbooks/fly-deploy.md`](runbooks/fly-deploy.md) — Fly.io first-run deploy guide.
- [`runbooks/ship-fix-to-langsmith.md`](runbooks/ship-fix-to-langsmith.md) — push an approved fix back to a LangSmith-origin prompt.
