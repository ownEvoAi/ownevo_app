-- Composite indexes supporting keyset pagination on the two trace-list endpoints.
--
-- Both list endpoints order by (started_at DESC, id DESC) and, on cursor
-- walks, filter with the row-value predicate:
--
--   (t.started_at, t.id) < ($ts::timestamptz, $id::uuid)
--
-- PostgreSQL cannot resolve a row-value comparison from single-column indexes
-- alone; a composite index on both columns is required for the planner to
-- execute cursor-page queries as a backward index scan rather than a
-- sequential scan + sort + filter.
--
-- RLS appends `workspace_id = current_setting('app.workspace_id')` to every
-- query, so we lead with workspace_id to confine each scan to one tenant's
-- rows before the time-ordered traversal.

CREATE INDEX CONCURRENTLY IF NOT EXISTS traces_ws_started_id_idx
    ON traces (workspace_id, started_at DESC, id DESC);

-- Per-workflow list: filters by workflow_id first, then walks the keyset.
CREATE INDEX CONCURRENTLY IF NOT EXISTS traces_wf_started_id_idx
    ON traces (workflow_id, started_at DESC, id DESC);
