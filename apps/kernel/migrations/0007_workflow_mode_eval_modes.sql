-- 0007_workflow_mode_eval_modes.sql
--
-- Extend `workflow_mode` from a 2-value enum to a 4-value enum so
-- the operating mode covers the full Connect-existing-agent shape:
--
--   eval-only       — we score the agent on its eval suite; never
--                     propose changes. For customers using ownEvo
--                     as a regression harness only.
--   eval-propose    — we score AND propose changes, but never
--                     auto-deploy. Customer-applied fixes.
--   gated           — full loop, propose + auto-gate, human approval
--                     before deploy. (existing default)
--   autonomous      — full loop, gate-pass auto-deploys. (existing)
--
-- Postgres requires ADD VALUE to be issued OUTSIDE a transaction; the
-- migration runner (init scripts) runs each .sql file in its own
-- session, so these statements stand alone. Per Postgres docs the
-- new values are visible immediately in subsequent statements within
-- the same session.

ALTER TYPE workflow_mode ADD VALUE IF NOT EXISTS 'eval-only';
ALTER TYPE workflow_mode ADD VALUE IF NOT EXISTS 'eval-propose';

-- Workflows.mode default stays 'gated' so existing inserts keep
-- compiling; the on-ramp form picks 'eval-only' explicitly when the
-- user lands via the Connect-existing-agent flow.
