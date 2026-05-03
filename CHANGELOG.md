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
- `infra/docker-compose.yml` — local Postgres 16 + pgvector. Migrations
  auto-applied on first boot via `docker-entrypoint-initdb.d`. Host port
  configurable with `OWNEVO_PG_PORT`; data persisted to a named volume
  (`docker compose down -v` to re-bootstrap). Production migration runner
  with version tracking is out of scope for the substrate.
- `apps/kernel/src/ownevo_kernel/db.py` — async connection helpers around
  asyncpg. `open_pool()` / `pool_scope()` for runtime use; `migrate()`
  applies all `apps/kernel/migrations/*.sql` in lexicographic order
  against a single connection (used by tests to bootstrap throwaway
  databases). Reads `OWNEVO_DATABASE_URL`; raises a clear setup error
  when unset.
- `apps/kernel/src/ownevo_kernel/sandbox/` — `LocalDockerSandbox` (D3
  reference impl) + `SandboxRuntime` Protocol. Hardened flags
  (`--network none`, `--read-only` rootfs + `/tmp` tmpfs, `--cap-drop ALL`,
  `--security-opt no-new-privileges`, `--memory` + `--memory-swap` with no
  swap, `--cpus`, `--pids-limit`). Failure classification matches the
  `ToolCallResult` contract: exit 0 → `status="ok"`; runner-caught Python
  exception (exit 100) → `status="error", error_class=None` (logical
  failure the agent owns); wall-clock kill → `Timeout`; cgroup OOM-kill
  (via `docker inspect State.OOMKilled`) → `OOM`; any other non-zero →
  `Crash`. Disambiguates Timeout from OOM (both surface as exit 137).
  Note: `--cap-drop ALL` strips `CAP_DAC_OVERRIDE`, so root in the
  container can't bypass host file permissions on the bind-mounted
  tempdir; the impl chmods the mount source to 0755 to compensate.
- `apps/kernel/src/ownevo_kernel/skills/` — YAML frontmatter parser
  per `SKILL_FORMAT.md` (handles both delimiter conventions: leading
  `---` block for markdown skills, module-docstring `---` block for
  Python skills). Registry writes `skills` + `skill_versions` in one
  transaction with `parent_version_id` linkage and `head_version_id`
  advancement; rejects `kind` mismatches across versions. `SkillFormatError`
  funnels every parse/validate failure so callers don't see Pydantic
  internals. `parse_stale_duration` covers `1h` / `24h` / `7d` / `never`
  for the retention-violation eval-case generator. PyYAML added as a
  kernel dep.
- `apps/kernel/src/ownevo_kernel/traces/` — `TraceCollector` +
  `trace_session` async context manager. Accumulates `AgentEvent`
  objects in memory and writes the whole stream as one row in
  `traces.events` (JSONB array) on context exit, including on exceptions
  — failing iterations still produce traces for the clustering pipeline.
  `make_event()` fills in `event_id` / `trace_id` / `timestamp` (and
  `iteration_id` when known) and validates against the discriminated
  union; `record()` rejects events with mismatched `trace_id` so a
  routing bug can't silently corrupt traces. `finalize()` is idempotent.
  ClickHouse / per-event row migration deferred to Phase 2.
- `apps/kernel/src/ownevo_kernel/datasets/m5.py` — M5 forecasting
  dataset loader. Path-and-shape only — no pandas on the kernel side
  (agent code in the sandbox brings its own). `load_m5(data_dir)`
  discovers the four CSVs and surfaces per-file metadata (columns,
  row counts) plus `date_range()` from `calendar.csv`.
  `make_sample_subset(catalog, num_items=)` slices an in-memory subset
  for fast eval-gate cycles using stdlib `csv`. Raises
  `M5DatasetError` with the missing filename when setup is incomplete.
