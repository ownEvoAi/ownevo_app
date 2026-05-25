-- 0022_audit_ship_langsmith_unique.sql — prevent double-push to LangSmith.
--
-- The ship-langsmith endpoint is idempotent by design: a second POST on the
-- same proposal detects a prior 'fix-shipped-langsmith' audit entry and
-- returns the existing result without pushing again. That check-then-write
-- pattern is a TOCTOU race: two concurrent requests both pass the SELECT
-- check before either writes the audit entry, producing two LangSmith commits
-- for the same approved fix.
--
-- A partial unique index on (kind, related_id) scoped to the
-- 'fix-shipped-langsmith' kind makes the second INSERT fail with a
-- unique_violation, which the route catches and handles as an idempotent
-- repeat (returning the existing audit entry).
--
-- The index is partial (WHERE kind = 'fix-shipped-langsmith') so it doesn't
-- constrain the full audit_entries table — other kinds (proposal-deployed,
-- cluster-created, etc.) intentionally allow multiple entries per related_id.
--
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
-- ownevo:no-txn

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS audit_entries_ship_langsmith_once_idx
    ON audit_entries (kind, related_id)
    WHERE kind = 'fix-shipped-langsmith';
