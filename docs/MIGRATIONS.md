# Migrations — `apps/kernel/migrations/`

**Authority:** when this doc disagrees with a migration's header comment,
the migration wins — update this doc to match.

Ordering is enforced by filename prefix. The migration runner applies each
`.sql` file in its own transaction, in lexicographic order. Most files are
idempotent (using `CREATE / ALTER ... IF NOT EXISTS` / `IF EXISTS`), but a few
are **not** and will fail with a Postgres error if re-run on an already-migrated
DB: `0009_audit_hash_chain.sql` (`ADD COLUMN`), `0010_grants_and_constraints.sql`
(`ADD CONSTRAINT`), and `0034_workspace_rls_enforcement.sql` (`DROP CONSTRAINT`
+ PK re-add on `integration_credentials`; the RLS/policy/default loop inside it
*is* idempotent, but the PK swap is not). The schema_migrations table prevents
accidental re-runs under normal operation.

Some files must run **outside** a transaction (the runner detects a marker and
switches to autocommit): `ALTER TYPE ... ADD VALUE` files (0007, 0015, 0023,
0026, 0028-audit) and the online-DDL files carrying `ownevo:no-txn`
(0018b, 0024, 0027 — `VALIDATE CONSTRAINT` / `CREATE INDEX CONCURRENTLY`).

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
| 0011 | `0011_workflow_template_id.sql` | `workflows.created_from_template` (vertical-template provenance) | 0001 |
| 0012 | `0012_design_agent_log.sql` | `workflows.design_agent_log` jsonb + two design-agent `audit_kind` values | 0001 |
| 0013 | `0013_case_output_payload.sql` | `iteration_case_outputs.output_payload` (domain-shaped agent output) | 0008 |
| 0014 | `0014_workflow_agent_model.sql` | `workflows.agent_model_id` (`provider:model` slug) + model-change `audit_kind` | 0001 |
| 0015 | `0015_changes_requested_enums.sql` | `proposal_state` + `audit_kind` values for "request changes" (ADD VALUE) | 0001 |
| 0015b | `0015b_changes_requested_constraint.sql` | Widen `approvals.decision` CHECK to allow `request-changes` | 0015 |
| 0016 | `0016_demo_phase1.sql` | Live-demo tables: `demo_usage`, `demo_invite_revocations`, `demo_budget_state` | 0001 |
| 0017 | `0017_proposal_kind.sql` | `proposal_kind` enum + `proposals.proposed_payload` (non-skill artifacts) | 0001 |
| 0018 | `0018_traces_ingest_source.sql` | `traces.ingest_source` (kernel-emitted vs OTLP-ingested), CHECK NOT VALID | 0001 |
| 0018b | `0018b_traces_ingest_source_online.sql` | VALIDATE CONSTRAINT + `CREATE INDEX CONCURRENTLY` (runs outside txn) | 0018 |
| 0019 | `0019_receiver_tokens.sql` | `receiver_tokens` (bearer-token auth for the OTLP ingest receiver) | 0018 |
| 0020 | `0020_mock_sim_tier.sql` | `workflows.sim_tier` + `mock_sim_config` (deterministic mock solver) | 0001 |
| 0021 | `0021_replay_sim_tier.sql` | Replay-tier config + `captured_sandbox_runs` table | 0020 |
| 0022 | `0022_langsmith_integration.sql` | `workflows.origin` + `skills.langsmith_prompt_id` (ship-fix-to-LangSmith) | 0001 |
| 0023 | `0023_audit_fix_shipped_langsmith.sql` | `audit_kind` value `fix-shipped-langsmith` (ADD VALUE) | 0022 |
| 0024 | `0024_audit_ship_langsmith_unique.sql` | Partial unique index preventing double-push to LangSmith (CONCURRENTLY) | 0023 |
| 0025 | `0025_design_agent_import_log.sql` | `workflows.design_agent_import_log` + import-negotiation `audit_kind` | 0012 |
| 0026 | `0026_audit_fix_exported_copilot_studio.sql` | `audit_kind` value `fix-exported-copilot-studio` (ADD VALUE) | 0022 |
| 0027 | `0027_audit_ship_copilot_studio_unique.sql` | Partial unique index preventing double-delivery of a Copilot Studio fix | 0026 |
| 0028 | `0028_agent_registry.sql` | `agent_registry` table (one improvable agent per workflow) | 0001 |
| 0028 | `0028_audit_eval_cases_pushed_copilot_studio.sql` | `audit_kind` value `eval-cases-pushed-copilot-studio` (ADD VALUE) | 0001 |
| 0029 | `0029_mcp_servers.sql` | `mcp_servers` (external MCP data sources, sealed secrets) | 0001 |
| 0030 | `0030_mcp_oauth.sql` | `mcp_oauth_clients` + `mcp_oauth_states` (authorization-code grant) | 0029 |
| 0031 | `0031_data_uploads.sql` | `data_uploads` (parsed CSV/Excel/Parquet/PDF/DOCX as agent data sources) | 0001 |
| 0032 | `0032_triggers.sql` | `trigger_kind` enum + `triggers` + `trigger_fires` (event triggers) | 0001 |
| 0033 | `0033_workspace_substrate.sql` | `workspaces` table + `workspace_id` on all 17 scoped tables (non-enforcing) | 0001 |
| 0034 | `0034_workspace_rls_enforcement.sql` | FORCE RLS + isolation policies + GUC default; PK/index/soft-delete fixes | 0033 |
| 0035 | `0035_auth_users.sql` | `users` + `user_identities` + `workspace_members` (global, non-RLS) + seeded dev user | 0033 |

