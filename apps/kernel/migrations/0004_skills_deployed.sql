-- 0004: Track the currently-deployed skill version separate from HEAD.
--
-- After 0003: `head_version_id` = last gate-passed version (validated /
-- safe to read in the agent loop). That's the loop's "best known good",
-- not the version the customer is actually running in production. The
-- approval state machine separates the two trust seams:
--
--   gate-passed           — gate said it's safe to consider.
--   approved-awaiting-deploy — operator approved; ready to ship.
--   deployed              — operator hit Deploy; this version is live.
--   rolled-back           — operator hit Rollback on a previously-deployed
--                           proposal; the prior deployed version is live again.
--
-- `deployed_version_id` is the live pointer. NULL = nothing deployed yet
-- (skill exists with gate-passed versions but the operator hasn't shipped
-- one). Advanced only by `approvals.deploy.deploy_proposal` /
-- `rollback_proposal`. No backfill — the column starts NULL for every
-- existing skill until the operator deploys.
--
-- ON DELETE SET NULL: mirrors 0003's pattern for head_version_id and
-- latest_proposed_version_id. The reset script deletes from skill_versions;
-- without SET NULL, any skill with a deployed version would raise a FK
-- violation during --reset runs.
ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS deployed_version_id uuid;

ALTER TABLE skills DROP CONSTRAINT IF EXISTS skills_deployed_fk;
ALTER TABLE skills
    ADD CONSTRAINT skills_deployed_fk
    FOREIGN KEY (deployed_version_id) REFERENCES skill_versions(id)
    ON DELETE SET NULL;

-- Enforce the single-deployed invariant at DB level: at most one proposal
-- per skill in 'deployed' state. The service layer checks this inline, but
-- without a DB constraint two concurrent deploys can both pass before either
-- commits. The partial index makes the constraint atomic.
CREATE UNIQUE INDEX IF NOT EXISTS proposals_one_deployed_per_skill
    ON proposals (skill_id) WHERE state = 'deployed';

-- Supporting indexes for the new query patterns introduced by this feature.
-- proposals(skill_id) is queried in: single-deployed invariant check,
-- skill-detail deployable/deployed lookups. A composite (skill_id, state)
-- index covers both without needing a separate skill_id-only index.
CREATE INDEX IF NOT EXISTS proposals_skill_state_idx
    ON proposals (skill_id, state);

-- audit_entries(kind, related_id) is queried in the rollback lineage lookup.
-- The existing single-column indexes on kind and related_id separately cannot
-- serve this multi-predicate query efficiently as the audit log grows.
CREATE INDEX IF NOT EXISTS audit_entries_kind_related_idx
    ON audit_entries (kind, related_id);
