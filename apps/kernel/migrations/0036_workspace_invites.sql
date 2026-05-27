-- 0036_workspace_invites.sql
--
-- Workspace invite tokens — link-based, signed by OWNEVO_INTERNAL_AUTH_KEY.
-- An owner/admin of a workspace mints an invite for an email + role; the
-- invitee redeems the token after signing in, which inserts their row in
-- workspace_members. Email delivery is out of scope: the invite URL is
-- returned to the admin who sends it via their own channel.
--
-- The token carries only {kind: "inv", invite_id, exp} signed with HMAC.
-- Lookup-by-id makes revocation and "pending invite" listings cheap; the
-- token stays short.
--
-- This table sits OUTSIDE row-level security:
--
--   * Redemption happens before the redeemer is bound to the workspace
--     (they're not a member yet). Scoping the invite row to the redeemer's
--     workspace GUC would be circular.
--   * Mint and revoke ARE workspace-scoped at the API layer (the kernel
--     endpoints check the caller's membership/role before touching this
--     table). The table itself stays non-RLS so the redemption read works.
--
-- 'owner' is intentionally NOT a valid invite role: ownership belongs to the
-- workspace creator and can only be transferred by promoting an existing
-- member (out of scope for this migration). Roles match workspace_members.

CREATE TABLE IF NOT EXISTS workspace_invites (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    invited_email   text NOT NULL,
    role            text NOT NULL CHECK (role IN ('admin', 'member')),
    invited_by      text NOT NULL REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    redeemed_at     timestamptz,
    redeemed_by     text REFERENCES users(id),
    revoked_at      timestamptz,
    revoked_by      text REFERENCES users(id),
    -- An invite is in exactly one of three terminal states: pending,
    -- redeemed, revoked. Once redeemed it cannot be revoked, and vice versa.
    -- Enforce paired (timestamp, actor) columns so a partial write cannot
    -- leave the row internally inconsistent.
    CHECK ((redeemed_at IS NULL) = (redeemed_by IS NULL)),
    CHECK ((revoked_at  IS NULL) = (revoked_by  IS NULL)),
    CHECK (NOT (redeemed_at IS NOT NULL AND revoked_at IS NOT NULL))
);

-- Admin UI: list pending invites for a workspace.
CREATE INDEX IF NOT EXISTS workspace_invites_workspace_idx
    ON workspace_invites(workspace_id);

-- Admin UI: "is this email already invited to this workspace?" pre-check.
CREATE INDEX IF NOT EXISTS workspace_invites_workspace_email_idx
    ON workspace_invites(workspace_id, lower(invited_email));
