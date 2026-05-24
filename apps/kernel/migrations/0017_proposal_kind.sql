-- 0017_proposal_kind.sql — non-skill artifact proposals.
--
-- Skill text was the only artifact a proposal could change before this
-- migration. Workflow description, success metric, simulation plan,
-- and operate-view UI primitives are now first-class edit targets
-- that flow through the same proposal → review → gate → approve loop.
--
-- The discriminator is `kind`. `skill` retains current semantics
-- (skill_id + parent_version_id + proposed_content all set). Other
-- kinds populate `proposed_payload jsonb` with the artifact-shaped
-- new value; their `skill_id` is null. A CHECK constraint pins the
-- shape so the gate / approval code can rely on it.
--
-- Backward compatible by design — existing rows default to `skill`
-- and the schema stays well-formed for the legacy insert path.

CREATE TYPE proposal_kind AS ENUM (
    'skill',
    'description',
    'metric',
    'sim',
    'ui-primitive'
);

ALTER TABLE proposals
    ADD COLUMN kind proposal_kind NOT NULL DEFAULT 'skill',
    ADD COLUMN proposed_payload jsonb;

-- skill_id is required only for kind='skill'. Existing rows are
-- 'skill' and already have a non-null skill_id, so dropping NOT NULL
-- is safe.
ALTER TABLE proposals ALTER COLUMN skill_id DROP NOT NULL;

ALTER TABLE proposals ADD CONSTRAINT proposals_skill_kind_requires_skill_id
    CHECK (kind != 'skill' OR skill_id IS NOT NULL);

-- Non-skill kinds must carry a payload (artifact-specific JSON). The
-- shape is validated at the app layer per kind, not here.
ALTER TABLE proposals ADD CONSTRAINT proposals_nonskill_kind_requires_payload
    CHECK (kind = 'skill' OR proposed_payload IS NOT NULL);

CREATE INDEX proposals_kind_idx ON proposals(kind);
