-- 0028_agent_registry.sql — registry of every connected agent.
--
-- An "agent" is the improvable unit behind a workflow: the thing that
-- runs, fails, gets clustered, and gets a proposed fix. Until now the
-- workflow row was the only handle on it. The registry promotes the
-- agent to a first-class entity with a stable identity that persists
-- across config edits, rebuilds, and re-imports, and a single index
-- spanning every origin — greenfield workflows authored in the kernel
-- and agents imported from external platforms (LangSmith, Copilot
-- Studio, ...).
--
-- One agent per workflow (UNIQUE workflow_id). Greenfield workflows
-- register an agent when they are created; imported agents register on
-- first trace ingestion. Registration is idempotent — the registry
-- only ever holds one row per workflow.
--
-- Single-tenant for MVP: no `workspace_id` column. The multi-tenant
-- retrofit adds one here alongside every other domain table; nothing in
-- this design fights that (the surrogate `id` PK stays, `workspace_id`
-- becomes part of a future composite uniqueness rule).
--
-- `origin` mirrors the vocabulary of `workflows.origin` but is NOT NULL:
-- a greenfield workflow has `workflows.origin IS NULL`, which registers
-- here as the explicit 'greenfield' value so the registry never shows a
-- blank origin column.
--
-- `identity_hash` is the stable agent identity (analog to a directory's
-- agent ID). It is minted once at registration and never changes, so an
-- agent keeps the same identity across config edits and re-imports. The
-- external-IAM resolution endpoint and the time-orderable generation
-- scheme are layered on in a follow-up; the column lives here so the
-- identity exists from the moment an agent is registered.

CREATE TABLE IF NOT EXISTS agents (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id         text        NOT NULL UNIQUE REFERENCES workflows(id),
    name                text        NOT NULL,
    origin              text        NOT NULL DEFAULT 'greenfield'
                            CHECK (origin IN ('greenfield', 'langsmith', 'copilot_studio')),
    owner               text,                       -- 'human:<id>' actor; nullable (no users table in MVP)
    status              text        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'paused', 'archived')),
    identity_hash       uuid        NOT NULL DEFAULT gen_random_uuid(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    status_updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agents_origin_idx ON agents(origin);
CREATE INDEX IF NOT EXISTS agents_status_idx ON agents(status);
CREATE INDEX IF NOT EXISTS agents_created_at_idx ON agents(created_at ASC, id ASC);

-- Backfill: workflows that existed before this migration get a registry row
-- immediately so the Agents page is not blank on first deploy. Name is
-- derived from the workflow description (capped, same as _derive_name in
-- registry.py); origin maps workflows.origin (NULL → 'greenfield').
-- Idempotent — ON CONFLICT DO NOTHING is a no-op on a fresh install.
INSERT INTO agents (workflow_id, name, origin)
SELECT
    id,
    CASE
        WHEN description IS NOT NULL AND trim(description) <> ''
            THEN left(trim(description), 200)
        ELSE id
    END,
    COALESCE(origin, 'greenfield')
FROM workflows
ON CONFLICT (workflow_id) DO NOTHING;
