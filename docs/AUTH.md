# Authentication & Workspace Resolution

Status: **complete** — all four rollout steps have landed. The full path
is live: schema (step 1), kernel assertion verify + membership gate (step 2),
web-edge Auth.js wiring (step 3), and workspace provisioning UI +
active-workspace switcher (step 4). This document fixes the
decisions the authentication layer is built on. It exists because the
multi-tenant substrate is live — every workspace-scoped table enforces
row-level security against the `app.workspace_id` session GUC — and the
per-request resolver now derives the workspace from the authenticated
principal rather than the hard-coded `default`.

## Goals

- Authenticate end users for the eventual paid cloud deployment, with
  **Google (OIDC)** as the first provider and room for more (email,
  Microsoft, GitHub, enterprise SSO) without re-architecting.
- Resolve each request to a workspace from the authenticated principal, so
  the existing row-level security actually scopes per tenant.
- Support **multiple workspaces per user** with a per-workspace role, so a
  later role-based approval feature reads membership rather than inventing
  its own.
- Keep **local development and tests free of any real OAuth round-trip** —
  `make api` / `make web-dev` and the pytest suite must work with no Google
  credentials and no browser.

## Non-goals (for the first cut)

- Org/billing administration UI, SCIM provisioning, SAML.
- Fine-grained per-resource permissions beyond a workspace-level role.
- Email/password account recovery flows (deferred until a provider that
  needs them is added).
- Service-to-service auth for the trace-ingest receiver — that already has
  its own bearer-token scheme and is out of scope here.

## Architecture

Authentication lives at the **web edge** (the Next.js app), not in the
Python kernel. The kernel stays a backend service that is never exposed
directly to end-user browsers.

```
  browser ──login──▶ Next.js (Auth.js)            kernel (FastAPI)
                       │  Google OIDC / dev          │
                       │  provider                   │
                       │  ── session cookie ──▶ user │
                       │                             │
                       └── REST call with a ─────────▶ verify assertion
                           signed identity assertion   → resolve workspace
                           (user_id, workspace_id)     → bind app.workspace_id
                                                        → RLS scopes queries
```

Rationale:

- Auth.js is provider-pluggable; "Google plus other ways later" is a
  configuration change, not new code. Hand-rolling OIDC in Python would put
  the login surface in the wrong tier and duplicate a solved problem.
- The kernel already has a stripped-down HMAC-signed-token primitive
  (`apps/kernel/src/ownevo_kernel/api/_demo_identity.py` —
  `base64url(payload).hmac_sha256(payload)`, stdlib only). The
  web→kernel identity assertion reuses that exact primitive rather than
  introducing a JWT library.

### The web→kernel identity assertion

After Auth.js establishes a session, the web app calls the kernel with a
short-lived signed assertion carrying the authenticated principal and the
active workspace:

```
payload = {"u": <user_id>, "w": <active_workspace_id>, "e": <exp>}
token   = base64url(payload) + "." + hmac_sha256(payload, SHARED_KEY)
```

- Sent as `Authorization: Bearer <token>` on every kernel request the web
  app makes on behalf of a user.
- Signed with a shared secret (`OWNEVO_INTERNAL_AUTH_KEY`) known only to the
  web app and the kernel. The kernel verifies the signature and expiry, then
  trusts `u` and `w`. The kernel never sees Google tokens.
- Short TTL (minutes). The web app re-mints per request (or caches briefly);
  switching the active workspace re-mints with a new `w`.

The kernel verifies membership defensively even though the web app already
checked it: a valid assertion for workspace `w` is only honored if user `u`
is actually a member of `w` and `w` is not soft-deleted. This keeps the
trust boundary auditable rather than "the web app said so."

### Kernel changes

Request resolution in `apps/kernel/src/ownevo_kernel/api/deps.py` no longer
returns the `default` constant. The implemented flow:

1. `get_principal` reads the bearer assertion from the request and verifies
   signature + expiry (via the shared `_signing` / `_internal_auth` helpers,
   factored out of the `_demo_identity` primitive). No database access here.
2. `get_workspace_id` returns the principal's `workspace_id`.
3. `get_conn` — the single path to workspace-scoped data — confirms
   `(user_id, workspace_id)` membership against the `workspace_members`
   table and that the workspace is live, *before* binding.