---

## 0001 — substrate

The baseline schema. Locked 2026-05-03 by design + engineering review. Establishes:

- **Enums:** `skill_kind`, `iteration_state`, `proposal_state`, `workflow_mode`, `sandbox_error_class`, `approver_type`, audit `kind`.
- **Tables:** `workflows`, `skills`, `skill_versions`, `eval_cases`, `traces`, `iterations`, `failure_clusters`, `proposals`, `approvals`, `audit_entries`, `meta_evals`, `learnings`.
- **Extensions:** `pgcrypto` (for `gen_random_uuid()`), `vector` (for pgvector failure-embedding columns).
- **Append-only WORM trigger** on `audit_entries` (layer 1; the role-level layer comes in 0010).

The audit log is the spine of the system: every state change in proposals / iterations / skills writes an `audit_entries` row. Customer export = `SELECT * FROM audit_entries ORDER BY seq`.

The multi-tenant retrofit later added `workspace_id` columns + FORCE RLS policies to every scoped table — see [0033](#0033--workspace-substrate) / [0034](#0034--workspace-rls-enforcement).

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

## 0011 — workflow template provenance

Adds `workflows.created_from_template TEXT NULL` so analytics can answer "what % of customer workflows started from a vertical-template starter" (retail demand planning / credit risk recalibration / clinical trial site selection). A slug-shape CHECK mirrors the kebab-id rule; NULL = free-form description, no template.

## 0012 — design-agent discovery log

Persists the design-agent discovery interview (metric / ambiguity / trigger / surface / premise) on `workflows.design_agent_log` (jsonb, NULL when discovery was skipped), and mirrors it into the hash-chained audit trail via two new `audit_kind` values (`design-agent-negotiation` per Q/A, `design-agent-ambiguity` for the report). This is the queryable compliance evidence behind the regulated-buyer pitch.

## 0013 — domain-shaped case output payload

Adds `iteration_case_outputs.output_payload` (jsonb, nullable). Where `output_json` carries the gate's pass/fail frame, `output_payload` carries the agent's domain-native artifact (forecast curve, redline pair, recommended-action table) that the Operate-tab resolver dispatches to the workflow-declared render primitives. Freeform JSONB by design — no DB-side schema, so primitive renderers can iterate.

## 0014 — per-workflow agent model

Adds `workflows.agent_model_id text NOT NULL DEFAULT 'anthropic:claude-sonnet-4-6'`, a `provider:model` slug, plus a model-change `audit_kind`. The sovereignty thesis: the customer picks the model. The allowlist is operator-controlled via env vars and validated at the API boundary — no FK to a providers table.

## 0015 / 0015b — "request changes" proposal state

`0015` (ADD VALUE, runs outside a transaction) adds `proposal_state` value `changes-requested` and matching `audit_kind` `proposal-changes-requested`: a gate-passed proposal the domain expert redirects with plain-language steering text, which the next iteration injects into the agent + proposer prompt. Distinct from `rejected` (closes + seeds an eval case) and `approved-awaiting-deploy` (ships as-is). `0015b` widens the `approvals.decision` CHECK to allow `request-changes`, split into its own file so the DROP + re-add runs atomically inside a transaction.

## 0016 — live-demo phase 1

Token-quota accounting and budget gating for the public design-agent + NL-gen demo: `demo_usage` (per-identity daily token tally + tier), `demo_invite_revocations` (revoked signed-invite jti), and `demo_budget_state` (operator-flipped soft global budget gate). These are global rate-limiting state keyed by demo identity, not customer domain data — which is why the later multi-tenant retrofit (0033) excludes them from `workspace_id` scoping.

## 0017 — non-skill proposal kinds

Adds a `proposal_kind` enum (`skill` / `description` / `metric` / `sim` / `ui-primitive`) and `proposals.proposed_payload jsonb` so workflow description, success metric, simulation plan, and operate-view primitives become first-class edit targets flowing through the same proposal → review → gate → approve loop. A CHECK pins the shape; existing rows default to `skill`.

## 0018 / 0018b — trace ingest source

`0018` adds `traces.ingest_source` distinguishing structurally-trusted kernel-emitted events (NULL) from OTLP-ingested events (`'otlp'`), whose provenance is unattested. The column is nullable + CHECK-guarded (added `NOT VALID` to avoid an `ACCESS EXCLUSIVE` table scan on the high-volume traces table). `0018b` then runs `VALIDATE CONSTRAINT` + `CREATE INDEX CONCURRENTLY` **outside a transaction** (`ownevo:no-txn`) so neither blocks concurrent reads/writes.

## 0019 — OTLP receiver bearer tokens

Adds `receiver_tokens` so an external collector POSTing to `/api/otel/v1/traces` must present an operator-minted token. Tokens are `ownevo_rt_<base64url>`; only `SHA-256(suffix)` is stored, so a DB dump is not a credential compromise. `workflow_id` is nullable — a bound token pins ingested traces to one workflow; a workflow-agnostic token requires the request to carry the workflow id.

## 0020 — mock simulation tier

Adds `workflows.sim_tier` (`real` default / `mock` / `replay`) and `mock_sim_config jsonb`. The `mock` tier runs a deterministic `MockAgentSolver` scripted by `accuracy_per_iteration[]` — zero LLM spend, sub-second iterations — for fast inner-loop dev, CI integration tests, and control-logic experiments. `replay` is reserved here so its CHECK value exists before 0021 builds it out.

## 0021 — replay simulation tier

Adds the operational storage for `sim_tier='replay'`. NL-gen workflows replay from the existing `iteration_case_outputs` table (no new table). Benchmark workflows (going through `SandboxRuntime`) replay from a new `captured_sandbox_runs` table — one row per captured Docker run, keyed `(iteration_id, call_idx)` so `ReplaySimSandbox` can cursor through calls in order.

## 0022 — LangSmith integration

Three pieces for shipping approved fixes back to a customer's LangSmith workspace: `workflows.origin` (NULL = greenfield, `'langsmith'` / `'copilot_studio'` = imported), `skills.langsmith_prompt_id` (the Prompt Hub id to push a new version to), and the binding needed for the "Ship fix to LangSmith" approval action, which only appears for `origin='langsmith'` workflows.

## 0023 — audit kind: fix shipped to LangSmith

ADD VALUE (outside a transaction). New `audit_kind` `fix-shipped-langsmith`: when an approved fix is pushed to LangSmith, the kernel writes a hash-chained row recording the LangSmith commit hash + URL.

## 0024 — single-push guard (LangSmith)

A partial unique index on `(kind, related_id) WHERE kind = 'fix-shipped-langsmith'` closes the TOCTOU race in the idempotent ship-langsmith endpoint: the second concurrent INSERT fails with `unique_violation`, which the route handles as an idempotent repeat. Partial so other audit kinds still allow multiple entries per `related_id`. `CREATE INDEX CONCURRENTLY` → `ownevo:no-txn`.

## 0025 — design-agent import log

Adds `workflows.design_agent_import_log jsonb` for the trace-import authoring surface — the reverse-discovery turn ("this agent does X — does this match your intent?") plus the reviewer's confirm/correct/skip and the discovery transcript — and a new `audit_kind` (`design-agent-negotiation-import`) so import-originated negotiation stays distinguishable from the written-description path's `design-agent-negotiation`.

## 0026 — audit kind: fix exported to Copilot Studio

ADD VALUE (outside a transaction). New `audit_kind` `fix-exported-copilot-studio`: Microsoft exposes no programmatic fix-feedback API, so an approved fix on a Copilot Studio-originated workflow is delivered as a plain-language diff the customer applies by hand; the kernel records the delivered diff text. Sibling to 0023.

## 0027 — single-delivery guard (Copilot Studio)

Mirrors 0024 for the Copilot Studio path: a partial unique index on `(kind, related_id) WHERE kind = 'fix-exported-copilot-studio'` makes a second concurrent delivery fail closed and be handled as an idempotent repeat. `CREATE INDEX CONCURRENTLY` → `ownevo:no-txn`.

## 0028 — agent registry + Copilot Studio eval-case-push audit kind

Two files share the `0028` prefix (lexicographic order: `agent_registry` before `audit_...`):

- **`0028_agent_registry.sql`** — `agent_registry` promotes the improvable unit behind a workflow to a first-class entity with a stable identity across config edits / rebuilds / re-imports, spanning greenfield and imported (LangSmith / Copilot Studio) origins. One agent per workflow (`UNIQUE workflow_id`); registration is idempotent.
- **`0028_audit_eval_cases_pushed_copilot_studio.sql`** — ADD VALUE: `audit_kind` `eval-cases-pushed-copilot-studio`, written when a workflow's (or cluster's) eval cases are pushed to the customer's deployed agent as a Copilot Studio test set via the Power Platform Evaluation API.

