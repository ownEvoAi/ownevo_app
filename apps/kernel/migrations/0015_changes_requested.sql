-- 0015 — proposal "Request changes" state + matching audit kind.
--
-- Adds a new terminal-ish state for proposals: `changes-requested`. A
-- gate-passed proposal can transition here when a domain expert wants
-- to keep the loop alive but redirect it with plain-language steering
-- text (e.g. "soften the seasonal cap"). The next iteration on the
-- workflow injects the steering text into the agent + proposer prompt
-- so the fresh proposal reflects the feedback.
--
-- Distinct from `rejected` (which closes the proposal and seeds an
-- eval case from the comment). Distinct from `approved-awaiting-deploy`
-- (which ships the proposal as-is).
--
-- Postgres ENUM additions must run outside a transaction; this file is
-- single-statement so the migration runner doesn't wrap it.

ALTER TYPE proposal_state ADD VALUE IF NOT EXISTS 'changes-requested';
ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'proposal-changes-requested';

-- Allow the new decision value on approvals.decision. Drop + re-add by
-- name is portable across Postgres versions; the default constraint
-- name `approvals_decision_check` matches what CREATE TABLE generated.
ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_decision_check;
ALTER TABLE approvals ADD CONSTRAINT approvals_decision_check
    CHECK (decision IN ('approve', 'reject', 'request-changes'));
