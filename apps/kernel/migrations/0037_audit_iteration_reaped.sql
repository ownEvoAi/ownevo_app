-- 0037_audit_iteration_reaped.sql — new audit_kind for the startup reaper.
--
-- ADD VALUE
--
-- A kernel restart mid-iteration leaves the iteration row in 'running'
-- state forever: the in-flight task that would have written the final
-- UPDATE is gone. The workflow's one-iteration-at-a-time guard then
-- refuses every subsequent run on that workflow.
--
-- The startup reaper (jobs/orphan_reaper.py) scans the iterations table
-- once per boot, closes any row still in 'running' as 'sandbox-error',
-- and writes an audit_entries row of this kind so the action is visible
-- in the audit log and exportable in the chain.
--
-- ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
-- Postgres < 16, so the migration runner executes this file outside a
-- transaction (the "ADD VALUE" detection in scripts/migrate.py + db.py).

ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'iteration-reaped';