4. `set_workspace` binds `app.workspace_id` so row-level security scopes the
   request.

Failure modes: missing/invalid assertion → 401; valid principal but not a
member of the requested workspace → 403; soft-deleted workspace → 403. These
are distinct from the existing `set_workspace` refusals (missing /
soft-deleted workspace), which stay as the last-line database guard.

## Data model

Three new tables (migration `0035_auth_users.sql`):

```sql
-- A person who can sign in. One row per identity, independent of provider.
CREATE TABLE users (
    id            text PRIMARY KEY,          -- internal id (not the provider's)
    email         text NOT NULL,
    display_name  text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (email)
);

-- Which provider(s) a user authenticates with. A user may link more than
-- one (Google today, email or Microsoft later) without duplicating rows.
CREATE TABLE user_identities (
    user_id        text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider       text NOT NULL,            -- 'google' | 'dev' | ...
    provider_sub   text NOT NULL,            -- the provider's stable subject id
    PRIMARY KEY (provider, provider_sub)
);

-- Membership: which users belong to which workspace, and their role.
-- The role column also feeds the later role-based approval feature.
CREATE TABLE workspace_members (
    workspace_id   text NOT NULL REFERENCES workspaces(id),
    user_id        text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role           text NOT NULL DEFAULT 'member',  -- 'owner' | 'admin' | 'member'
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);
```

`users` and `user_identities` are **global**, not workspace-scoped — a
person exists across workspaces. They are therefore *not* under row-level
security; they sit outside the tenant boundary, like the workspace
provisioning surface. `workspace_members` is the join that maps the two.

## Local development & test auth

This is a hard requirement: nothing about local iteration may depend on
Google.

- **Web (`OWNEVO_DEV_AUTH=true`):** Auth.js registers a dev/credentials
  provider that logs in as a seeded user (pick from a short list or type an
  email) with no external round-trip. The Google provider is only registered
  when real client credentials are present in the environment, so production
  gets Google and local gets the dev provider from the same code.
- **Kernel dev fallback:** when `OWNEVO_DEV_AUTH=true` and no assertion is
  present, the kernel resolves to a seeded dev user and the `default`
  workspace. This preserves the current zero-config behaviour of
  `make api` for anyone poking the kernel directly with `curl`.
- **pytest:** the workspace/principal resolution is a FastAPI dependency, so
  tests either override it via `app.dependency_overrides` (already the
  pattern for the pool) or mint a dev-signed assertion inline. This mirrors
  how `db` / `rls_db` fixtures already call `set_workspace(conn,
  DEFAULT_WORKSPACE_ID)` — no browser, no OAuth, no network.

The dev provider and the kernel dev fallback are **refused when
`OWNEVO_DEV_AUTH` is not explicitly true**, so a misconfigured production
deployment fails closed (no anonymous default-workspace access) rather than
silently granting access.

### Boot-time guards

Three startup checks refuse known-bad combinations rather than letting them
surface as quiet runtime failures:

- **Dev-auth + signing key** — `OWNEVO_DEV_AUTH=true` alongside
  `OWNEVO_INTERNAL_AUTH_KEY` would let the dev fallback bypass workspace
  isolation in any deployment that uses the shared signing key. Both the
  kernel (`api/app.py` lifespan) and the web app (`instrumentation.ts`)
  refuse to start in this state.
- **Dev-auth + Google credentials** — `OWNEVO_DEV_AUTH=true` alongside
  `AUTH_GOOGLE_ID/SECRET` is also refused at web startup: the dev-auth
  path only registers the credentials provider, so any Google sign-in
  attempt would 500 immediately.
- **Production environment** — when the deployment identifies as
  production (`OWNEVO_ENV=production` for the kernel; `NODE_ENV=production`
  for the web app), startup refuses any of: dev-auth still on; the
  internal signing key missing; the kernel's credentials master key
  (`OWNEVO_CREDENTIALS_MASTER_KEY`) missing. A misconfigured prod boot
  crashes loudly with an actionable error instead of rejecting every
  authenticated request or crashing at the first integration write.

  **Deployment requirement:** `OWNEVO_ENV=production` must be set
  explicitly in the kernel's deployment environment (e.g. as a secret or
  env var in your hosting platform). The web app's production guard fires
  automatically because Next.js sets `NODE_ENV=production` in production
  builds; the kernel's guard is opt-in by this explicit marker. Without it
  the kernel's production-only checks do not run. See `.env.example` for
  the documented variable.

