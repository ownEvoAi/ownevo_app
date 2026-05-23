-- Phase 1 of the live-demo unlock: token-quota accounting for the
-- design-agent + NL-gen routes, signed-invite revocation, and a soft
-- global budget gate flipped by the operator.
--
-- Schema is single-tenant-friendly: no workspace_id; the demo mode is
-- intentionally a single shared workspace until Phase 2 splits it.

CREATE TABLE IF NOT EXISTS demo_usage (
    identity_key TEXT NOT NULL,
    day DATE NOT NULL,
    input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT 'anonymous'
        CHECK (tier IN ('anonymous', 'elevated', 'unlimited')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (identity_key, day)
);

CREATE INDEX IF NOT EXISTS demo_usage_day_idx ON demo_usage (day);

CREATE TABLE IF NOT EXISTS demo_invite_revocations (
    jti TEXT PRIMARY KEY,
    label TEXT,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason TEXT
);

-- One row per day. `status` flips to 'exhausted' when the operator
-- decides the demo's daily LLM budget is spent (manual or cron). The
-- Anthropic console cap is the hard ceiling; this is the soft signal
-- that lets the kernel return a friendly 502 before the upstream
-- cap-breach error bubbles up.
CREATE TABLE IF NOT EXISTS demo_budget_state (
    day DATE PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'exhausted')),
    note TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
