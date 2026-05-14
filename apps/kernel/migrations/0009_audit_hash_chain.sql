-- TODO-3: Audit hash chain columns.
--
-- Adds `parent_hash` + `entry_hash` (SHA-256 hex, 64 chars) to
-- `audit_entries`. Existing rows keep NULL hashes — they are the
-- "pre-hash epoch" and are skipped by the chain-verification logic.
-- The chain begins from the first entry written after this migration.
--
-- No backfill is run here. A backfill helper script can be added when
-- the first regulated-industry buyer requires it; until then the verify
-- endpoint reports `hash_chain_entries = 0` on a freshly-migrated DB
-- and `hash_chain_valid = true` (empty chain is valid by definition).

ALTER TABLE audit_entries
    ADD COLUMN parent_hash text,
    ADD COLUMN entry_hash  text;

-- Index for chain-verification traversal (newest entry → its hash →
-- next entry's parent_hash lookup).
CREATE INDEX audit_entries_entry_hash_idx ON audit_entries (entry_hash)
    WHERE entry_hash IS NOT NULL;
