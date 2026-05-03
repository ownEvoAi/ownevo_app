# Database Schema

**Source of truth:** [`apps/kernel/migrations/0001_substrate.sql`](../apps/kernel/migrations/0001_substrate.sql).
This doc explains the shape; the SQL is authoritative.

Locked 2026-05-03 by CEO review v4.3 + eng review. Changes go through migrations
(`0002_*.sql`, etc.); this doc gets updated with every schema-affecting commit.

## Decisions reflected

- **D2** — `audit_entries` is append-only (WORM). UPDATE/DELETE blocked by row trigger AND app-role grants. Crypto-grade tamper-evidence is Phase 2.
- **D3** — `iterations.sandbox_error_class` enum captures Timeout/OOM/Crash distinctly. Gate consumer does NOT advance best-ever when iteration ends in a sandbox error.
- **D4** — Single-tenant for MVP. NO `workspace_id` columns. The Phase-2 retrofit is a single-pass `ALTER TABLE ... ADD COLUMN workspace_id` across every domain table + RLS policies.
- **D7** — `meta_evals` table stores NL-gen meta-eval results.

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
            │  id (PK, uuid)     │      └─────────┬────────────┘
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
            │  (WORM — D2)       │      │  workflow_id (FK)    │
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

### `workflows`
The user's described workflow + the NL-gen-generated artifacts. `spec` is the frozen-schema JSONB containing tools, ui block, environment description. `meta_eval_score` is the description-coverage from D7.

`mode` is `'gated' | 'autonomous'` (default `'gated'`). In `autonomous` mode the regression gate's `gate-passed` state transitions directly to `approved-awaiting-deploy` without a human or LLM-judge step — used for benchmarking (τ³-bench conditions A/B/C) and any future fully-automated deployment pipeline. Mode is set per-workflow at creation and cannot be changed mid-run in MVP.

### `skills` + `skill_versions`
Mirror the auto-harness "single mutable artifact" pattern. `head_version_id` points to the current HEAD (denormalized for hot reads). Every revision is an immutable row in `skill_versions`. `retention_block` is the parsed YAML frontmatter (see [`SKILL_FORMAT.md`](./SKILL_FORMAT.md)).

`version_seq` is monotonic per-skill (1, 2, 3...). The unique constraint enforces this.

### `iterations`
One row per loop iteration. `parent_skill_version_id` is what the agent started with; `proposed_skill_version_id` is what it ended with (only written if gate passes; null if rejected). `sandbox_error_class` is non-null iff `state = 'sandbox-error'` (D3).

`best_ever_score_before` and `best_ever_score_after` are the gate's "best ever val_score" snapshots. The convention: `best_ever_score_after = max(best_ever_score_before, val_score)` if gate passed, else equals `best_ever_score_before`.

### `proposals` + `approvals`
The approval queue. State machine documented in [`STATE_MACHINES.md`](./STATE_MACHINES.md). One proposal can have at most one resolved approval.

`proposals.eval_score` (numeric(3,2), `[0,1]` check) and `proposals.eval_rationale` (text) hold the LLM-judge-stub output. Shape carried over from `core/agentos_harness/types.py:Proposal` (MIT); populated starting W2 when the judge wires up.

`approvals.approver_type` is `'human' | 'llm-judge' | 'autonomous'`. In autonomous mode the gate runner writes the approval row directly (no human in the loop); `decided_by` is `"autonomous"` and `comment` is null. `approvals.became_eval_case_id` closes the comment-becomes-eval-case flow: when a human reviewer rejects with a comment, the comment is structured into an `eval_cases` row tagged `provenance = 'rejected-feedback'`. Not applicable in autonomous mode.

### `eval_cases`
Per-workflow, with provenance tracking. `is_test_fold` enforces train/test discipline — gate runner refuses to use test-fold rows as training input.

### `failure_clusters`
HDBSCAN output. `centroid vector(384)` matches `sentence-transformers/all-MiniLM-L6-v2`'s output dim. `quality_score` is HDBSCAN cluster persistence; below threshold the UI shows "more iterations needed" rather than an unhelpful card. `label_eval_score` is the D4 cluster-label-vs-human agreement.

### `audit_entries` (WORM — D2)
Append-only spine. Every state change in proposals/iterations/skills/clusters/etc. writes a row here. `seq` is the canonical export ordering. WORM-enforced two ways:
1. Row trigger raises on UPDATE/DELETE (works against any role).
2. App-role grants (set in `0002_grants.sql`) only allow INSERT and SELECT.

Crypto-grade tamper-evidence (canonical-JSON content hash + parent hash + chain rotation procedure for migrations; Merkle + signed root + transparency log) is a **Phase-2 retrofit** when first regulated buyer requires it.

### `meta_evals` (D7)
Stores judge-vs-human meta-eval results from the NL-gen quality gate. `coverage_score` is the headline number; `per_dimension` breaks it down (sim_completeness / eval_coverage / metric_alignment). Surfaced in the workspace UI as the "sim covers 11/12 of your description" badge.

### `learnings`
Mirrors the auto-harness `learnings.md` append-only file. Every iteration writes hypotheses, observations, and requests-to-human here. The loop-stuck alert (W2.4a) fires when no new entry appears in 2h.

### `traces`
The high-volume table. JSONB `events` is an array of typed `AgentEvent` (defined in `packages/trace-format/`). Phase 2 will migrate to ClickHouse if volume justifies; for MVP, monthly partitioning on `started_at` is the migration path if needed.

## Indexes

Indexes target the hot queries:

- `pending_proposals` view (approval queue UI): `proposals(created_at) WHERE state IN ('pending', 'gate-passed')` — partial index keeps it tiny.
- `lift_series` view (lift chart): `iterations(workflow_id, iteration_index)` unique constraint covers this.
- Failure cluster vector search: `ivfflat (centroid vector_cosine_ops)`.
- Audit log export: `audit_entries(seq)` is the canonical order.

## Phase-2 retrofit (D4 — multi-tenant)

When customer #2 onboards, run a migration that:

1. Adds `workspace_id text NOT NULL` to every domain table (`skills`, `skill_versions`, `eval_cases`, `traces`, `failure_clusters`, `iterations`, `proposals`, `approvals`, `audit_entries`, `meta_evals`, `learnings`, `workflows`).
2. Backfills `workspace_id = 'mvp-default'` on existing rows.
3. Enables RLS: `ALTER TABLE <name> ENABLE ROW LEVEL SECURITY` + `CREATE POLICY ... USING (workspace_id = current_setting('app.workspace_id'))`.
4. Wraps every kernel session start with `SET LOCAL app.workspace_id = ...`.
5. Drops the old "single-tenant" assumption from API endpoints.

Estimated 1-2 weeks per the Phase-2 retrofit checklist in [`PLAN.md`](./PLAN.md).
