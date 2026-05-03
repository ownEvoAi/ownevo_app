-- 0001_substrate.sql
-- ownEvo MVP substrate schema. Locked 2026-05-03 by CEO review v4.3 + eng review.
--
-- Decisions reflected:
--   D2 — append-only audit log (WORM); crypto-grade tamper-evidence is Phase 2.
--   D3 — local Docker sandbox; sandbox_error_class enum captures structured failures.
--   D4 — single-tenant for MVP. NO `workspace_id` columns. Phase-2 retrofit will add
--        them to every domain table; design here is "retrofit-friendly" — no patterns
--        that fight a future workspace_id (e.g., no composite PKs that would need to
--        be widened).
--   D7 — meta_evals table stores NL-gen meta-eval results (LLM-judge-vs-human).
--
-- The audit log is the spine. Every state change in proposals/iterations/skills
-- writes an audit_entries row. Export = `SELECT * FROM audit_entries ORDER BY seq`.
--
-- Phase-2 retrofit checklist (D4):
--   ALTER TABLE skills ADD COLUMN workspace_id text NOT NULL;
--   (... same for skill_versions, eval_cases, traces, failure_clusters, iterations,
--    proposals, approvals, audit_entries, meta_evals, learnings, workflows)
--   CREATE POLICY ... ON each table USING (workspace_id = current_setting('app.workspace_id'));
--   ALTER TABLE ... ENABLE ROW LEVEL SECURITY;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- ENUMs
-- =============================================================================

CREATE TYPE skill_kind AS ENUM ('python', 'instruction', 'composite');

CREATE TYPE provenance_kind AS ENUM (
    'hand-authored',
    'cluster-derived',
    'nl-gen',
    'retention-violation',
    'rejected-feedback'
);

CREATE TYPE iteration_state AS ENUM (
    'running',
    'gate-pass',
    'gate-blocked-regression',
    'gate-blocked-no-improvement',
    'sandbox-error'
);

CREATE TYPE proposal_state AS ENUM (
    'pending',
    'in-gate',
    'gate-failed',
    'gate-passed',
    'rejected',
    'approved-awaiting-deploy',
    'deployed',
    'rolled-back'
);

CREATE TYPE workflow_mode AS ENUM ('gated', 'autonomous');

CREATE TYPE approver_type AS ENUM ('human', 'llm-judge', 'autonomous');

-- D3 — explicit sandbox failure classes; gate consumer does NOT advance best-ever
-- when iteration ends with one of these.
CREATE TYPE sandbox_error_class AS ENUM ('Timeout', 'OOM', 'Crash');

CREATE TYPE audit_kind AS ENUM (
    'skill-version-created',
    'gate-run-started',
    'gate-run-completed',
    'proposal-created',
    'proposal-approved',
    'proposal-rejected',
    'proposal-deployed',
    'proposal-rolled-back',
    'eval-case-added',
    'cluster-created',
    'cluster-relabeled',
    'workflow-created',
    'meta-eval-result',
    'schema-migration',
    'deployment-created',
    'deployment-updated'
);

-- =============================================================================
-- workflows — the user's described workflow + generated artifacts (NL-gen)
-- =============================================================================
-- Created first because skills/eval_cases/etc. reference workflow_id.

