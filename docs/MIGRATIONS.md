# Migrations — `apps/kernel/migrations/`

**Authority:** when this doc disagrees with a migration's header comment,
the migration wins — update this doc to match.

Ordering is enforced by filename prefix. The migration runner applies each
`.sql` file in its own transaction, in lexicographic order. Most files are
idempotent (using `CREATE / ALTER ... IF NOT EXISTS` / `IF EXISTS`), but
`0009_audit_hash_chain.sql` (`ADD COLUMN`) and `0010_grants_and_constraints.sql`
(`ADD CONSTRAINT`) are **not** — re-running them on an already-migrated DB
will fail with a Postgres error. The schema_migrations table prevents
accidental re-runs under normal operation.

See [`SCHEMA.md`](SCHEMA.md) for the resulting table layout. This doc
explains **why** each migration exists, in dependency order.

---

## Index

| # | File | Adds | Depends on |
|---|---|---|---|
| 0001 | `0001_substrate.sql` | All base tables, enums, the WORM audit trigger | — |
| 0002 | `0002_failure_cluster_fingerprint.sql` | `failure_clusters.fingerprint` + partial unique index | 0001 |
| 0003 | `0003_skills_latest_proposed.sql` | Split `skills.head_version_id` from "agent's last write" | 0001 |
| 0004 | `0004_skills_deployed.sql` | `skills.deployed_version_id` (live pointer, separate from HEAD) | 0003 |
| 0005 | `0005_workflow_sim_metric.sql` | `workflows.simulation_plan` + `metric_definition` jsonb | 0001 |
| 0006 | `0006_workflow_kind.sql` | `workflows.kind` (benchmark vs production) | 0001 |
| 0007 | `0007_workflow_mode_eval_modes.sql` | Two new `workflow_mode` enum values: `eval-only`, `eval-propose` | 0001 |
| 0008 | `0008_iteration_case_outputs.sql` | `iteration_case_outputs` table (per-case structured agent output) | 0001 |
| 0009 | `0009_audit_hash_chain.sql` | `audit_entries.parent_hash` + `entry_hash` (SHA-256 chain) | 0001 |
| 0010 | `0010_grants_and_constraints.sql` | WORM role-level grants + sentinel guard on `workflows.id` | 0001, 0009 |

---

## 0001 — substrate

The baseline schema. Locked 2026-05-03 by design + engineering review. Establishes:

- **Enums:** `skill_kind`, `iteration_state`, `proposal_state`, `workflow_mode`, `sandbox_error_class`, `approver_type`, audit `kind`.
- **Tables:** `workflows`, `skills`, `skill_versions`, `eval_cases`, `traces`, `iterations`, `failure_clusters`, `proposals`, `approvals`, `audit_entries`, `meta_evals`, `learnings`.
- **Extensions:** `pgcrypto` (for `gen_random_uuid()`), `vector` (for pgvector failure-embedding columns).
- **Append-only WORM trigger** on `audit_entries` (layer 1; the role-level layer comes in 0010).

The audit log is the spine of the system: every state change in proposals / iterations / skills writes an `audit_entries` row. Customer export = `SELECT * FROM audit_entries ORDER BY seq`.

Phase-2 retrofit checklist for multi-tenant (live in the header comment): add `workspace_id` columns + RLS policies to every domain table.

## 0002 — failure cluster fingerprint

Adds `fingerprint TEXT` to `failure_clusters` plus a **partial unique index** scoped to `WHERE fingerprint IS NOT NULL`. The partial scope lets pre-existing rows (NULL fingerprints) coexist without collision.

**Why:** the clustering pipeline (`cluster_m5_failures.py`) is idempotent — re-running it on the same trace set must not duplicate clusters. The fingerprint is the content hash of the cluster's defining members; `INSERT ON CONFLICT (fingerprint) DO NOTHING` makes re-runs safe.

## 0003 — split HEAD from "agent's last write"

