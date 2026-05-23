-- 0015a — proposal "Request changes" state + matching audit kind (enum additions).
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
-- Postgres ENUM additions cannot run inside a transaction on Postgres < 16.
-- The migration runner detects ADD VALUE and executes this file in
-- autocommit mode. The CHECK constraint change is in a separate file
-- (0015b) so it can run inside a transaction and roll back on failure.

ALTER TYPE proposal_state ADD VALUE IF NOT EXISTS 'changes-requested';
ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'proposal-changes-requested';