## 0029 — MCP servers

Adds `mcp_servers` so the loop consumes external MCP-exposed data sources (Slack, Google Workspace, Microsoft 365, ...) rather than bespoke connectors. Auth is split: `auth_config` holds non-secret token-minting parameters (safe to read back); `auth_secret_ciphertext` holds the sealed secret material (master key in `secrets/encrypted_field.py`), never returned to the API.

## 0030 — MCP OAuth authorization-code grant

Adds the state behind the interactive consent flow that 0029's token-refresh path didn't cover: `mcp_oauth_clients` (the per-provider OAuth app registration — `client_id` plus sealed `client_secret`) and `mcp_oauth_states` (a short-lived per-attempt nonce carried as the OAuth `state` parameter for CSRF defence + to recover the chosen server name / scopes / endpoint across the provider round-trip).

## 0031 — direct data uploads

Adds `data_uploads` for the reviewer whose data lives in files, not connected systems: a CSV / Excel / Parquet / PDF / DOCX is parsed once and the agent reads the parsed result by id every iteration. Only the normalized representation is stored (`schema` + `content`), not raw bytes; `sha256` + `size_bytes` + `name` are kept for provenance/dedupe. `retention_expires_at` (NULL = keep indefinitely) records when an upload may be purged.

## 0032 — event triggers

