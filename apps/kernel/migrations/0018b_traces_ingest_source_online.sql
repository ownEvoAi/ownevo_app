-- 0018b_traces_ingest_source_online.sql — online validation + index
--
-- ownevo:no-txn
--
-- This migration MUST run outside a transaction. Both statements below
-- require it:
--
--   VALIDATE CONSTRAINT
--     Scans existing rows under SHARE UPDATE EXCLUSIVE (allows concurrent
--     reads and writes). Inside a transaction the ACCESS EXCLUSIVE lock
--     from the ADD CONSTRAINT NOT VALID in migration 0018 would still be
--     held, defeating the purpose. Running outside a transaction lets
--     Postgres release the ACCESS EXCLUSIVE lock before this scan begins.
--
--   CREATE INDEX CONCURRENTLY
--     Postgres refuses CREATE INDEX CONCURRENTLY inside a transaction
--     block entirely (raises ERROR: CREATE INDEX CONCURRENTLY cannot run
--     inside a transaction block).
--
-- The migration runner records this migration in a short separate
-- transaction after both statements complete.

ALTER TABLE traces
    VALIDATE CONSTRAINT traces_ingest_source_chk;

CREATE INDEX CONCURRENTLY IF NOT EXISTS traces_ingest_source_idx
    ON traces(ingest_source)
    WHERE ingest_source IS NOT NULL;
