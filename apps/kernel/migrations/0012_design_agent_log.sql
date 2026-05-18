-- 0012_design_agent_log.sql
--
-- Persist the design-agent discovery conversation + ambiguity report on
-- the workflow row, and surface them through the audit chain.
--
-- The /workflows/new/design route runs a short discovery interview
-- (metric / ambiguity / trigger / surface / premise) before NL-gen
-- generates the spec. Every answered question and every ambiguity
-- finding becomes auditable evidence of the deliberate authoring
-- choices the operator made — the compliance pitch for regulated
-- buyers (chief risk officer, chief medical officer) rests on this
-- chain being queryable per-workflow.
--
-- Two additions:
--   1. `workflows.design_agent_log` JSONB — full conversation transcript
--      + AmbiguityReport, persisted alongside the spec / sim_plan /
--      metric_definition. NULL when the operator skipped discovery.
--   2. Two new `audit_kind` enum values mirroring the row contents into
--      the hash-chained audit trail: one entry per Q/A
--      (`design-agent-negotiation`) and one for the full ambiguity report
--      (`design-agent-ambiguity`).

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS design_agent_log JSONB NULL;

-- PostgreSQL forbids adding enum values inside a transaction block when
-- the enum is referenced from a CHECK constraint or partitioning expression.
-- audit_kind is referenced from a typed column on audit_entries, which is
-- safe; the ALTER ... ADD VALUE IF NOT EXISTS form is idempotent across
-- migration re-runs.
ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'design-agent-negotiation';
ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'design-agent-ambiguity';
