-- 0021_audit_fix_shipped_langsmith.sql — new audit_kind for fix delivery.
--
-- ADD VALUE
--
-- When an approved fix is pushed back to a customer's LangSmith
-- workspace (POST /api/proposals/{id}/ship-langsmith), the kernel writes
-- a hash-chained audit_entries row of this kind recording the LangSmith
-- commit hash + URL. Mirrors the existing `proposal-deployed` /
-- `workflow-agent-model-changed` audit kinds.
--
-- ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
-- Postgres < 16, so the migration runner executes this file outside a
-- transaction (the "ADD VALUE" detection in scripts/migrate.py + db.py).

ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'fix-shipped-langsmith';