- `apps/kernel/src/ownevo_kernel/audit/` — append-only audit log writer
  (W2.4 / D2). `append_audit_entry(conn, kind=, payload=, actor=,
  related_id=)` returns the typed `AuditEntry`; `kind` accepts the
  `AuditKind` enum or its string value. `export_audit_log(conn,
  since_seq=, kind=)` reads in monotonic `seq` order with optional
  filters for incremental and per-kind exports. `to_canonical_json`
  serializes sorted-keys + no-whitespace + UTF-8 — bytes are the
  contract so customers can `diff` exports byte-for-byte. WORM
  enforcement (UPDATE / DELETE / TRUNCATE blocked) lives in the schema
  per D2; the writer doesn't bypass it.
- `apps/kernel/src/ownevo_kernel/eval_cases/` — eval-case CRUD (W2.3).
  `add_eval_case(conn, provenance=, input=, expected_behavior=, ...)`
  returns the typed `EvalCase`; `get_eval_case(conn, id)` fetches one
  by id; `list_eval_cases(conn, workflow_id=, provenance=,
  is_test_fold=, cluster_id=)` filters and orders by `created_at` so
  the gate fail-fasts on older (more-load-bearing) cases first.
  Train/test discipline: the `is_test_fold` filter is what the gate
  uses to surface held-out cases; gate runner refuses to train on them.

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
- `apps/kernel/src/ownevo_kernel/sandbox/local_docker.py` — extract
  `_USER_EXCEPTION_EXIT_CODE = 100` as a named constant; runner script
  uses f-string interpolation so the runner side and the classifier
  side reference the same source of truth.
- `apps/kernel/src/ownevo_kernel/traces/collector.py` — `finalize()`
  serializes events with one `model_dump(mode="json")` + `json.dumps`
  pass instead of the previous `model_dump_json` → `json.loads` →
  `json.dumps` triple roundtrip.
- `apps/kernel/src/ownevo_kernel/datasets/m5.py` — simplify
  `make_sample_subset` row-collection branch (drop redundant
  `if iid in seen` guard that was always true after the preceding
  block).
- `apps/kernel/src/ownevo_kernel/skills/registry.py` — module docstring
  clarifies that `capability_tags` is refreshed on every re-registration
  while `kind` is locked at first registration.

### Tests
- `apps/kernel/tests/test_skill_format.py` — add coverage for malformed
  YAML (`"not valid YAML"`), non-dict YAML (`"must be a YAML mapping"`),
  and the `m` (minutes) unit in `parse_stale_duration`.
- `apps/kernel/tests/test_trace_collector.py` — add `make_event`
  validation tests (unknown `type`, missing required field) and an
  empty-session test that verifies `events == []` is persisted.



### Fixed
- `apps/kernel/migrations/0001_substrate.sql` — close TRUNCATE bypass on the
  `audit_entries` WORM trigger. Adds `BEFORE TRUNCATE … FOR EACH STATEMENT`
  trigger; row-level `BEFORE UPDATE/DELETE` triggers do not catch
  statement-level TRUNCATE. Verified end-to-end against
  `pgvector/pgvector:pg16`: TRUNCATE / DELETE / UPDATE all raise the WORM
  exception; row count preserved. Layer 2 (role grants in
  `0002_grants.sql`) remains the production answer; this guards dev/test
  envs where the app role is not enforced.
- Schema: `approvals` gains `UNIQUE (proposal_id)` (prevents double-approval
  race); `failure_clusters.severity` gains `CHECK` constraint.
- Pydantic: missing field constraints added to `FailureCluster` (`centroid`
  length 384, `quality_score` range), `AuditEntry.seq` (`ge=1`).
  `SandboxErrorClass` consolidated — promoted to `StrEnum` in `ownevo_format`
  and imported from there in `types.py`; removes the duplicate definition.
- Evolution protocol: `ReflectionDecision` enum (`FINALIZE`/`CONTINUE`/`REPLAN`)
  introduced; `Reflector.reflect()` returns it instead of `Learning`.
- OpenAPI: `Workflow.mode`, `Proposal.eval_score`/`eval_rationale`,
  `LiftPoint` deployment fields, and `Approval` schema — all present in SQL
  and Pydantic but missing from the spec.
- State machine tests added (`test_proposal_states.py`) covering all 11
  legal transitions, terminal-state guards, audit-kind coupling, and the
  autonomous-mode path. Boundary/constraint tests added across both packages.
  64 tests pass.
