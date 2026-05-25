-- 0026_audit_fix_exported_copilot_studio.sql — new audit_kind for the
-- Copilot Studio fix-delivery path.
--
-- ADD VALUE
--
-- Microsoft exposes no programmatic fix-feedback API: an approved fix on
-- a Copilot Studio-originated workflow is delivered as a plain-language
-- diff the customer applies by hand in the Copilot Studio UI
-- (POST /api/proposals/{id}/ship-copilot-studio). The kernel writes a
-- hash-chained audit_entries row of this kind recording the delivered
-- diff text. Sibling to 'fix-shipped-langsmith' (0023), which records the
-- programmatic LangSmith push.
--
-- ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
-- Postgres < 16, so the migration runner executes this file outside a
-- transaction (the "ADD VALUE" detection in scripts/migrate.py + db.py).

ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'fix-exported-copilot-studio';
