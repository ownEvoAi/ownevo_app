-- 0030_mcp_oauth.sql — OAuth authorization-code support for MCP connectors.
--
-- 17.0.1 (migration 0029) covered token *refresh* + client-credentials. To
-- connect Slack / Google Workspace / Microsoft 365 a workspace admin first
-- runs the interactive authorization-code grant: ownEvo redirects the admin to
-- the provider's consent screen, then exchanges the returned code for tokens
-- and registers an mcp_servers row. Two pieces of state back that flow.
--
-- 1. mcp_oauth_clients
--    The OAuth *app* registration the admin created with each provider:
--    client_id (non-secret) + client_secret (sealed at rest, same master key
--    as integration_credentials / mcp_servers). One row per provider in the
--    single-tenant MVP; the multi-tenant retrofit widens the PK to
--    (workspace_id, provider).
--
-- 2. mcp_oauth_states
--    A short-lived per-attempt nonce. Created when the admin clicks "Connect",
--    carried through the provider round-trip as the OAuth `state` parameter,
--    and consumed (deleted) when the callback returns — both as CSRF defence
--    and to recover the intended server name / scopes / endpoint the admin
--    chose before leaving for the consent screen.

CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
    provider                 text        PRIMARY KEY,
    client_id                text        NOT NULL,
    client_secret_ciphertext text        NOT NULL,
    -- Non-secret provider extras (e.g. Microsoft 365 `tenant`). Safe to read
    -- back to the admin UI.
    config                   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mcp_oauth_states (
    state         text        PRIMARY KEY,
    provider      text        NOT NULL,
    server_name   text        NOT NULL,
    scopes        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    endpoint_url  text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
