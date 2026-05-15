# docs

Repo-specific architecture notes. Use this directory for things that only make sense once you're inside the code: schema diagrams, runbooks, dev setup.

## Contents

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — one-document system tour. Start here.
- [`SCHEMA.md`](SCHEMA.md) — Postgres schema (workflows, iterations, audit_entries, etc.) with retrofit-friendly notes.
- [`SKILL_FORMAT.md`](SKILL_FORMAT.md) — `SkillRecord` spec + retention contract conventions.
- [`STATE_MACHINES.md`](STATE_MACHINES.md) — proposal lifecycle + gate-runner state diagrams.
- [`HARNESS.md`](HARNESS.md) — improvement-loop harness design rules (proposer, agent, gate).
- [`BENCHMARK_ARCHITECTURE.md`](BENCHMARK_ARCHITECTURE.md) — multi-benchmark substrate design (M5 + τ³).
- [`local-model-testing.md`](local-model-testing.md) — methodology + findings for the local-model dogfood track. The source of truth for "which local model can drive this gate / loop". Read this before any local-model sweep.
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — all three deployment paths (local dev, Docker compose, Fly.io), env-var reference, migration table, health checks, and DEMO_MODE behaviour.
- [`runbooks/demo-rollback.md`](runbooks/demo-rollback.md) — 5-minute operator playbook for reverting a bad skill HEAD before a live demo. Backed by `make revert-skill`.
- [`runbooks/fly-deploy.md`](runbooks/fly-deploy.md) — Fly.io first-run deploy guide.
- [`api/openapi.yaml`](api/openapi.yaml) — OpenAPI spec for the kernel REST + SSE seam.