CREATE TABLE workflows (
    id                  text PRIMARY KEY,
    description         text NOT NULL,                    -- the original user description
    spec                jsonb NOT NULL,                   -- generated workflow spec (frozen schema, W3)
    metric_id           text,                             -- name of the success metric
    sim_skill_id        text,                             -- FK added after skills table exists
    meta_eval_score     numeric(3,2),                     -- description-coverage 0.00-1.00 (D7)
    mode                workflow_mode NOT NULL DEFAULT 'gated',  -- 'gated' requires human/llm-judge; 'autonomous' auto-approves gate-pass
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- =============================================================================
-- skills + skill_versions
-- =============================================================================

CREATE TABLE skills (
    id                  text PRIMARY KEY,
    kind                skill_kind NOT NULL,
    head_version_id     uuid,                             -- FK added below (cyclic)
    workflow_id         text REFERENCES workflows(id),    -- nullable: substrate skills not bound to a workflow
    capability_tags     text[] NOT NULL DEFAULT '{}',
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE skill_versions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id            text NOT NULL REFERENCES skills(id),
    parent_version_id   uuid REFERENCES skill_versions(id),
    version_seq         integer NOT NULL,                 -- monotonic per skill_id (1, 2, 3...)
    content             text NOT NULL,                    -- raw skill source (Python or markdown)
    retention_block     jsonb,                            -- parsed YAML frontmatter; see SKILL_FORMAT.md
    diff_summary        text,                             -- human-readable diff from parent
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text NOT NULL,                    -- 'agent:<model>' | 'human:<id>' | 'nl-gen'
    UNIQUE (skill_id, version_seq)
);

ALTER TABLE skills
    ADD CONSTRAINT skills_head_fk FOREIGN KEY (head_version_id) REFERENCES skill_versions(id);

ALTER TABLE workflows
    ADD CONSTRAINT workflows_sim_skill_fk FOREIGN KEY (sim_skill_id) REFERENCES skills(id);

CREATE INDEX skill_versions_skill_idx ON skill_versions(skill_id);
CREATE INDEX skill_versions_parent_idx ON skill_versions(parent_version_id);
CREATE INDEX skills_workflow_idx ON skills(workflow_id);
CREATE INDEX skills_capability_tags_idx ON skills USING gin(capability_tags);

-- =============================================================================
-- skill_deployments — deployment configs for A/B testing and per-model variants
-- =============================================================================
-- Each row is a named deployment of a skill: same content, different runtime config
-- (model, temperature, tools, etc.). Traffic weights control call routing. Iterations
-- reference deployment_id so the lift chart can compare variants on the same eval set.

CREATE TABLE skill_deployments (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id        text NOT NULL REFERENCES skills(id),
    config_tag      text NOT NULL,                           -- 'control' | 'opus-low-temp' | 'sonnet-v2'
    model_id        text NOT NULL,                           -- 'claude-opus-4-7' | 'claude-sonnet-4-6' etc.
    run_config      jsonb NOT NULL DEFAULT '{}',             -- temperature, tools, system_prompt_override, timeout_ms
    traffic_weight  numeric(3,2) NOT NULL DEFAULT 1.00
                        CHECK (traffic_weight >= 0 AND traffic_weight <= 1),
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (skill_id, config_tag)
);

CREATE INDEX skill_deployments_skill_idx ON skill_deployments(skill_id);
CREATE INDEX skill_deployments_active_idx ON skill_deployments(skill_id) WHERE is_active = true;

-- =============================================================================
-- eval_cases
-- =============================================================================

CREATE TABLE eval_cases (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id             text REFERENCES workflows(id),
    provenance              provenance_kind NOT NULL,
    cluster_id              uuid,                         -- FK added after failure_clusters
    input                   jsonb NOT NULL,
    expected_behavior       jsonb NOT NULL,
    regression_tolerance    numeric(5,4),                 -- e.g., 0.0500 = 5pp tolerance
    is_test_fold            boolean NOT NULL DEFAULT false, -- train/test discipline
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX eval_cases_workflow_idx ON eval_cases(workflow_id);
CREATE INDEX eval_cases_provenance_idx ON eval_cases(provenance);
CREATE INDEX eval_cases_test_fold_idx ON eval_cases(is_test_fold) WHERE is_test_fold = true;

-- =============================================================================
-- failure_clusters
-- =============================================================================

CREATE TABLE failure_clusters (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id             text REFERENCES workflows(id),
    label                   text NOT NULL,                -- LLM-generated; eval'd for hallucination
    label_eval_score        numeric(3,2),                 -- judge-vs-human agreement (D4)
    severity                text NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    centroid                vector(384),                  -- sentence-transformers all-MiniLM-L6-v2 dim
    sample_trace_ids        uuid[],
    cluster_size            integer NOT NULL,
    quality_score           numeric(3,2),                 -- HDBSCAN cluster persistence; below threshold = "needs more iterations"
    created_at              timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE eval_cases
    ADD CONSTRAINT eval_cases_cluster_fk FOREIGN KEY (cluster_id) REFERENCES failure_clusters(id);

CREATE INDEX failure_clusters_workflow_idx ON failure_clusters(workflow_id);
CREATE INDEX failure_clusters_centroid_idx ON failure_clusters
    USING ivfflat (centroid vector_cosine_ops) WITH (lists = 50);

-- =============================================================================
-- traces (AgentEvent stream)
-- =============================================================================
-- High-volume table; ClickHouse migration is Phase 2. For MVP, Postgres + monthly partitioning.

CREATE TABLE traces (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id         text REFERENCES workflows(id),
    iteration_id        uuid,                             -- nullable; production traces aren't from iterations
    skill_version_id    uuid REFERENCES skill_versions(id),
    events              jsonb NOT NULL,                   -- array of AgentEvent (typed per packages/trace-format/)
    started_at          timestamptz NOT NULL,
    ended_at            timestamptz,
    metric_outputs      jsonb,                            -- predictions, decisions, etc.
    token_usage         jsonb                             -- {input, output, cache_hit} per provider
);

CREATE INDEX traces_workflow_idx ON traces(workflow_id);
CREATE INDEX traces_iteration_idx ON traces(iteration_id);
CREATE INDEX traces_started_idx ON traces(started_at);
CREATE INDEX traces_skill_version_idx ON traces(skill_version_id);

-- =============================================================================
-- iterations (gate runs + replay state)
-- =============================================================================

CREATE TABLE iterations (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id                 text NOT NULL REFERENCES workflows(id),
    iteration_index             integer NOT NULL,         -- replay day or sequential count
    proposed_skill_version_id   uuid REFERENCES skill_versions(id),
    parent_skill_version_id     uuid REFERENCES skill_versions(id),
    state                       iteration_state NOT NULL DEFAULT 'running',
    sandbox_error_class         sandbox_error_class,      -- non-null iff state='sandbox-error' (D3)
    val_score                   numeric(10,6),
    best_ever_score_before      numeric(10,6),
    best_ever_score_after       numeric(10,6),
    cluster_id                  uuid REFERENCES failure_clusters(id),  -- which cluster triggered this iteration
    deployment_id               uuid REFERENCES skill_deployments(id),  -- nullable; null = no deployment config
    token_budget_used           integer,
    token_budget_total          integer,
    started_at                  timestamptz NOT NULL DEFAULT now(),
    ended_at                    timestamptz,
    UNIQUE (workflow_id, iteration_index)
);

CREATE INDEX iterations_workflow_idx ON iterations(workflow_id);
CREATE INDEX iterations_state_idx ON iterations(state);
CREATE INDEX iterations_deployment_idx ON iterations(deployment_id);

ALTER TABLE traces
    ADD CONSTRAINT traces_iteration_fk FOREIGN KEY (iteration_id) REFERENCES iterations(id);

-- =============================================================================
-- proposals (agent-proposed skill changes pending decision)
-- =============================================================================

CREATE TABLE proposals (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    iteration_id            uuid NOT NULL REFERENCES iterations(id),
    skill_id                text NOT NULL REFERENCES skills(id),
    parent_version_id       uuid REFERENCES skill_versions(id),
    proposed_content        text NOT NULL,
    plain_language_summary  text NOT NULL,
    expected_impact         jsonb,                        -- {improves: [...eval_case_ids], regresses: [...]}
    state                   proposal_state NOT NULL DEFAULT 'pending',
    eval_score              numeric(3,2) CHECK (eval_score IS NULL OR (eval_score >= 0 AND eval_score <= 1)),  -- LLM-judge stub score (W2)
    eval_rationale          text,                         -- LLM-judge plain-language reason (W2)
    created_at              timestamptz NOT NULL DEFAULT now(),
    state_updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX proposals_state_idx ON proposals(state);
CREATE INDEX proposals_iteration_idx ON proposals(iteration_id);
CREATE INDEX proposals_pending_idx ON proposals(created_at)
    WHERE state IN ('pending', 'gate-passed');

-- =============================================================================
-- approvals (resolved decisions on proposals)
-- =============================================================================

CREATE TABLE approvals (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id             uuid NOT NULL REFERENCES proposals(id),
    decided_by              text NOT NULL,                -- 'human:<id>' | 'llm-judge' | 'autonomous'
    approver_type           approver_type NOT NULL,
    decision                text NOT NULL CHECK (decision IN ('approve', 'reject')),
    comment                 text,
    became_eval_case_id     uuid REFERENCES eval_cases(id),  -- if reject + comment
    decided_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (proposal_id)                                  -- one resolved decision per proposal
);

CREATE INDEX approvals_proposal_idx ON approvals(proposal_id);

-- =============================================================================
-- audit_entries (append-only WORM per D2)
-- =============================================================================

CREATE TABLE audit_entries (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq                 bigserial UNIQUE NOT NULL,        -- guaranteed monotonic for export ordering
    kind                audit_kind NOT NULL,
    payload             jsonb NOT NULL,
    related_id          uuid,                             -- proposal_id / iteration_id / cluster_id depending on kind
    actor               text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_entries_kind_idx ON audit_entries(kind);
CREATE INDEX audit_entries_seq_idx ON audit_entries(seq);
CREATE INDEX audit_entries_related_idx ON audit_entries(related_id);
CREATE INDEX audit_entries_created_idx ON audit_entries(created_at);

-- WORM enforcement (D2). Belt-and-suspenders:
--   1. Trigger raises on UPDATE/DELETE, even from superuser unless explicitly disabled.
--   2. App role grants (set in 0002_grants.sql or env-specific): only INSERT, SELECT.

CREATE OR REPLACE FUNCTION audit_entries_worm() RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_entries is append-only (WORM); UPDATE/DELETE/TRUNCATE forbidden. To bypass for schema migration, drop trigger explicitly.';
END;
$$;

CREATE TRIGGER audit_entries_no_update
    BEFORE UPDATE ON audit_entries
    FOR EACH ROW EXECUTE FUNCTION audit_entries_worm();

CREATE TRIGGER audit_entries_no_delete
    BEFORE DELETE ON audit_entries
    FOR EACH ROW EXECUTE FUNCTION audit_entries_worm();

-- TRUNCATE is statement-level and bypasses BEFORE UPDATE/DELETE row triggers.
-- Without this, a superuser (e.g., local dev, ad-hoc migration scripts) could
-- empty the audit log silently. Layer 2 (role grants) is the production answer
-- but this guards dev/test environments where the app role is not enforced.
CREATE TRIGGER audit_entries_no_truncate
    BEFORE TRUNCATE ON audit_entries
    FOR EACH STATEMENT EXECUTE FUNCTION audit_entries_worm();

-- =============================================================================
-- meta_evals (NL-gen meta-eval per D7)
-- =============================================================================

CREATE TABLE meta_evals (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id         text NOT NULL REFERENCES workflows(id),
    description         text NOT NULL,                    -- copy of workflow description at eval time
    coverage_score      numeric(3,2) NOT NULL,            -- 0.00-1.00
    per_dimension       jsonb NOT NULL,                   -- {sim_completeness: 0.91, eval_coverage: 0.85, metric_alignment: 1.0}
    judge_model         text NOT NULL,
    passed_threshold    boolean NOT NULL,                 -- coverage_score >= configured threshold
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX meta_evals_workflow_idx ON meta_evals(workflow_id);

-- =============================================================================
-- learnings (agent's append-only memory; mirrors auto-harness's learnings.md)
-- =============================================================================

CREATE TABLE learnings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    iteration_id    uuid REFERENCES iterations(id),
    kind            text NOT NULL CHECK (kind IN ('hypothesis', 'observation', 'request-to-human', 'failure-note')),
    content         text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX learnings_iteration_idx ON learnings(iteration_id);
CREATE INDEX learnings_created_idx ON learnings(created_at);

-- =============================================================================
-- Convenience views
-- =============================================================================

CREATE VIEW pending_proposals AS
    SELECT p.*, i.workflow_id, i.iteration_index
    FROM proposals p
    JOIN iterations i ON p.iteration_id = i.id
    WHERE p.state IN ('pending', 'gate-passed')
    ORDER BY p.created_at ASC;

CREATE VIEW lift_series AS
    SELECT
        i.workflow_id,
        i.iteration_index,
        i.best_ever_score_after AS score,
        i.ended_at AS ts,
        i.state,
        i.deployment_id,
        d.config_tag,
        d.model_id
    FROM iterations i
    LEFT JOIN skill_deployments d ON i.deployment_id = d.id
    WHERE i.state IN ('gate-pass', 'gate-blocked-regression', 'gate-blocked-no-improvement')
    ORDER BY i.workflow_id, i.iteration_index;
