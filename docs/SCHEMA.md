# Database Schema

**Source of truth:** [`apps/kernel/migrations/0001_substrate.sql`](../apps/kernel/migrations/0001_substrate.sql).
This doc explains the shape; the SQL is authoritative. Changes go through
migrations (`0002_*.sql`, etc.); this doc is updated with every
schema-affecting commit.

## Design decisions reflected in the schema

- **Append-only audit (WORM).** `audit_entries` blocks UPDATE/DELETE via a row trigger and via app-role grants. Crypto-grade tamper-evidence (Merkle root + signed export on top of the SHA-256 chain) is Phase 2.
- **Sandbox-error class.** `iterations.sandbox_error_class` captures Timeout / OOM / Crash distinctly. The gate consumer does **not** advance best-ever when an iteration ends in a sandbox error.
- **Single-tenant for MVP.** There are no `workspace_id` columns. The Phase-2 retrofit is a single-pass `ALTER TABLE ... ADD COLUMN workspace_id` across every domain table + RLS policies.
- **NL-gen meta-eval storage.** The `meta_evals` table stores judge-vs-human meta-eval results from the NL-gen quality gate.

## ER diagram (substrate, MVP)

```
                                    ┌──────────────────────┐
                                    │      workflows       │
                                    │  id (PK, text)       │
                                    │  description         │
                                    │  spec (jsonb)        │◄────┐
                                    │  metric_id           │     │
                                    │  sim_skill_id (FK) ─ │ ─┐  │
                                    │  meta_eval_score     │  │  │
                                    │  mode (enum)         │  │  │
                                    └─────────┬────────────┘  │  │
                                              │ 1:N           │  │ N:1
                                              ▼               ▼  │
            ┌────────────────────┐      ┌──────────────────────┐ │
            │      skills        │◄─────│      iterations      │ │
            │  id (PK, text)     │      │  id (PK, uuid)       │ │
            │  kind (enum)       │      │  workflow_id (FK) ───┼─┘
            │  head_version_id   │      │  iteration_index     │
            │  workflow_id (FK)  │      │  parent_skill_v_id   │
            │  capability_tags   │      │  proposed_skill_v_id │
            └─────────┬──────────┘      │  state (enum)        │
                      │ 1:N             │  sandbox_error_class │
                      ▼                 │  val_score           │
            ┌────────────────────┐      │  best_ever_*         │
            │   skill_versions   │◄─────│  cluster_id (FK)     │
            │  id (PK, uuid)     │      │  deployment_id (FK) ─┼──► skill_deployments
            │                    │      └─────────┬────────────┘
            │  skill_id (FK)     │                │ 1:N
            │  parent_version_id │                ▼
            │  version_seq       │      ┌──────────────────────┐
            │  content (text)    │      │      proposals       │
            │  retention_block   │      │  id (PK, uuid)       │
            │    (jsonb)         │      │  iteration_id (FK)   │
            │  diff_summary      │      │  skill_id (FK)       │
            │  created_by        │◄─────│  parent_version_id   │
            └────────────────────┘      │  proposed_content    │
                      ▲                 │  plain_lang_summary  │
                      │ FK              │  expected_impact     │
                      │                 │  state (enum)        │
            ┌─────────┴──────────┐      └─────────┬────────────┘
            │      traces        │                │ 1:N
            │  id (PK, uuid)     │                ▼
            │  workflow_id (FK)  │      ┌──────────────────────┐
            │  iteration_id (FK) │      │      approvals       │
            │  skill_version_id  │      │  proposal_id (FK)    │
            │  events (jsonb[])  │      │  decided_by          │
            │  metric_outputs    │      │  approver_type (enum)│
            │  token_usage       │      │  decision            │
            └────────────────────┘      │  comment             │
                                        │  became_eval_case_id │ ──┐
                                        └──────────────────────┘   │
                                                                   │ FK (loop)
            ┌────────────────────┐      ┌──────────────────────┐   │
            │  failure_clusters  │◄─────│      eval_cases      │◄──┘
            │  id (PK, uuid)     │      │  id (PK, uuid)       │
            │  workflow_id (FK)  │      │  workflow_id (FK)    │
            │  label             │      │  provenance (enum)   │
            │  label_eval_score  │      │  cluster_id (FK)     │
            │  centroid (vec384) │      │  input (jsonb)       │
            │  cluster_size      │      │  expected_behavior   │
            │  quality_score     │      │  regression_tolerance│
            └────────────────────┘      │  is_test_fold        │
                                        └──────────────────────┘

            ┌────────────────────┐      ┌──────────────────────┐
            │   audit_entries    │      │     meta_evals       │
            │  (append-only WORM)│      │  workflow_id (FK)    │
            │  seq (bigserial)   │      │  description         │
            │  kind (enum)       │      │  coverage_score      │
            │  payload (jsonb)   │      │  per_dimension       │
            │  related_id        │      │  judge_model         │
            │  actor             │      │  passed_threshold    │
            └────────────────────┘      └──────────────────────┘

            ┌────────────────────┐
            │     learnings      │
            │  iteration_id (FK) │
            │  kind              │
            │  content           │
            └────────────────────┘
```

