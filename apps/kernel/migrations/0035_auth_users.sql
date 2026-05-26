-- 0035_auth_users.sql
--
-- Authentication substrate: users, their linked sign-in identities, and
-- workspace membership. See docs/AUTH.md for the full design.
--
-- These three tables are GLOBAL, not workspace-scoped, and are deliberately
-- NOT placed under row-level security:
--
--   * A person exists across workspaces, so `users` / `user_identities` have
--     no single workspace dimension.
--   * `workspace_members` is the table the per-request resolver reads to map
--     an authenticated principal to a workspace. It is queried BEFORE any
--     workspace is bound to the connection, so scoping it by the very GUC it
--     helps establish would be circular. Authorization is enforced in the
--     resolver (is this user a member of this workspace?), not by RLS.
--
-- This migration only lands the schema + a seeded dev user. The kernel-side
-- resolver change and the web sign-in flow are separate steps.

-- A person who can sign in. One row per human identity, independent of which
-- provider(s) they authenticate with. `id` is an internal id we mint, never
-- the provider's subject.
CREATE TABLE IF NOT EXISTS users (
    id            text PRIMARY KEY,
    email         text NOT NULL,
    display_name  text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (email)
);

-- Which provider(s) a user authenticates with. A user may link more than one
-- (Google today; email / Microsoft / GitHub later) without duplicating the
-- `users` row. `provider_sub` is the provider's stable subject identifier.
CREATE TABLE IF NOT EXISTS user_identities (
    user_id        text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider       text NOT NULL,
    provider_sub   text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, provider_sub)
);

CREATE INDEX IF NOT EXISTS user_identities_user_idx
    ON user_identities(user_id);

-- Membership: which users belong to which workspace and their role. The role
-- column also feeds the later role-based approval feature, which reads
-- membership rather than introducing a parallel permission store.
CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id   text NOT NULL REFERENCES workspaces(id),
    user_id        text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role           text NOT NULL DEFAULT 'member'
        CHECK (role IN ('owner', 'admin', 'member')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS workspace_members_user_idx
    ON workspace_members(user_id);

-- Seed a single dev user and make it an owner of the 'default' workspace
-- (seeded in 0033). This keeps local development and tests working with the
-- dev-auth fallback: when OWNEVO_DEV_AUTH=true and no signed assertion is
-- present, the kernel resolves to this user + the default workspace, so
-- `make api` and the test suite need no real sign-in. It is inert in
-- production, where the dev-auth fallback is refused.
INSERT INTO users (id, email, display_name)
    VALUES ('dev-user', 'dev@ownevo.local', 'Local Dev User')
    ON CONFLICT (id) DO NOTHING;

INSERT INTO user_identities (user_id, provider, provider_sub)
    VALUES ('dev-user', 'dev', 'dev-user')
    ON CONFLICT (provider, provider_sub) DO NOTHING;

INSERT INTO workspace_members (workspace_id, user_id, role)
    VALUES ('default', 'dev-user', 'owner')
    ON CONFLICT (workspace_id, user_id) DO NOTHING;
