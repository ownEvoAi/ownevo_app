-- 0029_mcp_servers.sql — register external MCP servers as agent data sources.
--
-- The improvement loop reaches into a customer's connected systems (Slack,
-- Google Workspace, Microsoft 365, or any MCP-exposed source) by consuming
-- MCP servers rather than building bespoke per-source connectors. Each row
-- here is one connected server: where to reach it, how to authenticate, and
-- the last connection-test result.
--
-- Auth split (secret vs. non-secret):
--   * auth_config (jsonb) holds the NON-secret parameters needed to mint or
--     refresh a token — token endpoint URL, client_id, OAuth scopes, tenant
--     id. Safe to read back to the admin UI.
--   * auth_secret_ciphertext holds the secret material (bearer token, OAuth
--     access+refresh tokens, service-principal client secret) as a single
--     JSON blob sealed with the app credentials master key
--     (see secrets/encrypted_field.py). The plaintext never touches the DB
--     and is never returned to the API surface. NULL when auth_kind='none'.
--
-- Single-tenant MVP: no workspace_id (consistent with every other table; see
-- 0001_substrate.sql §D4). The multi-tenant retrofit widens the design with a
-- workspace_id column + scoping the unique name constraint to (workspace_id,
-- name); nothing here fights that.

CREATE TABLE IF NOT EXISTS mcp_servers (
    id                      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    text        NOT NULL UNIQUE,
    provider                text        NOT NULL,
    endpoint_url            text        NOT NULL,
    transport               text        NOT NULL DEFAULT 'streamable_http'
        CHECK (transport IN ('streamable_http', 'sse')),
    auth_kind               text        NOT NULL
        CHECK (auth_kind IN ('none', 'bearer', 'oauth', 'service_principal')),
    auth_config             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    auth_secret_ciphertext  text,
    status                  text        NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'error')),
    last_validated_at       timestamptz,
    validation_status       text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);
