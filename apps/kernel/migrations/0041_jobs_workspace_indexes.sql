-- 0041_jobs_workspace_indexes.sql — add workspace_id to jobs partial indexes.
--
-- The partial indexes added in 0040 for job claiming (jobs_claim_idx) and
-- stale-heartbeat scanning (jobs_heartbeat_idx) did not include workspace_id.
-- Under RLS, every query against these indexes applies the workspace filter as
-- a post-scan condition, so a scan visits all queued/running rows across all
-- workspaces before narrowing to the active workspace's rows.  With
-- workspace_id as the leading column the planner can seek directly to the
-- active workspace, keeping claim and stale-scan O(queued_per_workspace)
-- rather than O(queued_total) as tenant count grows.
--
-- jobs_active_per_workflow_idx already carries workspace_id and is unchanged.
-- The jobs table is new (migration 0040) and has no production traffic, so
-- standard transactional DROP + CREATE is safe — no CONCURRENTLY needed.

DROP INDEX jobs_claim_idx;
CREATE INDEX jobs_claim_idx
    ON jobs (workspace_id, available_at, created_at)
    WHERE status = 'queued';

DROP INDEX jobs_heartbeat_idx;
CREATE INDEX jobs_heartbeat_idx
    ON jobs (workspace_id, heartbeat_at)
    WHERE status = 'running';
