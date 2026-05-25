-- 0026_workspace_substrate.sql
--
-- Multi-tenant substrate, step 1 of 2: workspaces table + a workspace_id
-- column on every workspace-scoped domain table. This migration is
-- deliberately NON-enforcing — it adds the columns (backfilled to a single
-- 'default' workspace) and supporting indexes, but does NOT enable row-level
-- security. Enforcement (ENABLE ROW LEVEL SECURITY + isolation policies) is a
-- separate follow-up migration so the enforcement switch is isolated and
-- every read path can be verified against scoped data first.
--
-- Until RLS is enabled the workspace_id column is transparent: existing
-- single-tenant queries keep working unchanged because every row defaults to
-- the 'default' workspace and nothing filters on the column yet.
--
-- The default-valued NOT NULL column with a constant default applies to
-- existing rows without rewriting them, and the inline FK validates trivially
-- because every backfilled value is 'default', which is seeded first below.
--
-- Demo-mode infrastructure tables (demo_usage / demo_invite_revocations /
-- demo_budget_state) are intentionally excluded: they are global rate-limiting
-- state keyed by demo identity, not customer domain data, and have no
-- workspace dimension.

CREATE TABLE IF NOT EXISTS workspaces (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- The single workspace every pre-retrofit row belongs to. The string id keeps
-- the column type identical to the text foreign keys it will scope (workflows
-- already use text ids), so RLS policies compare like-typed values.
INSERT INTO workspaces (id, name)
    VALUES ('default', 'Default workspace')
    ON CONFLICT (id) DO NOTHING;

-- Add workspace_id to every workspace-scoped domain table. Each column is
-- NOT NULL DEFAULT 'default' with an FK to workspaces(id), plus an index to
-- keep the workspace-filtered scans RLS will add cheap.

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS workflows_workspace_idx ON workflows(workspace_id);

ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS skills_workspace_idx ON skills(workspace_id);

ALTER TABLE skill_versions
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS skill_versions_workspace_idx ON skill_versions(workspace_id);

ALTER TABLE skill_deployments
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS skill_deployments_workspace_idx ON skill_deployments(workspace_id);

ALTER TABLE eval_cases
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS eval_cases_workspace_idx ON eval_cases(workspace_id);

ALTER TABLE failure_clusters
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS failure_clusters_workspace_idx ON failure_clusters(workspace_id);

ALTER TABLE traces
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS traces_workspace_idx ON traces(workspace_id);

ALTER TABLE iterations
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS iterations_workspace_idx ON iterations(workspace_id);

ALTER TABLE iteration_case_outputs
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS iteration_case_outputs_workspace_idx
    ON iteration_case_outputs(workspace_id);

ALTER TABLE proposals
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS proposals_workspace_idx ON proposals(workspace_id);

ALTER TABLE approvals
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS approvals_workspace_idx ON approvals(workspace_id);

ALTER TABLE meta_evals
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS meta_evals_workspace_idx ON meta_evals(workspace_id);

ALTER TABLE learnings
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS learnings_workspace_idx ON learnings(workspace_id);

ALTER TABLE captured_sandbox_runs
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS captured_sandbox_runs_workspace_idx
    ON captured_sandbox_runs(workspace_id);

ALTER TABLE receiver_tokens
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS receiver_tokens_workspace_idx ON receiver_tokens(workspace_id);

ALTER TABLE integration_credentials
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS integration_credentials_workspace_idx
    ON integration_credentials(workspace_id);

-- audit_entries is append-only (WORM): UPDATE/DELETE/TRUNCATE are blocked by
-- trigger. ADD COLUMN is DDL and does not fire those row triggers, and the
-- constant default backfills existing rows without an UPDATE, so the WORM
-- guarantee is unaffected.
ALTER TABLE audit_entries
    ADD COLUMN IF NOT EXISTS workspace_id text NOT NULL DEFAULT 'default'
        REFERENCES workspaces(id);
CREATE INDEX IF NOT EXISTS audit_entries_workspace_idx ON audit_entries(workspace_id);

-- =============================================================================
-- Pre-conditions that MUST be resolved in the RLS enforcement migration (0027).
-- This migration is non-enforcing; enabling RLS before the items below are
-- addressed will cause silent data-isolation failures.
--
-- 1. pool.acquire() call sites in background workers (iteration_runner.py,
--    clustering/auto_trigger.py, eval_runner/try_runner.py,
--    sandbox/capturing.py, api/routes/nl_gen.py, api/routes/design_agent*.py,
--    etc.) bypass get_conn and therefore never call set_workspace. Under RLS
--    these connections will have an empty app.workspace_id GUC and will
--    silently operate against the wrong (or no) tenant. All direct
--    pool.acquire() uses must call set_workspace(conn, workspace_id) before
--    RLS is enabled. Introduce acquire_workspace_conn(pool, workspace_id)
--    as a shared context manager in tenant_session.py.
--
-- 2. integration_credentials PRIMARY KEY is (provider). It must be widened
--    to (workspace_id, provider) before the second workspace is provisioned;
--    otherwise two tenants storing credentials for the same provider collide.
--    The ON CONFLICT clause in the upsert must be updated to match.
--
-- 3. failure_clusters_fingerprint_unique is a partial unique index on
--    (fingerprint) with no workspace_id column. Include workspace_id in the
--    index so fingerprint dedup is scoped per workspace.
-- =============================================================================
