-- 0015b — widen `approvals.decision` CHECK constraint for "request-changes".
--
-- Runs inside a transaction (no ADD VALUE in this file) so the DROP +
-- re-add is atomic: if ADD CONSTRAINT fails for any reason, the old
-- constraint is automatically restored by rollback.
--
-- Drop + re-add by name is portable across Postgres versions; the
-- default constraint name `approvals_decision_check` matches what the
-- original CREATE TABLE generated.

ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_decision_check;
ALTER TABLE approvals ADD CONSTRAINT approvals_decision_check
    CHECK (decision IN ('approve', 'reject', 'request-changes'));
