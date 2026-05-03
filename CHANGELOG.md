# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
This project does not yet follow semver — pre-W1 substrate work runs against
moving targets. Versions on PyPI / npm publication are deferred (see TODOS.md
TODO-4 for `ownevo-trace-format` license + naming + publication path).

Sections per release: **Added** (new features), **Changed** (existing
behavior), **Deprecated**, **Removed**, **Fixed** (bug fixes), **Security**
(vulnerability fixes). Omit empty sections.

When updating: add an entry to `[Unreleased]` in the same commit as the code
change. On release, rename `[Unreleased]` to the version + date and start a
fresh `[Unreleased]` block above it.

## [Unreleased]

### Added
- `packages/trace-format/` — Pydantic implementation of the canonical
  AgentEvent schema (`SPEC.md`). 7 variants (`content_delta`,
  `reasoning_delta`, `tool_call_start`, `tool_call_result`, `skill_loaded`,
  `citation`, `monitor_signal`) with discriminated-union parsing via
  `TypeAdapter`. `is_*` helpers return `TypeGuard[Variant]` so static
  checkers narrow after the guard. D3 sandbox-failure invariants
  (`status` / `error` / `error_class`) enforced via `model_validator`.
- `apps/kernel/src/ownevo_kernel/types.py` — Pydantic mirror of
  `docs/SCHEMA.md` / `0001_substrate.sql`. 12 entity models, 6 `StrEnum`s.
  `ProposalAction` extends with `regression_gate` per D6 (gate outcomes flow
  through the same proposal pipeline as skill mutations).
- `apps/kernel/src/ownevo_kernel/evolution/__init__.py` — 4-stage Protocol
  scaffolding (`Tracker`, `Reflector`, `Curator`, `Proposer`). Reference
  architecture preserved from the W1 spike; concrete implementations land
  in W2 once gate + clustering pipelines exist.
- `docs/SPIKE-RESULT.md` — W1 day-2 go/no-go ruling on the `core/` reuse
  spike. Outcome: NO-GO on wholesale lift, greenfield for W1-W2. Reasoning
  doc + reuse audit.
- uv workspace wiring (root `pyproject.toml` dependency-groups, per-package
  hatchling builds, `--import-mode=importlib` for cross-dir test
  collection). `pydantic>=2.7,<3` pinned at workspace level.

### Fixed
- `apps/kernel/migrations/0001_substrate.sql` — close TRUNCATE bypass on the
  `audit_entries` WORM trigger. Adds `BEFORE TRUNCATE … FOR EACH STATEMENT`
  trigger; row-level `BEFORE UPDATE/DELETE` triggers do not catch
  statement-level TRUNCATE. Verified end-to-end against
  `pgvector/pgvector:pg16`: TRUNCATE / DELETE / UPDATE all raise the WORM
  exception; row count preserved. Layer 2 (role grants in
  `0002_grants.sql`) remains the production answer; this guards dev/test
  envs where the app role is not enforced.
