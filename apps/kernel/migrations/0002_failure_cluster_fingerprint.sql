-- 0002: Dedup fingerprint for failure_clusters (B3 idempotency).
--
-- Adds a content fingerprint so cluster_m5_failures.py re-runs are
-- safe: the second INSERT ON CONFLICT finds the existing row and
-- skips it rather than creating a duplicate cluster.
--
-- Partial unique index (WHERE fingerprint IS NOT NULL) keeps existing
-- rows (fingerprint = NULL) from conflicting with each other.

ALTER TABLE failure_clusters
    ADD COLUMN IF NOT EXISTS fingerprint TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS failure_clusters_fingerprint_unique
    ON failure_clusters (fingerprint)
    WHERE fingerprint IS NOT NULL;
