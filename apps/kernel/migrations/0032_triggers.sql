-- 0032_triggers.sql — generic event-trigger definitions (Track 17.1)
--
-- Stores the live trigger definitions for each workflow. The `kind` column
-- discriminates between the six trigger backends. `config` is a JSONB blob
-- validated at the application layer (Pydantic) before write; the DB stores
-- it opaquely so kind-specific fields can evolve without schema migrations.
--
-- `trigger_fires` records each execution: when it fired, what action it ran,
-- and whether it succeeded or failed. This gives the triggers page its
-- history view and supports the threshold evaluator's look-back queries.

BEGIN;

CREATE TYPE trigger_kind AS ENUM (
    'webhook',      -- inbound HMAC-signed HTTP POST
    'cron',         -- time-based schedule (cron expression)
    'threshold',    -- metric aggregate crosses a configured value
    'slack',        -- Slack channel message ingestion
    'email',        -- Gmail / Outlook thread ingestion
    'calendar'      -- Google / Outlook Calendar event proximity
);

CREATE TYPE trigger_action AS ENUM (
    'run_clustering',    -- run cluster_production_failures for this workflow
    'run_iteration',     -- start one improvement-loop iteration
    'ingest_failures'    -- convert ingested content to production_failure AgentEvents
);

CREATE TABLE trigger_definitions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    name            TEXT NOT NULL CHECK (char_length(name) > 0),
    kind            trigger_kind NOT NULL,
    action          trigger_action NOT NULL DEFAULT 'run_clustering',
    config          JSONB NOT NULL DEFAULT '{}',
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_fired_at   TIMESTAMPTZ,
    fire_count      INTEGER NOT NULL DEFAULT 0
);

-- Perf: most queries filter by workflow + enabled.
CREATE INDEX trigger_definitions_workflow_idx
    ON trigger_definitions (workflow_id)
    WHERE enabled = TRUE;

-- Threshold evaluator looks up definitions by workflow + kind.
CREATE INDEX trigger_definitions_kind_idx
    ON trigger_definitions (workflow_id, kind)
    WHERE enabled = TRUE;

-- Trigger fire history — append-only log of each execution.
CREATE TABLE trigger_fires (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_id      UUID NOT NULL REFERENCES trigger_definitions(id) ON DELETE CASCADE,
    workflow_id     UUID NOT NULL,
    fired_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    action          trigger_action NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error')),
    error_message   TEXT,
    payload_summary TEXT    -- short human-readable description of what fired
);

CREATE INDEX trigger_fires_trigger_idx
    ON trigger_fires (trigger_id, fired_at DESC);

CREATE INDEX trigger_fires_workflow_idx
    ON trigger_fires (workflow_id, fired_at DESC);

-- Restrict direct mutation: fires are append-only (same as audit_entries).
REVOKE UPDATE, DELETE ON trigger_fires FROM PUBLIC;

-- Metric samples table — threshold evaluator polls this for rolling aggregates.
-- External systems (or internal kernel paths) write rows here; the threshold
-- poller queries them with a time-window GROUP BY.
CREATE TABLE metric_samples (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    metric_name     TEXT NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT   -- free-form tag: 'gate', 'benchmark', 'external', etc.
);

CREATE INDEX metric_samples_lookup_idx
    ON metric_samples (workflow_id, metric_name, recorded_at DESC);

COMMIT;
