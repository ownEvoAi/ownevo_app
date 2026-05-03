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
- `apps/kernel/src/ownevo_kernel/observability/` — loop-stuck Slack
  alerter + learnings writer (W2.4a). `write_learning(conn, kind=,
  content=, iteration_id=)` appends one row to the `learnings` table
  (the agent's append-only memory mirroring auto-harness's
  `learnings.md`); kind is one of `hypothesis` / `observation` /
  `request-to-human` / `failure-note` per the SQL CHECK constraint.
  `latest_learning(conn)` returns the most recent row (sorted by
  `created_at DESC, id DESC` for determinism) or None.
  `LoopStuckAlerter` reads the latest learning, compares to `now`,
  and fires a Slack webhook if the gap exceeds
  `idle_threshold_seconds` (default 2h per the design review's spec).
  Returns a structured `StuckSignal` (is_stuck, last_learning_at,
  seconds_since_last, threshold_seconds, summary, webhook_fired) so
  the caller has the evidence even when no webhook fires. Empty
  `learnings` table → not stuck (the alerter catches stalls, not
  not-yet-started workflows). `webhook_url=None` puts the alerter
  in observe-only mode for dev / dry-run. `now=` is injectable so
  integration tests fast-forward without sleeping. Stdlib HTTP via
  `asyncio.to_thread(urllib.request.urlopen)` — no `httpx` /
  `aiohttp` dep added. `http_post` is injectable for test mocks.
- `apps/kernel/src/ownevo_kernel/gate/` — 3-step regression gate
  (W2.2). `run_gate(runner, *, prior_eval_task_ids=, best_ever_score=,
  regression_tolerance=, improvement_epsilon=)` is a pure async
  function over the `BenchmarkRunner` Protocol; returns a structured
  `GateResult` with `decision` (PASS / FAIL_REGRESSION /
  FAIL_NO_IMPROVEMENT / SANDBOX_ERROR), `val_score`,
  `failed_prior_task_ids`, and `promotable_task_ids`. Steps: (1)
  every task in `prior_eval_task_ids` must score at or above
  `1.0 - regression_tolerance`; empty prior suite → step skipped per
  the Day-1 bootstrap rule. (2) val_score must exceed
  `best_ever_score + improvement_epsilon`; `best_ever_score=None` →
  step skipped (first run becomes the baseline). (3) tasks that
  passed at threshold and were not in the prior suite are returned
  as `promotable_task_ids` for the caller to wire into
  `add_eval_case`. D3 sandbox-error short-circuit: any None reward
  in the runner result emits SANDBOX_ERROR without trusting
  val_score and without advancing best-ever. `GateDecision` values
  are wire-compatible with `IterationState` so the wrapper that
  writes iterations + proposals + audit entries (lands alongside the
  M5 baseline pipeline) can use `decision.value` directly. The gate
  executes `runner.run(None)` exactly once and derives all three
  steps from that result.
- `apps/kernel/tests/gate_self_test/` — gate self-test harness
  (W2.2a). Five synthetic scenarios pin the gate-trust contract:
  known-good change admitted; known-bad regression blocked; no-net-
  improvement blocked; adversarial higher-aggregate-but-regresses-
  prior change blocked (the failure mode val_score-alone would
  silently admit); crashing skill blocked. Runs in-process via
  `SyntheticBenchmarkRunner` — no Docker, no DB, no LLM — so the
  failure mode being detected is purely "the gate logic is broken,"
  not substrate flakiness. Picks up automatically under `pytest`;
  failing the harness fails the build.
- `apps/kernel/src/ownevo_kernel/agent_tools/` — 5 kernel-side tool
  functions exposed to the coding agent (W2.1):
  `read_skill(conn, skill_id)` and `write_skill(conn, skill_id, content,
  created_by=)` wrap the skill registry; `run_pipeline(sandbox,
  skill_content=, input_data=, timeout_seconds=, memory_mb=,
  task_timeout_seconds=)` runs a skill in the sandbox with a
  per-task timeout layer above the sandbox per-call timeout, an
  `input_data` Python global injected via prologue (no file I/O — the
  bind-mount is RO), and JSON-on-stdout output parsing into
  `PipelineResult.outputs`; `read_metrics(conn, trace_id)` and
  `analyze_failures(conn, workflow_id=, k=10)` are the agent's read
  surface over `traces`. Both read tools enforce **train/test
  discipline**: by default, neither surfaces traces stamped
  `metric_outputs.fold == "test"` (raises `TestFoldAccessRefused` /
  filters them out). `include_test_fold=True` is reserved for the gate
  runner. The convention is the enforcement boundary until the
  iteration↔eval_case schema linkage lands in W4. Claude Agent SDK
  middleware adapter — exposing these as agent tool definitions and
  emitting AgentEvents into a TraceCollector — is a separate slice; the
  kernel-side functions are usable directly from the gate runner (W2.2)
  and tests without taking the SDK as a dep.
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
- `apps/kernel/src/ownevo_kernel/datasets/m5_metric.py` — M5 scorers
  (W2.6 prerequisite). Pure numpy. `rmse(predictions, actuals)` for the
  headline baseline number; `wrmsse(predictions, actuals, weights=,
  scales=)` per the M5 paper (per-series RMSSE / first-difference scale,
  weighted by sales-dollar share); `compute_wrmsse_weights_and_scales`
  derives both from training data; `make_held_out_fold(catalog,
  val_days=28, test_days=28)` carves the train / val / test day-column
  split per Phase 0's lock. Refuses zero-scale series so silent +inf
  results can't slip past. numpy>=1.26,<3 added as a kernel dep.
- `apps/kernel/src/ownevo_kernel/benchmark/` — `BenchmarkRunner` Protocol
  + `BenchmarkResult` dataclass + `SyntheticBenchmarkRunner` (PR #5 from
  the W2 plan; substrate for the gate self-test in W2.2a).
  `BenchmarkResult.val_score` is the mean reward with `None` (timeout /
  no-result) counting as 0.0 in the denominator so an agent can't game
  the aggregate by causing dropouts. `n_passed` / `n_no_result` /
  `n_tasks` accessors round out what the gate's regression-suite step
  consumes. `SyntheticBenchmarkRunner` runs in-process — no Docker, no
  DB, no LLM — so the gate self-test isolates gate logic from sandbox /
  runtime behavior. Skill exceptions score as 0.0 (definite failure,
  not missing measurement). Real M5 + Tau3 runners (W2.6 / W7-8) will
  implement the same Protocol with workflow-specific scoring inside.
- `apps/kernel/tests/test_skill_format.py` — add coverage for malformed
  YAML (`"not valid YAML"`), non-dict YAML (`"must be a YAML mapping"`),
  and the `m` (minutes) unit in `parse_stale_duration`.
- `apps/kernel/tests/test_trace_collector.py` — add `make_event`
  validation tests (unknown `type`, missing required field) and an
  empty-session test that verifies `events == []` is persisted.

### Changed
- `apps/kernel/migrations/0001_substrate.sql` — `proposals` table gains
  `eval_score numeric(3,2)` (with `[0,1]` check) and `eval_rationale text`
  to align with the Pydantic `Proposal` model. Pre-stages the LLM-judge
  wiring that lands in W2; closes the schema-vs-types divergence flagged
  in `/review`. Migration not yet applied to any deployed DB so this is a
  forward-only edit, not a `0002_*.sql` follow-up.
- `apps/kernel/src/ownevo_kernel/types.py` — `FailureCluster` gains
  `centroid: list[float] | None = Field(default=None, min_length=384, max_length=384)`
  mirroring the SQL `centroid vector(384)` column. Without this, `extra="forbid"`
  would reject any `SELECT *` from `failure_clusters`. Length constraint enforces
  the all-MiniLM-L6-v2 dimension at the Pydantic layer.
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

### Fixed
- `apps/kernel/src/ownevo_kernel/agent_tools/run_pipeline.py` — `run_pipeline`
  now catches `TypeError`/`ValueError` from `json.dumps(input_data)` and returns
  a structured `PipelineResult(status="error")` instead of propagating a raw
  exception when `input_data` contains non-JSON-serializable values (datetime,
  UUID, custom objects).
- `apps/kernel/src/ownevo_kernel/agent_tools/skills.py` — `write_skill` now
  validates that the `skill_id` argument matches the frontmatter `id` in
  `content`, raising `SkillFormatError` before any DB write on mismatch.
  Previously the arg was advisory-only and a mismatch silently wrote to the
  wrong skill.
- `apps/kernel/src/ownevo_kernel/sandbox/local_docker.py` — Docker container
  leaked when the outer `asyncio.wait_for` in `run_pipeline` cancelled
  `sandbox.run()`: `CancelledError` bypassed the `except TimeoutError` handler
  so `_kill_container` was never called and the container kept running until its
  own timeout expired. Added `except asyncio.CancelledError` to kill and remove
  the container before re-raising.
- `apps/kernel/src/ownevo_kernel/agent_tools/metrics.py` — `analyze_failures`
  secondary sort key was ascending by `started_at` (oldest-first for equal error
  counts); corrected to descending so the agent surfaces the most recent failures
  first. `read_metrics` now returns `None` for non-dict JSONB `metric_outputs`
  (closes a return-type contract violation and a subtle test-fold bypass for
  corrupt rows).
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

### Security
- `apps/kernel/src/ownevo_kernel/sandbox/local_docker.py` — close TODO-17
  user-exception spoof. Previously the runner script ran user code via
  `runpy.run_path` in its own process, so user code calling
  `os._exit(100)` short-circuited the runner's `try/except` and exited the
  container with the runner's user-exception sentinel — classifier returned
  `error_class=None` (the gate's "logical failure the agent owns" path).
  Runner now executes user code as a subprocess (`subprocess.run([sys.executable,
  '/sandbox/user_code.py'])`) and maps the child's returncode according to a
  fixed policy: 0 → 0; 1 → 100 (Python's default for uncaught exceptions);
  100 → 102 (the new `_RUNNER_CRASH_REMAP_EXIT_CODE`, classifier returns
  `Crash`); negative-N (signal) → 128+|N|; otherwise passthrough. Closes the
  same-process attack surface — user code can no longer manipulate the runner
  process's state, FDs, or memory. The `os._exit(0)` case remains observably
  indistinguishable from clean exit at the process boundary; defense-in-depth
  lives at the metric layer (`run_pipeline`'s JSON-output requirement →
  missing/invalid → `outputs=None` → gate refuses to advance best-ever). Pinned
  by 3 new tests in `test_sandbox.py`: `os._exit(100)` now classifies as
  `Crash`, arbitrary `os._exit(N)` classifies as `Crash`, `os._exit(0)`
  remains `ok` (documented limit, pinned to catch silent regressions).