## Table-by-table notes

Each section's "Introduced by" line is the authoritative provenance — see [`MIGRATIONS.md`](MIGRATIONS.md) for the rationale, dependencies, and rollback strategy of each migration.

### `workflows`
*Introduced by: [0001](MIGRATIONS.md#0001--substrate). Extended by: [0005](MIGRATIONS.md#0005--workflow-simulation_plan--metric_definition) (simulation_plan + metric_definition), [0006](MIGRATIONS.md#0006--workflow-kind-benchmark-vs-production) (kind), [0007](MIGRATIONS.md#0007--eval-only--eval-propose-modes) (mode enum values), [0010](MIGRATIONS.md#0010--worm-grants--sentinel-guard) (sentinel-id CHECK).*

The described workflow plus the NL-gen-generated artifacts. `spec` is the frozen-schema JSONB containing tools, ui block, environment description. `meta_eval_score` is the description-coverage score from the NL-gen meta-eval.

`mode` is `'gated' | 'autonomous' | 'eval-only' | 'eval-propose'` (default `'gated'` — the two new values come from migration 0007). In `autonomous` mode the regression gate's `gate-passed` state transitions directly to `approved-awaiting-deploy` without a human or LLM-judge step — used for benchmarking (τ³-bench conditions A/B/C) and any future fully-automated deployment pipeline. Mode is set per-workflow at creation and cannot be changed mid-run in MVP.

### `skills` + `skill_versions`
*Introduced by: [0001](MIGRATIONS.md#0001--substrate). Extended by: [0003](MIGRATIONS.md#0003--split-head-from-agents-last-write) (latest_proposed_version_id), [0004](MIGRATIONS.md#0004--separate-deployed-from-head) (deployed_version_id).*

Mirror the auto-harness "single mutable artifact" pattern. Three pointers, advanced by different code paths:

| Pointer | Meaning | Advanced by |
|---|---|---|
| `head_version_id` | Last gate-passed version. | `gate/persistence.py` |
| `latest_proposed_version_id` | Most recent `register_skill` write, regardless of gate outcome. | `register_skill` |
| `deployed_version_id` | Currently-live in production. NULL = nothing deployed yet. | `approvals.deploy.deploy_proposal` / `rollback_proposal` |

Every revision is an immutable row in `skill_versions`. `retention_block` is the parsed YAML frontmatter (see [`SKILL_FORMAT.md`](./SKILL_FORMAT.md)). `version_seq` is monotonic per-skill (1, 2, 3...). The unique constraint enforces this.

### `skill_deployments`
*Introduced by: [0001](MIGRATIONS.md#0001--substrate).*

Named deployment configs for a skill — same content, different runtime. Each row is a `(skill, config_tag)` pair specifying the model, temperature, tools, and other call-time parameters. Multiple deployments can be active simultaneously on the same skill, enabling A/B testing and per-model comparison without branching skill content.

`traffic_weight` (0.00–1.00) controls what fraction of live calls are routed to this deployment; the runtime is responsible for ensuring weights across active deployments sum to 1.0. `run_config` is open-ended JSONB — expected keys: `temperature`, `tools`, `system_prompt_override`, `timeout_ms`.

`iterations.deployment_id` ties each gate run to the config it ran under, so the `lift_series` view can plot variant lines on the same eval set. Which deployment drives the loop is a runtime concern, not a schema constraint.

### `iterations`
*Introduced by: [0001](MIGRATIONS.md#0001--substrate). Extended by: [0008](MIGRATIONS.md#0008--per-case-structured-agent-output) (iteration_case_outputs child table).*

One row per loop iteration. `parent_skill_version_id` is what the agent started with; `proposed_skill_version_id` is what it ended with (only written if gate passes; null if rejected). `sandbox_error_class` is non-null iff `state = 'sandbox-error'`. `deployment_id` is the deployment config the iteration ran under; null for iterations without a deployment config.

`best_ever_score_before` and `best_ever_score_after` are the gate's "best ever val_score" snapshots. The convention: `best_ever_score_after = max(best_ever_score_before, val_score)` if gate passed, else equals `best_ever_score_before`.

The child table `iteration_case_outputs` (one row per `(iteration, eval_case)`) carries the agent's per-case structured output — used by TableView / AlertList primitives in the operator shell.

### `proposals` + `approvals`
*Introduced by: [0001](MIGRATIONS.md#0001--substrate).*

The approval queue. State machine documented in [`STATE_MACHINES.md`](./STATE_MACHINES.md). One proposal can have at most one resolved approval.

`proposals.eval_score` (numeric(3,2), `[0,1]` check) and `proposals.eval_rationale` (text) hold the LLM-judge-stub output. Populated when the judge wires up to the proposal flow.

`approvals.approver_type` is `'human' | 'llm-judge' | 'autonomous'`. In autonomous mode the gate runner writes the approval row directly (no human in the loop); `decided_by` is `"autonomous"` and `comment` is null. `approvals.became_eval_case_id` closes the comment-becomes-eval-case flow: when a human reviewer rejects with a comment, the comment is structured into an `eval_cases` row tagged `provenance = 'rejected-feedback'`. Not applicable in autonomous mode. See [`HARNESS.md`](HARNESS.md#rejection-feedback-loop) for the full path.

### `eval_cases`
*Introduced by: [0001](MIGRATIONS.md#0001--substrate).*

Per-workflow, with provenance tracking. `is_test_fold` enforces train/test discipline — gate runner refuses to use test-fold rows as training input. See [`TRAIN_TEST_DISCIPLINE.md`](TRAIN_TEST_DISCIPLINE.md) for the full invariant.

### `failure_clusters`
*Introduced by: [0001](MIGRATIONS.md#0001--substrate). Extended by: [0002](MIGRATIONS.md#0002--failure-cluster-fingerprint) (fingerprint + dedup index).*

HDBSCAN output. `centroid vector(384)` matches `sentence-transformers/all-MiniLM-L6-v2`'s output dim. `quality_score` is HDBSCAN cluster persistence; below threshold the UI shows "more iterations needed" rather than an unhelpful card. `label_eval_score` is the cluster-label-vs-human agreement score. `fingerprint` (with partial unique index) makes re-runs of `cluster_m5_failures.py` idempotent.

### `audit_entries` (append-only WORM)
*Introduced by: [0001](MIGRATIONS.md#0001--substrate). Extended by: [0009](MIGRATIONS.md#0009--audit-hash-chain) (parent_hash + entry_hash), [0010](MIGRATIONS.md#0010--worm-grants--sentinel-guard) (role-level grants).*

Append-only spine. Every state change in proposals/iterations/skills/clusters/etc. writes a row here. `seq` is the canonical export ordering. WORM-enforced **three ways** (see [`AUDIT_HARDENING.md`](AUDIT_HARDENING.md) for the full threat model):

1. Row trigger raises on UPDATE/DELETE (works against any role; migration 0001).
2. App-role grants only allow INSERT and SELECT (migration 0010 — operator runs the REVOKE manually).
3. SHA-256 hash chain (`parent_hash` + `entry_hash`) makes post-hoc tampering detectable via `POST /api/audit/verify` (migration 0009).

### `meta_evals`
Stores judge-vs-human meta-eval results from the NL-gen quality gate. `coverage_score` is the headline number; `per_dimension` breaks it down (sim_completeness / eval_coverage / metric_alignment). Surfaced in the workspace UI as the "sim covers 11/12 of your description" badge.

### `learnings`
Mirrors the auto-harness `learnings.md` append-only file. Every iteration writes hypotheses, observations, and requests-to-human here. The loop-stuck alert fires when no new entry appears in 2h.

### `traces`
The high-volume table. JSONB `events` is an array of typed `AgentEvent` (defined in `packages/trace-format/`). Phase 2 will migrate to ClickHouse if volume justifies; for MVP, monthly partitioning on `started_at` is the migration path if needed.

## Indexes

Indexes target the hot queries:

- `pending_proposals` view (approval queue UI): `proposals(created_at) WHERE state IN ('pending', 'gate-passed')` — partial index keeps it tiny.
- `lift_series` view (lift chart): `iterations(workflow_id, iteration_index)` unique constraint covers this.
- Failure cluster vector search: `ivfflat (centroid vector_cosine_ops)`.
- Audit log export: `audit_entries(seq)` is the canonical order.

## Phase-2 retrofit (multi-tenant)

When a second tenant onboards, run a migration that:

1. Adds `workspace_id text NOT NULL` to every domain table (`skills`, `skill_versions`, `eval_cases`, `traces`, `failure_clusters`, `iterations`, `proposals`, `approvals`, `audit_entries`, `meta_evals`, `learnings`, `workflows`).
2. Backfills `workspace_id = 'mvp-default'` on existing rows.
3. Enables RLS: `ALTER TABLE <name> ENABLE ROW LEVEL SECURITY` + `CREATE POLICY ... USING (workspace_id = current_setting('app.workspace_id'))`.
4. Wraps every kernel session start with `SET LOCAL app.workspace_id = ...`.
5. Drops the old "single-tenant" assumption from API endpoints.

Estimated 1-2 weeks of work whenever a second deployment requires it.
