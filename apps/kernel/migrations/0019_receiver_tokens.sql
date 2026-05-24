-- 0019_receiver_tokens.sql — bearer-token auth for the OTLP ingest receiver.
--
-- The OTLP receiver shipped without authentication. Its docstring called
-- this out explicitly ("assumes a trusted network path; bearer-token auth
-- lands with the multi-tenant retrofit"). Time to close the gap: an
-- external collector pointed at `POST /api/otel/v1/traces` must present
-- a token that proves the operator minted it, otherwise the receiver
-- accepts traffic from anyone who can reach the host.
--
-- DESIGN
-- ------
-- Tokens are random 32-byte secrets, format `ownevo_rt_<base64url>`.
-- The prefix is non-secret (helps operators grep logs); the random
-- suffix is the secret. Server stores SHA-256(suffix) as hex — there
-- is no path to recover plaintext, so a DB dump is not a credential
-- compromise.
--
-- `workflow_id` is nullable:
--   * NOT NULL → token is bound to a specific workflow; every batch
--     authenticated by this token lands with `traces.workflow_id = $X`.
--     The collector cannot accidentally cross-write to another workflow.
--   * NULL → token is workflow-agnostic; the request must carry a
--     `?workflow_id=` query parameter to bind the batch. This shape is
--     for multi-workflow collectors (e.g. one langsmith-collector-proxy
--     covering several agents) where one token authenticates many
--     workflows.
--
-- `revoked_at` is the soft-delete column. Verification rejects any
-- token where `revoked_at IS NOT NULL`. We don't delete revoked rows
-- because the audit trail ("this token was active 2026-05-01 → 2026-06-15")
-- is load-bearing for incident response.
--
-- `last_used_at` is updated best-effort on every successful verify —
-- the operator CLI can spot dormant tokens at rotation time. It is
-- not a security control; missing updates under load are acceptable.
--
-- WHY NO workspace_id
-- -------------------
-- The kernel is single-tenant for MVP per CLAUDE.md — no `workspaces`
-- table exists yet. When multi-tenant retrofit lands, the migration
-- that adds `workspace_id` everywhere also adds it here as a NOT NULL
-- column populated from the bound workflow's workspace. Until then,
-- the implicit shared workspace + the workflow_id binding cover the
-- scoping job a workspace_id would do.
--
-- ONLINE-DDL NOTES
-- ----------------
-- Pure CREATE TABLE — no existing rows, no online-DDL trickery needed.
-- The companion `0019b_*_online.sql` is not required for this migration.

CREATE TABLE IF NOT EXISTS receiver_tokens (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash    TEXT         NOT NULL UNIQUE,
    workflow_id   TEXT         NULL REFERENCES workflows(id) ON DELETE CASCADE,
    label         TEXT         NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ  NULL,
    revoked_at    TIMESTAMPTZ  NULL
);

-- Verification reads on token_hash. UNIQUE already creates an index;
-- declaring the partial index makes the "active tokens only" lookup
-- path explicit and lets us drop the predicate from query plans.
CREATE INDEX IF NOT EXISTS receiver_tokens_active_idx
    ON receiver_tokens (token_hash)
    WHERE revoked_at IS NULL;

-- Operator CLI lists tokens by workflow to support per-workflow
-- rotation. Index on workflow_id keeps that scan cheap as the table
-- grows.
CREATE INDEX IF NOT EXISTS receiver_tokens_workflow_idx
    ON receiver_tokens (workflow_id)
    WHERE workflow_id IS NOT NULL;
