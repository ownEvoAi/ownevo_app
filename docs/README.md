# docs

Repo-specific architecture notes. The MVP plan, scope decisions, and stack rationale are in the product design docs (not included in this repo).

Use this directory for things that only make sense once you're inside the code: schema diagrams, ADRs, runbooks, dev setup.

## Contents

- [`PLAN.md`](PLAN.md) — week-by-week build plan, phase decisions, milestone status. Revisit when the W1-W8 sequence shifts.
- [`SCHEMA.md`](SCHEMA.md) — Postgres schema (workflows, iterations, audit_entries, etc.) with retrofit-friendly notes.
- [`SKILL_FORMAT.md`](SKILL_FORMAT.md) — `SkillRecord` spec + retention contract conventions.
- [`STATE_MACHINES.md`](STATE_MACHINES.md) — proposal lifecycle + gate-runner state diagrams.
- [`SPIKE-RESULT.md`](SPIKE-RESULT.md) — pre-W1 spike notes on the evolution harness reuse decision (archive value).
- [`W6_DEMO_STORYBOARD.md`](W6_DEMO_STORYBOARD.md) — 90-second demo storyboard the W6/W7 surfaces are built against. Read before touching any customer-facing screen.
- [`W7_SLICE.md`](W7_SLICE.md) — W7 Track 1 sub-slice plan (which PLAN.md rows landed where, video-critical-path framing, deferred items).
- [`local-model-testing.md`](local-model-testing.md) — methodology + findings for the local-model dogfood track. Largest doc by far (F1-F14k); the source of truth for "which local model can drive this gate / loop". Read this before any local-model sweep.
- [`runbooks/demo-rollback.md`](runbooks/demo-rollback.md) — 5-minute operator playbook for reverting a bad skill HEAD before a live demo. Backed by `make revert-skill`.
- [`api/openapi.yaml`](api/openapi.yaml) — OpenAPI spec for the kernel REST + SSE seam.