Previously `skills.head_version_id` advanced on every `register_skill` call, including writes from rejected proposals. Anyone reading "the current best skill" via that pointer after a `FAIL_NO_IMPROVEMENT` cycle got the *rejected* version back.

New invariants:
- **`head_version_id`** — last gate-passed version (or v1 bootstrap if no gate-pass yet). Advanced **only** by `gate/persistence.py`.
- **`latest_proposed_version_id`** (this migration adds this column) — most recent `register_skill` write regardless of gate outcome. Used by the proposer for `parent_version_id` chaining so version lineage stays linear.

Backfill: every existing skill's `latest_proposed` is set to the current `head_version_id` (their previous semantics were the same row).

## 0004 — separate deployed from HEAD

After 0003, `head_version_id` = "the loop's best known good"; that's still not the same as "the version the customer is actually running in production." This migration adds `deployed_version_id` so the approval state machine has its own pointer:

| Pointer | Advanced by | Meaning |
|---|---|---|
| `head_version_id` | `gate/persistence.py` | Last gate-passed version — safe to consider. |
| `latest_proposed_version_id` | `register_skill` | Most recent agent write, regardless of gate outcome. |
| `deployed_version_id` | `approvals.deploy.deploy_proposal` / `rollback_proposal` | The version currently live in production. NULL = nothing deployed yet. |

`ON DELETE SET NULL` mirrors 0003's pattern so the `--reset` script (which deletes from `skill_versions`) doesn't raise FK violations on skills that previously had a deployed version.

## 0005 — workflow simulation_plan + metric_definition

Adds two `jsonb` columns to `workflows` so the iteration runner doesn't have to regenerate them on every run.

- `simulation_plan` — the typed `SimulationPlan` (one of the four NL-gen artifacts).
- `metric_definition` — the typed `MetricDefinition`.

Both are **nullable.** Legacy benchmark workflows (`m5-demand-prediction`, `tau3-retail-v1`) don't have these columns populated — they're code-driven by their own benchmark runners. The iteration runner branches on "is this an NL-gen workflow?" by checking `simulation_plan IS NOT NULL`.

## 0006 — workflow kind (benchmark vs production)

Adds `workflows.kind TEXT` so the web UI can partition the sidebar into separate sections. NULL = production (default).

**Why now and not later:** until multi-tenant lands, benchmark and customer workflows share one workspace. `kind` is the cleanest way to keep them visually separate without prematurely retrofitting `workspace_id`.

Backfill: pre-existing benchmark workflows (`m5-*`, `tau-*`, `tau2-*`, `tau3-*`, `taubench-*`) are pattern-matched and set to `kind='benchmark'`. Future benchmarks should set `kind='benchmark'` explicitly at insert time.

## 0007 — eval-only / eval-propose modes

`workflow_mode` was a 2-value enum (`gated`, `autonomous`). The Connect-existing-agent flow needs two more:

| Mode | Behavior |
|---|---|
| `eval-only` | Score the agent on the eval suite; never propose changes. For customers using ownEvo as a regression harness. |
| `eval-propose` | Score AND propose changes; never auto-deploy. Customer applies fixes manually. |
| `gated` (existing) | Full loop, propose + auto-gate, human approval before deploy. |
| `autonomous` (existing) | Full loop; gate-pass auto-deploys. |

**Postgres quirk:** `ALTER TYPE ... ADD VALUE` must run *outside* a transaction on Postgres < 16. The migration runner detects `ADD VALUE` in the SQL and runs those files without a transaction wrapper (the schema_migrations record is then inserted in a short separate transaction). Per Postgres docs the new values are visible immediately in subsequent statements within the same connection.

## 0008 — per-case structured agent output

Adds the `iteration_case_outputs` table. One row per `(iteration, eval_case)` carrying the agent's full structured output (`output_json jsonb`) plus the `passed: bool` the gate already used.

