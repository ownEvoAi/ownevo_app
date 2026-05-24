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
-- The CHECK constraint is declared `NOT VALID` so this migration runs
-- fast under ACCESS EXCLUSIVE — the existing rows are NOT scanned here.
-- All current rows have `ingest_source IS NULL` (the column was just
-- added above), which trivially satisfies the predicate; new INSERTs
-- and UPDATEs are still enforced immediately.
--
-- The subsequent VALIDATE CONSTRAINT and CREATE INDEX CONCURRENTLY are
-- in migration 0018b_traces_ingest_source_online.sql, which runs
-- outside a transaction (annotated `ownevo:no-txn`) so they can use
-- the lighter SHARE UPDATE EXCLUSIVE lock mode — that lock blocks DDL
-- but allows concurrent reads and writes on the high-volume traces table.

ALTER TABLE traces
    ADD COLUMN IF NOT EXISTS ingest_source text;

ALTER TABLE traces
    DROP CONSTRAINT IF EXISTS traces_ingest_source_chk;

ALTER TABLE traces
    ADD CONSTRAINT traces_ingest_source_chk
        CHECK (ingest_source IS NULL OR ingest_source IN ('otlp'))
        NOT VALID;
