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
ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS deployed_version_id uuid;

ALTER TABLE skills DROP CONSTRAINT IF EXISTS skills_deployed_fk;
ALTER TABLE skills
    ADD CONSTRAINT skills_deployed_fk
    FOREIGN KEY (deployed_version_id) REFERENCES skill_versions(id);