## How this unblocks the rest of the multi-tenant work

- **Per-request workspace resolution** is no longer blocked — the kernel
  resolver described above now maps a verified assertion to a workspace.
- **Workspace provisioning API** (create / list / soft-delete a workspace)
  plugs into the same outside-the-tenant-boundary surface as `users` /
  provisioning; creating a workspace also writes the creator's
  `workspace_members` row as `owner`.
- **Role-based approval** reads `workspace_members.role` directly instead of
  inventing a parallel permission store.

## Resolved decisions

1. **Session strategy → encrypted JWT cookie** for the web session, paired
   with the separate short-lived kernel assertion. No server-side session
   table for the first cut; the short assertion TTL bounds staleness. If
   server-side revocation becomes a requirement (e.g. force-logout on a role
   change), add a denylist keyed by session id rather than switching the
   whole model to database sessions.
2. **Assertion transport → `Authorization: Bearer`.** Conventional, and keeps
   the kernel's auth check uniform with the existing receiver-token path
   rather than introducing a second custom header.
3. **`users` / `user_identities` / `workspace_members` live in the same
   Postgres database** as the tenant data, as global tables outside row-level
   security. The global-vs-scoped boundary is already expressed by which
   tables have RLS, so a separate auth store would add operational surface for
   no isolation benefit.
4. **First login → an explicit "create or join a workspace" screen; no
   auto-created personal workspace.** A paid multi-tenant product should not
   let every Google sign-in silently provision a billable tenant. A new user
   with no membership lands on the create/join screen; creating a workspace
   writes their `workspace_members` row as `owner`. Joining an existing
   workspace is by invitation (the invite flow is a later slice; the schema
   supports it today).

## Rollout

1. **(done)** Land the schema (`users`, `user_identities`,
   `workspace_members`) as a migration; backfill a single seeded dev user as a
   member of the `default` workspace so existing local flows keep working.
2. **(done)** Add the kernel-side assertion verify + membership check + dev
   fallback; flip `get_workspace_id` to use it. With `OWNEVO_DEV_AUTH=true`
   this is a no-op for current single-tenant behaviour.
3. **(done)** Wire Auth.js into the web app with the dev provider first, then
   the Google provider behind configured credentials. The web app upserts the
   principal through the kernel's internal auth-sync endpoint on first sign-in
   and mints a per-request bearer assertion for every kernel call.
4. **(done)** Add the workspace provisioning surface and the active-workspace
   switcher in the web app. New users with no membership land on
   `/setup/new-workspace`; creating a workspace calls `POST /api/internal/workspaces`
   (service-token authenticated) and updates the session JWT in place via
   `unstable_update`. Users with multiple workspaces can switch via a form-based
   switcher in the sidebar. A second real tenant can now exist end to end.

## Known limitations and deferred items

- **JWT membership staleness on externally-triggered changes.** If an admin
  removes a user from a workspace from outside their session (e.g. via another
  account), the affected user's JWT continues to list the membership until they
  re-sign-in. Self-triggered changes (workspace create / switch from the UI) are
  reflected immediately via `unstable_update`. Fixing externally-triggered
  staleness would require a server-side session store or a short-TTL JWT with a
  per-session denylist — deferred until revocation becomes a hard requirement.
- **`WorkspaceIdDep` bypass in `try_workflow_one_case`.** Pre-existing, read-only,
  bounded to a 120 s window. Tracked separately; not part of the auth rollout.
- **Sign-in redirect and route-gating polish.** The middleware now redirects
  unauthenticated requests to `/api/auth/signin`. Error pages (e.g. Google
  `email_verified` failure, expired session) show Auth.js defaults; custom
  error UI is deferred.
- **Browser-direct kernel calls.** The design-flow conflict check and any future
  client-side kernel calls currently lean on `OWNEVO_DEV_AUTH`; before a
  non-dev deploy they should route through Next.js server actions so the
  assertion is minted server-side.
- **Workspace invite flow.** Joining an existing workspace requires an invitation.
  The schema supports it (`workspace_members`); the invite creation and redemption
  UI is a later slice.
