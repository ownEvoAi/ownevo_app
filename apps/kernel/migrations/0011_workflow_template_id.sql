-- 0011_workflow_template_id.sql
--
-- Track which vertical template (if any) a workflow was created from.
--
-- The /workflows/new page (PLAN 8.5.1) offers three buyer-persona starters
-- — retail demand planning, credit risk recalibration, clinical trial site
-- selection — each prefilling the description textarea. We record the
-- chosen template id on the workflows row so the audit log + analytics
-- can answer "what % of customer workflows started from a template" and
-- so the Theme 1.1 design agent can later branch on it to surface the
-- template's `discovery_questions` for clarification.
--
-- NULL = workflow authored from a free-form description (no template).

ALTER TABLE workflows
    ADD COLUMN created_from_template TEXT NULL;

-- Slug shape mirrors the kebab-id rule on workflow_id. Empty string is
-- rejected; the column stays NULL for free-form workflows.
ALTER TABLE workflows
    ADD CONSTRAINT workflows_created_from_template_slug
    CHECK (
        created_from_template IS NULL
        OR created_from_template ~ '^[a-z0-9][a-z0-9-]*[a-z0-9]$'
    );
