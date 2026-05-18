-- 0011_workflow_template_id.sql
--
-- Track which vertical template (if any) a workflow was created from.
--
-- The /workflows/new page offers three buyer-persona starters:
-- retail demand planning, credit risk recalibration, clinical trial site
-- selection — each prefilling the description textarea. We record the
-- chosen template id on the workflows row so analytics can answer
-- "what % of customer workflows started from a template".
--
-- NULL = workflow authored from a free-form description (no template).

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS created_from_template TEXT NULL;

-- Slug shape mirrors the kebab-id rule on workflow_id. Empty string is
-- rejected; the column stays NULL for free-form workflows.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'workflows_created_from_template_slug'
    ) THEN
        ALTER TABLE workflows
            ADD CONSTRAINT workflows_created_from_template_slug
            CHECK (
                created_from_template IS NULL
                OR created_from_template ~ '^[a-z0-9][a-z0-9-]*[a-z0-9]$'
            );
    END IF;
END;
$$;
