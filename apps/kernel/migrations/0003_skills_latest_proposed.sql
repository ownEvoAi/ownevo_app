-- 0003: Split skill HEAD semantics from "agent's last write" (TODO-31).
--
-- `skills.head_version_id` previously advanced on every register_skill
-- call (i.e. every agent write_skill). After a NO_IMPROVEMENT or
-- SANDBOX_ERROR cycle, HEAD pointed at the rejected proposal — anyone
-- restoring "the current best skill" via `skills.head_version_id` got
-- the failed version back.
--
-- New semantics:
--   head_version_id            — last gate-passed version (or v1 bootstrap
--                                if no gate-pass has happened yet).
--                                Advanced only by gate/persistence.py.
--   latest_proposed_version_id — most recent register_skill write,
--                                regardless of gate outcome. Used by the
--                                proposer for parent_version_id chaining
--                                so version lineage stays linear.
--
-- Backfill: every existing skill's latest_proposed gets set to the
-- current head_version_id (their previous semantics were the same row).

ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS latest_proposed_version_id uuid;

ALTER TABLE skills DROP CONSTRAINT IF EXISTS skills_latest_proposed_fk;
ALTER TABLE skills
    ADD CONSTRAINT skills_latest_proposed_fk
    FOREIGN KEY (latest_proposed_version_id) REFERENCES skill_versions(id);

UPDATE skills
SET latest_proposed_version_id = head_version_id
WHERE latest_proposed_version_id IS NULL
  AND head_version_id IS NOT NULL;
