-- 0027_audit_ship_copilot_studio_unique.sql — prevent double-delivery of a
-- Copilot Studio fix.
--
-- The ship-copilot-studio endpoint is idempotent by design: a second POST
-- on the same proposal detects a prior 'fix-exported-copilot-studio' audit
-- entry and returns the existing diff without re-recording. That
-- check-then-write pattern is a TOCTOU race: two concurrent requests both
-- pass the SELECT check before either writes, producing two audit entries
-- for the same approved fix.
--
-- A partial unique index on (kind, related_id) scoped to the
-- 'fix-exported-copilot-studio' kind makes the second INSERT fail with a
-- unique_violation, which the route catches and handles as an idempotent
-- repeat. Mirrors 0024 for the LangSmith path.
--
-- The index is partial so it doesn't constrain the full audit_entries
-- table — other kinds intentionally allow multiple entries per related_id.
--
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
-- ownevo:no-txn

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS audit_entries_ship_copilot_studio_once_idx
    ON audit_entries (kind, related_id)
    WHERE kind = 'fix-exported-copilot-studio';