**Why:** the gate scores on a single pass/fail bool stored in trace events; that's enough for the lift chart but not enough for the operator-shell mocks (TableView / AlertList primitives). Those primitives need richer per-case fields — recommended action, confidence, rationale, alerts.

`ON DELETE CASCADE` for both parents so deleting a workflow / iteration / eval case cleans up automatically. `UNIQUE (iteration_id, eval_case_id)` enforces one-row-per-pair.

## 0009 — audit hash chain

Adds `parent_hash text` + `entry_hash text` (SHA-256 hex, 64 chars) to `audit_entries`.

- **No backfill.** Existing rows keep NULL hashes; they are the **"pre-hash epoch"** and the verify-chain logic skips them.
- The chain begins from the first entry written *after* this migration.
- `POST /api/audit/verify` returns `hash_chain_entries = 0` and `hash_chain_valid = true` on a freshly-migrated DB (an empty chain is valid by definition).

Index `audit_entries_entry_hash_idx` covers `entry_hash IS NOT NULL` for chain-verification traversal. See [`AUDIT_HARDENING.md`](AUDIT_HARDENING.md) for verification semantics.

## 0010 — WORM grants + sentinel guard

Two hardening items:

### 1. Role-level WORM grants (layer 2 of the append-only guarantee)

The trigger-based WORM in 0001 is layer 1. Layer 2 is the Postgres-grants level — even a superuser shell cannot silently `UPDATE` / `DELETE` audit rows without first regranting privileges.

**Manual step:** the migration ships the REVOKE statement *commented out* because the actual role name depends on the deploy environment.

```sql
-- 1. Replace <app_role> with the role used by the kernel.
--    Find it on Fly.io managed Postgres with:
--      fly pg connect -a ownevo-pg -c "\du"
REVOKE UPDATE, DELETE ON audit_entries FROM <app_role>;
```

The migration tool runs the file as-is; operators must edit-and-rerun (or hand-execute the `REVOKE` after the rest of the migration applies). See [`DEPLOYMENT.md`](DEPLOYMENT.md) for the post-deploy checklist.

### 2. Sentinel guard

`GET /api/skills?workflow_id=_unscoped` uses `'_unscoped'` as a magic value meaning "skills with no workflow." This constraint prevents a real workflow from colliding with the sentinel.

```sql
ALTER TABLE workflows
    ADD CONSTRAINT workflows_id_not_sentinel
    CHECK (id <> '_unscoped');
```

---

## Operating notes

### How to run

The kernel reads `OWNEVO_DATABASE_URL` (see [`ENV_VARS.md`](ENV_VARS.md)). On a fresh DB:

```bash
make db-migrate
```

This applies every `.sql` file in `apps/kernel/migrations/` in order. The runner is idempotent — re-running on a fully-migrated DB is a no-op.

### Rollback strategy

There are no down-migrations. The schema is forward-only by design — the append-only audit log would be meaningless if we ever dropped a column it referenced. To roll back a migration in development:

- For DDL-only migrations (everything except 0001), the simplest path is to restore from a pre-migration backup.
- For 0001, drop and re-create the database (`make db-reset`); this is destructive and should never be run in production.

For production: take a base backup before applying any migration, and resolve issues by **rolling forward** (writing a new migration that compensates) rather than backwards.

### Adding a new migration

1. Number it `00NN_<short_kebab_description>.sql` — pick `NN` = highest existing + 1.
2. Header comment: state the **why** in plain English, reference any PR or design doc, and list any **backfill** explicitly.
3. Use `CREATE / ALTER ... IF NOT EXISTS` / `IF EXISTS` so the file is idempotent.
4. If your migration uses `ALTER TYPE ADD VALUE`, the runner detects it and runs the file *without* a transaction wrapper (see 0007 and `scripts/migrate.py`). No extra action needed — just don't mix `ADD VALUE` with other DDL in the same file.
5. Update this doc — add an Index-table row + a `## NNNN — short title` section.
6. Update [`SCHEMA.md`](SCHEMA.md) if you've added or changed a column the schema reference covers.
