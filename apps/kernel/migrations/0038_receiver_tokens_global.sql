-- 0038_receiver_tokens_global.sql — receiver_tokens leaves the workspace-RLS set.
--
-- WHY
-- ---
-- `receiver_tokens` is an auth-gateway table: the OTLP ingest route receives a
-- bearer token and looks it up to decide which workspace the request operates
-- in. By definition the lookup runs *before* any workspace is bound -- there is
-- no `app.workspace_id` GUC yet, the token IS what tells us which workspace to
-- bind. While the table was in the workspace-RLS set (migration 0034) an
-- unbound lookup returned zero rows, so the route could not authenticate any
-- receiver token in a non-dev environment.
--
-- The fix is to take `receiver_tokens` out of the workspace-RLS set, alongside
-- the other auth-gateway tables that already sit outside RLS: `workspaces`,
-- `workspace_members`, `users`, `user_identities`, and the global session/auth
-- substrate from migration 0035. The `workspace_id` column stays on the table
-- as metadata ("this token operates in workspace X"), so the route can read it
-- after the lookup and bind the connection accordingly.
--
-- INSERT-SIDE FAIL-CLOSED
-- -----------------------
-- The DEFAULT installed in migration 0034 (`current_setting('app.workspace_id',
-- true)`) stays: an unbound conn that INSERTs into receiver_tokens without an
-- explicit workspace_id will resolve the GUC to NULL and the NOT NULL column
-- rejects the write. Token-minting scripts and the admin UI already bind a
-- workspace, so the default keeps the fail-closed behaviour on writes without
-- requiring a code change.
--
-- ONLINE-DDL NOTES
-- ----------------
-- DROP POLICY + DISABLE/NO FORCE are metadata-only operations against the
-- table; no row rewrite, no exclusive lock beyond the brief catalogue update.

DROP POLICY IF EXISTS receiver_tokens_workspace_isolation ON receiver_tokens;

ALTER TABLE receiver_tokens NO FORCE ROW LEVEL SECURITY;
ALTER TABLE receiver_tokens DISABLE ROW LEVEL SECURITY;
