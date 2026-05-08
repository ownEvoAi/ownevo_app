# docs

Repo-specific architecture notes. The MVP plan, scope decisions, and stack rationale stay in `../../ownevo_docs/`.

Use this directory for things that only make sense once you're inside the code: schema diagrams, ADRs, runbooks, dev setup. Keep cross-references back to `ownevo_docs/` rather than duplicating.

## Contents

- [`PLAN.md`](PLAN.md) — week-by-week build plan, phase decisions, milestone status. Revisit when the W1-W8 sequence shifts.
- [`SCHEMA.md`](SCHEMA.md) — Postgres schema (workflows, iterations, audit_entries, etc.) with retrofit-friendly notes.
- [`SKILL_FORMAT.md`](SKILL_FORMAT.md) — `SkillRecord` spec + retention contract conventions.
- [`STATE_MACHINES.md`](STATE_MACHINES.md) — proposal lifecycle + gate-runner state diagrams.
- [`SPIKE-RESULT.md`](SPIKE-RESULT.md) — pre-W1 spike notes (lift candidates from `startup2026/`, archive value).
- [`local-model-testing.md`](local-model-testing.md) — methodology + findings for the local-model dogfood track. Largest doc by far (F1-F14j); the source of truth for "which local model can drive this gate / loop". Read this before any local-model sweep.
- [`api/openapi.yaml`](api/openapi.yaml) — OpenAPI spec for the kernel REST + SSE seam.
