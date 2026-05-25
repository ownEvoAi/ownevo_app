-- 0028_audit_eval_cases_pushed_copilot_studio.sql — new audit_kind for the
-- Copilot Studio eval-case push path.
--
-- ADD VALUE
--
-- ownEvo can turn a workflow's (or a failure cluster's) eval cases into a
-- Copilot Studio test set and push them to the customer's deployed agent
-- via the Power Platform Evaluation API
-- (POST /api/workflows/{id}/push-eval-cases-copilot-studio). The kernel
-- writes a hash-chained audit_entries row of this kind recording the
-- created test-set id + case count. Sibling to 'fix-shipped-langsmith'
-- (0023) and 'fix-exported-copilot-studio' (0026).
--
-- ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
-- Postgres < 16, so the migration runner executes this file outside a
-- transaction (the "ADD VALUE" detection in scripts/migrate.py + db.py).

ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'eval-cases-pushed-copilot-studio';
