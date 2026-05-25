-- 0025_design_agent_import_log.sql — persist trace-import discovery.
--
-- ADD VALUE
--
-- The trace-import authoring surface (/workflows/connect/design) opens
-- with a reverse-discovery turn ("this agent does X — does this match
-- your intent?") that the reviewer confirms or corrects, then runs the
-- same metric / ambiguity / trigger / surface / premise interview the
-- written-description surface uses. This migration gives that path its
-- own persisted record, distinct from the authoring `design_agent_log`:
--
--   1. `workflows.design_agent_import_log` JSONB — the reverse-discovery
--      summary + the reviewer's confirm/correct/skip decision + the full
--      discovery transcript, stored alongside the spec / sim_plan /
--      metric so a future Audit-tab read renders the import conversation
--      without joining per-row audit entries. NULL when the workflow was
--      authored from a written description rather than imported traces.
--   2. A new `audit_kind` value mirroring those contents into the
--      hash-chained audit trail: one row for the reverse-discovery turn
--      and one per discovery Q/A, all under `design-agent-negotiation-import`
--      so import-originated negotiation stays distinguishable from the
--      written-description path's `design-agent-negotiation` rows.
--
-- ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
-- Postgres < 16, so the migration runner executes this file outside a
-- transaction (the "ADD VALUE" detection in scripts/migrate.py + db.py).
-- ALTER TABLE ... ADD COLUMN IF NOT EXISTS is safe outside a transaction
-- and idempotent across migration re-runs.

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS design_agent_import_log JSONB NULL;

ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'design-agent-negotiation-import';
