-- 0022_langsmith_integration.sql — wiring for shipping approved fixes
-- back to a customer's LangSmith workspace as a new prompt version.
--
-- Three independent pieces, all on small tables (workflows / skills are
-- low-cardinality in the single-tenant MVP), so the online-DDL split
-- used for the high-volume traces table (0018/0018b) isn't needed —
-- the CHECK is validated immediately.
--
-- 1. workflows.origin
--    Tags where a workflow came from. NULL = greenfield / kernel-authored
--    (the existing default); 'langsmith' / 'copilot_studio' = imported
--    from that vendor. The "Ship fix to LangSmith" approval action only
--    appears for origin='langsmith' workflows.
--
-- 2. skills.langsmith_prompt_id
--    The opaque LangSmith Prompt Hub identifier this skill maps to. Set
--    automatically when a workflow is imported from LangSmith (read off
--    the ingested span attributes) or manually via the binding picker.
--    The push-back step needs it to know which LangSmith prompt to push
--    a new version to. Nullable: greenfield skills have no LangSmith
--    counterpart until one is bound.
--
-- 3. integration_credentials
--    Per-provider API credentials, encrypted at rest. Single-tenant
--    singleton: one row per provider ('langsmith', and future
--    'copilot_studio' etc.). `ciphertext` is the API key sealed with the
--    app's credentials master key (see secrets/encrypted_field.py); the
--    plaintext never touches the database. No `workspaces` table exists
--    in the MVP — when the multi-tenant retrofit lands it widens the PK
--    to (workspace_id, provider). `validation_status` records the result
--    of the last "test connection" so the Settings UI can show whether
--    the stored key still works without re-hitting the vendor on every
--    page load.

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS origin text;

ALTER TABLE workflows
    DROP CONSTRAINT IF EXISTS workflows_origin_chk;

ALTER TABLE workflows
    ADD CONSTRAINT workflows_origin_chk
        CHECK (origin IS NULL OR origin IN ('langsmith', 'copilot_studio'));

ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS langsmith_prompt_id text;

CREATE TABLE IF NOT EXISTS integration_credentials (
    provider           text        PRIMARY KEY,
    ciphertext         text        NOT NULL,
    last_validated_at  timestamptz,
    validation_status  text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
