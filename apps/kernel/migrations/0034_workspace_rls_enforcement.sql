-- 0034_workspace_rls_enforcement.sql
--
-- Multi-tenant substrate, step 2 of 2: turn on enforcement. Migration 0033
-- added the workspaces table and a workspace_id column to every scoped table
-- but deliberately left row-level security off so the columns could be
-- verified against backfilled data first. This migration flips the switch:
--
--   1. Every scoped table's workspace_id default becomes the active session
--      workspace (the app.workspace_id GUC) instead of the literal 'default',
--      so an INSERT auto-stamps the row with the connection's workspace. With
--      the GUC unset the default evaluates to NULL and the NOT NULL constraint
--      rejects the write -- a write on an unscoped connection fails closed.
--
--   2. ROW LEVEL SECURITY is ENABLEd and FORCEd on every scoped table, with a
--      single isolation policy per table that constrains both reads (USING)
--      and writes (WITH CHECK) to rows whose workspace_id equals the session
--      GUC. FORCE is required because the kernel connects as the table owner,
--      and a plain ENABLE leaves the owner exempt.
--
--   3. Three pre-conditions called out in 0033 are resolved: the
--      integration_credentials primary key is widened to (workspace_id,
--      provider); the failure-cluster fingerprint unique index is scoped by
--      workspace_id; and workspaces gains a deleted_at column for soft delete.
--
-- Once this lands, a connection with no app.workspace_id set sees zero rows in
-- every scoped table and cannot insert into any of them. tenant_session.py
-- (set_workspace / acquire_workspace_conn) is the single place that binds the
-- GUC, and it refuses to bind to a missing or soft-deleted workspace.

-- The workspaces table itself is the tenant registry, not tenant data: it is
-- intentionally NOT under RLS so tenant_session can look up a workspace's
-- existence / deleted_at before binding a session to it.
ALTER TABLE workspaces
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

-- Pre-condition 2: integration_credentials primary key must be per-workspace.
-- The (provider) PK would collide the moment a second workspace stores a key
-- for the same provider. The app upsert's ON CONFLICT target is updated to
-- (workspace_id, provider) in lockstep (api/_integration_credentials.py).
ALTER TABLE integration_credentials
    DROP CONSTRAINT integration_credentials_pkey;
ALTER TABLE integration_credentials
    ADD CONSTRAINT integration_credentials_pkey
    PRIMARY KEY (workspace_id, provider);

-- Pre-condition 3: scope the fingerprint dedup index by workspace so two
-- workspaces can independently dedup clusters with the same fingerprint.
-- The clustering upsert's ON CONFLICT target is updated to match
-- (clustering/persistence.py).
DROP INDEX IF EXISTS failure_clusters_fingerprint_unique;
CREATE UNIQUE INDEX failure_clusters_fingerprint_unique
    ON failure_clusters (workspace_id, fingerprint)
    WHERE fingerprint IS NOT NULL;

-- Pre-condition 1 (the pool.acquire() sweep) is a code change, not a schema
-- change -- every background-worker connection now binds the GUC via
-- acquire_workspace_conn before issuing a query. Nothing to do here.

-- Flip the default + enable/force RLS + install the isolation policy on each
-- scoped table. Driven from one list so the 17 tables stay in lockstep; adding
-- a scoped table later means adding one name here.
DO $$
DECLARE
    scoped_table text;
    scoped_tables text[] := ARRAY[
        'workflows',
        'skills',
        'skill_versions',
        'skill_deployments',
        'eval_cases',
        'failure_clusters',
        'traces',
        'iterations',
        'iteration_case_outputs',
        'proposals',
        'approvals',
        'meta_evals',
        'learnings',
        'captured_sandbox_runs',
        'receiver_tokens',
        'integration_credentials',
        'audit_entries'
    ];
BEGIN
    FOREACH scoped_table IN ARRAY scoped_tables LOOP
        -- An INSERT with no explicit workspace_id adopts the session workspace.
        -- current_setting(..., true) yields NULL when the GUC is unset, which
        -- the NOT NULL column then rejects -- unscoped writes fail closed.
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN workspace_id '
            'SET DEFAULT current_setting(''app.workspace_id'', true)',
            scoped_table
        );

        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', scoped_table);
        -- FORCE so the owning role the kernel connects as is not exempt.
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', scoped_table);

        -- Idempotent re-create so the migration is safe to re-apply against a
        -- partially-migrated database.
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON %I',
            scoped_table || '_workspace_isolation',
            scoped_table
        );
        -- USING governs which rows are visible to SELECT/UPDATE/DELETE;
        -- WITH CHECK governs which rows INSERT/UPDATE may write. Both compare
        -- the row's workspace_id to the session GUC, so an unset GUC (NULL)
        -- matches nothing and writes nothing.
        EXECUTE format(
            'CREATE POLICY %I ON %I '
            'USING (workspace_id = current_setting(''app.workspace_id'', true)) '
            'WITH CHECK (workspace_id = current_setting(''app.workspace_id'', true))',
            scoped_table || '_workspace_isolation',
            scoped_table
        );
    END LOOP;
END $$;
