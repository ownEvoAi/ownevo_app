-- 0042_jobs_list_indexes.sql — indexes for the GET /api/jobs list endpoint.
--
-- `GET /api/jobs` runs two queries against the workspace-scoped jobs table:
--
--   SELECT status, count(*) AS n FROM jobs GROUP BY status
--   SELECT ... FROM jobs [WHERE status = $1] ORDER BY created_at DESC LIMIT $N
--
-- Both execute under RLS (workspace_id = current GUC), but no index covers
-- either access pattern. With no pruning policy, succeeded and failed rows
-- accumulate monotonically, so both queries degrade as the table grows.
--
-- jobs_status_idx covers the GROUP BY count query (and status-filtered lists).
-- jobs_list_idx   covers the ORDER BY created_at DESC list query.
--
-- The jobs table is new (migration 0040) with no production traffic, so
-- transactional CREATE (no CONCURRENTLY) is safe here.

CREATE INDEX jobs_status_idx
    ON jobs (workspace_id, status);

CREATE INDEX jobs_list_idx
    ON jobs (workspace_id, created_at DESC);
