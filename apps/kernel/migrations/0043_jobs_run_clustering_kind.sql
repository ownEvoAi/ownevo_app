-- 0043_jobs_run_clustering_kind.sql
--
-- Add a `run_clustering` value to the `job_kind` enum so production-failure
-- clustering can run as a durable queue job, the same way `run_iteration`
-- already does. Before this, clustering ran inline — in the trigger
-- dispatcher and in the in-process debounced auto-trigger — so a kernel
-- restart mid-run dropped the work with no retry and no visibility in the
-- `jobs` table or the `ownevo_jobs` metric. Routing it through the queue
-- gives clustering the same durability, retry/backoff, and observability.
--
-- Postgres requires ADD VALUE to be issued OUTSIDE a transaction; the
-- migration runner detects `ADD VALUE` and runs this file in autocommit so
-- the new value is committed and immediately usable by later statements.

ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'run_clustering';