Adds the `trigger_kind` enum (`webhook` / `cron` / `threshold` / `slack` / `email` / `calendar`), the `triggers` table (per-workflow definitions with an application-validated opaque JSONB `config`), and `trigger_fires` (per-execution history backing the triggers page and the threshold evaluator's look-back queries).

## 0033 — workspace substrate

Multi-tenant substrate, **step 1 of 2 (non-enforcing)**. Creates the `workspaces` table and adds `workspace_id text NOT NULL DEFAULT 'default'` (FK to `workspaces(id)`) + a supporting index to all 17 workspace-scoped tables, backfilled to a single seeded `'default'` workspace. The constant default backfills existing rows without a table rewrite, and the FK validates trivially. RLS is deliberately **not** enabled yet so every read path can be verified against scoped data first. Demo-mode tables (0016) are excluded — they have no workspace dimension. The header comment lists three pre-conditions for step 2 (route `pool.acquire()` sites through a workspace-binding context manager; widen `integration_credentials` PK; scope the fingerprint index) — all resolved in 0034.

## 0034 — workspace RLS enforcement

Multi-tenant substrate, **step 2 of 2 (enforcement)**. For each of the 17 scoped tables: changes the `workspace_id` default from the literal `'default'` to `current_setting('app.workspace_id', true)` (an insert auto-stamps the session workspace; an unscoped connection's NULL fails the NOT NULL check → writes fail closed), then `ENABLE` + `FORCE ROW LEVEL SECURITY` with a `<table>_workspace_isolation` policy constraining reads (`USING`) and writes (`WITH CHECK`) to the session GUC. `FORCE` is required because the kernel connects as the table owner, which plain `ENABLE` leaves exempt. Also resolves 0033's three pre-conditions: widens `integration_credentials` PK to `(workspace_id, provider)`, re-creates `failure_clusters_fingerprint_unique` scoped to `(workspace_id, fingerprint)`, and adds `workspaces.deleted_at` for soft delete. `workspaces` itself stays out of RLS so `tenant_session.py` can look up existence / `deleted_at` before binding. After this lands, an unscoped connection sees zero rows and can insert nothing. The policy/default loop is idempotent; the `integration_credentials` PK swap is not.

## 0035 — authentication substrate

Adds the auth layer that resolves a request to a workspace (full design in [`AUTH.md`](AUTH.md)). Three **global, non-RLS** tables: `users` (one row per human, internal id, never the provider's subject), `user_identities` (`(provider, provider_sub)` PK — a user may link multiple sign-in providers), and `workspace_members` (`(workspace_id, user_id)` PK + `role` of `owner`/`admin`/`member`). These are deliberately outside row-level security: a person spans workspaces, and `workspace_members` is read by the per-request resolver *before* any workspace is bound, so scoping it by the GUC it helps establish would be circular — authorization is enforced in the resolver, not by RLS. The migration also seeds a `dev-user` who owns the `'default'` workspace so `make api` and the test suite work under the dev-auth fallback (`OWNEVO_DEV_AUTH=true`); the seed is inert in production, where the fallback is refused. Schema only — the kernel resolver and web sign-in flow land separately.

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
