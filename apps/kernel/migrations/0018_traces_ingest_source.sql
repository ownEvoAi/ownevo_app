-- 0018_traces_ingest_source.sql — distinguish kernel-emitted traces
-- from traces ingested over the OTLP-JSON receiver.
--
-- Without this column, traces produced inside this kernel (TraceCollector
-- on a gate iteration) are indistinguishable from traces received over
-- the `/api/otel/v1/traces` ingest from an external collector. The two
-- have different provenance guarantees: kernel-emitted events are
-- structurally trusted (the typed AgentEvent constructor enforces
-- shape), whereas ingested events were translated from an external
-- OTel span stream and may carry warnings, partial fields, or vendor
-- extensions whose provenance is unattested.
--
-- The column is nullable on purpose:
--   * NULL  → legacy / kernel-emitted (the existing default)
--   * 'otlp' → ingested via the OTLP-JSON receiver
--
-- A NOT NULL default would either rewrite every existing row at
-- migration time (slow on the high-volume traces table) or force the
-- TraceCollector to set an explicit value (touching unrelated code).
-- Nullable + a CHECK on the small enum of accepted values keeps the
-- change local to the ingest path while leaving room for future
-- sources ('webhook', 'replay', etc.).
--
-- ONLINE-DDL NOTES
-- ----------------
-- `ADD COLUMN <text>` is metadata-only in Postgres 11+ (the column
-- has no default and is nullable, so existing rows aren't rewritten —
-- the new value is materialised lazily on first write to each row).
--
-- The CHECK constraint is declared `NOT VALID` so the migration does
-- not scan the existing `traces` rows under an ACCESS EXCLUSIVE lock.
-- All current rows have `ingest_source IS NULL` (the column was just
-- added in the previous statement), which trivially satisfies the
-- predicate; new INSERTs and UPDATEs are still enforced. The
-- subsequent `VALIDATE CONSTRAINT` upgrades the constraint to fully
-- valid under a SHARE UPDATE EXCLUSIVE lock — that lock blocks DDL
-- but allows concurrent reads and writes, so it is safe to run
-- online on a large table.

ALTER TABLE traces
    ADD COLUMN IF NOT EXISTS ingest_source text;

ALTER TABLE traces
    DROP CONSTRAINT IF EXISTS traces_ingest_source_chk;

ALTER TABLE traces
    ADD CONSTRAINT traces_ingest_source_chk
        CHECK (ingest_source IS NULL OR ingest_source IN ('otlp'))
        NOT VALID;

ALTER TABLE traces
    VALIDATE CONSTRAINT traces_ingest_source_chk;

CREATE INDEX IF NOT EXISTS traces_ingest_source_idx
    ON traces(ingest_source)
    WHERE ingest_source IS NOT NULL;
