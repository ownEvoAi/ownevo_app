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

### Changed
- `apps/kernel/migrations/0001_substrate.sql` — `proposals` table gains
  `eval_score numeric(3,2)` (with `[0,1]` check) and `eval_rationale text`
  to align with the Pydantic `Proposal` model. Pre-stages the LLM-judge
  wiring that lands in W2; closes the schema-vs-types divergence flagged
  in `/review`. Migration not yet applied to any deployed DB so this is a
  forward-only edit, not a `0002_*.sql` follow-up.
- `apps/kernel/src/ownevo_kernel/types.py` — `FailureCluster` gains
  `centroid: list[float] | None = None` mirroring the SQL `centroid
  vector(384)` column. Without this, `extra="forbid"` would reject any
  `SELECT *` from `failure_clusters`. Most readers will continue to fetch
  via SQL-side pgvector ops; this is for the explicit-fetch path.

### Fixed
- `apps/kernel/migrations/0001_substrate.sql` — close TRUNCATE bypass on the
  `audit_entries` WORM trigger. Adds `BEFORE TRUNCATE … FOR EACH STATEMENT`
  trigger; row-level `BEFORE UPDATE/DELETE` triggers do not catch
  statement-level TRUNCATE. Verified end-to-end against
  `pgvector/pgvector:pg16`: TRUNCATE / DELETE / UPDATE all raise the WORM
  exception; row count preserved. Layer 2 (role grants in
  `0002_grants.sql`) remains the production answer; this guards dev/test
  envs where the app role is not enforced.
- `apps/kernel/migrations/0001_substrate.sql` — add `UNIQUE (proposal_id)`
  to `approvals` table. Without it, concurrent gate-runner retries or a
  race between an autonomous approval and a human click could insert two
  resolved decisions for the same proposal.
- `apps/kernel/migrations/0001_substrate.sql` — add
  `CHECK (severity IN ('high', 'medium', 'low'))` to `failure_clusters`.
  SQL had no constraint; any string would insert cleanly and only fail at
  the Pydantic read boundary.
- `apps/kernel/src/ownevo_kernel/types.py` — `FailureCluster.centroid` now
  enforces `min_length=384, max_length=384` via `Field`; `quality_score`
  gains `ge=0.0, le=1.0`; `AuditEntry.seq` gains `ge=1`. All three mirror
  existing SQL constraints that were missing from the Pydantic layer.
- `apps/kernel/src/ownevo_kernel/evolution/__init__.py` — `Reflector.reflect()`
  return type was `Learning` (an audit record) but the docstring described
  a three-way `FINALIZE / CONTINUE / REPLAN` decision. Introduced
  `ReflectionDecision` `StrEnum` and changed the return type to match.
  W2 implementations must now return an actionable decision; learning
  persistence is a side-effect of the concrete class.
- `packages/trace-format/src/ownevo_format/agent_event.py` — `SandboxErrorClass`
  promoted from `Literal["Timeout","OOM","Crash"]` to a proper `StrEnum`
  (single canonical definition). `apps/kernel/…/types.py` now imports from
  `ownevo_format` instead of duplicating the enum; eliminates the two-place
  update hazard when adding failure classes.
- `docs/STATE_MACHINES.md` — corrected stale field name `becomes_eval_case_id`
  → `became_eval_case_id` in the Invariants section.
- `docs/api/openapi.yaml` — four schema gaps closed: `Workflow.mode`
  (`WorkflowMode` ref), `Proposal.eval_score` / `eval_rationale`, and
  `LiftPoint.deployment_id` / `config_tag` / `model_id` (the `lift_series`
  view now joins `skill_deployments`; the typed client was silently dropping
  variant data). `Approval` schema added — `approver_type` was `NOT NULL`
  in SQL and required in Pydantic but invisible to API consumers.
- `packages/trace-format/src/ownevo_format/__init__.py` — `MonitorSeverity`
  and `MonitorName` type aliases added to public exports.

### Tests
- `apps/kernel/tests/state_machines/test_proposal_states.py` — new file.
  `docs/STATE_MACHINES.md` referenced it as existing; it did not. 9 unit
  tests (one per legal transition), 5 negative tests (illegal shortcuts /
  terminal-state guards), 1 audit-coupling assertion, 3 autonomous-mode
  tests. 64 tests total now pass.
- Boundary/constraint tests added: `SkillDeployment` traffic-weight bounds,
  `Proposal.eval_score` out-of-range, `Approval.decision` / `approver_type`
  invalid values, `Iteration.iteration_index` negative, `SkillVersion.version_seq`
  zero, `ToolCallResult.duration_ms` negative, `Citation.ref` zero,
  `SkillLoaded.version_seq` zero.
