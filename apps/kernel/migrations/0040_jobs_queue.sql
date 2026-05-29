-- 0040_jobs_queue.sql — durable background-job queue.
--
-- Some background work is requested by a trigger rather than a connected
-- HTTP client: when a cron/threshold/Slack/email trigger fires the
-- `run_iteration` action, the kernel must run one improvement-loop
-- iteration with no request to block on. Spawning that as an in-process
-- task loses the work on any kernel restart — the task dies and nothing
-- retries it.
--
-- This table is the durable record of such work. A row is inserted before
-- the work is dispatched (persist-before-dispatch), a worker claims it with
-- FOR UPDATE SKIP LOCKED, heartbeats while running, and either completes it
-- or retries it with backoff. A worker that dies mid-job leaves the row in
-- 'running' with a stale heartbeat; the next worker poll re-queues it, so
-- the work survives a restart instead of vanishing.
--
-- Workspace-scoped under FORCE ROW LEVEL SECURITY, exactly like the domain
-- tables (migration 0034): every queue access binds app.workspace_id via
-- tenant_session, and an unscoped connection sees no rows and cannot insert.

CREATE TYPE job_status AS ENUM (
    'queued',      -- waiting to be claimed (available_at <= now())
    'running',     -- claimed by a worker; heartbeat_at is being renewed
    'succeeded',   -- finished; result holds the outcome
    'failed'       -- terminal failure (retries exhausted); last_error holds why
);

-- The kind of work. Extensible: a new background-job type adds a value here
-- and a branch in the worker's dispatch. Today only iteration runs enqueue.
CREATE TYPE job_kind AS ENUM ('run_iteration');

CREATE TABLE jobs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Auto-stamped with the session workspace; an unscoped insert (GUC unset
    -- -> NULL) fails closed against NOT NULL, same as every scoped table.
    workspace_id    text NOT NULL
                        DEFAULT current_setting('app.workspace_id', true),
    kind            job_kind NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          job_status NOT NULL DEFAULT 'queued',
    attempts        int NOT NULL DEFAULT 0,
    max_attempts    int NOT NULL DEFAULT 3,
    -- Owner of the in-flight claim + its liveness signal. All three are
    -- cleared (set to NULL) when the job returns to 'queued' (via fail_job
    -- on a retryable error or requeue_stale_jobs on a dead worker).
    claimed_by      text,
    claimed_at      timestamptz,
    heartbeat_at    timestamptz,
    -- Not eligible to be claimed before this time — used for retry backoff.
    available_at    timestamptz NOT NULL DEFAULT now(),
    last_error      text,
    result          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Claim ordering: the worker selects the oldest ready 'queued' row. Partial
-- so the index stays small and only covers claimable rows.
CREATE INDEX jobs_claim_idx
    ON jobs (available_at, created_at)
    WHERE status = 'queued';

-- Stale-claim scan: the worker re-queues 'running' rows whose heartbeat has
-- lapsed (their worker died).
CREATE INDEX jobs_heartbeat_idx
    ON jobs (heartbeat_at)
    WHERE status = 'running';

-- At most one active (queued or running) job per (workspace, kind, workflow).
-- Makes enqueue idempotent and preserves the one-iteration-at-a-time intent
-- the run-iteration HTTP endpoint already enforces for manual runs.
CREATE UNIQUE INDEX jobs_active_per_workflow_idx
    ON jobs (workspace_id, kind, (payload->>'workflow_id'))
    WHERE status IN ('queued', 'running');

-- Row-level security, identical idiom to migration 0034: USING governs
-- read/update/delete visibility, WITH CHECK governs insert/update writes,
-- both pinned to the session GUC. FORCE so the owning role the kernel
-- connects as is not exempt; an unset GUC (NULL) matches nothing.
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY jobs_workspace_isolation ON jobs
    USING (workspace_id = current_setting('app.workspace_id', true))
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true));
