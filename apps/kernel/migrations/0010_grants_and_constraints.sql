-- 0010_grants_and_constraints.sql
--
-- Two hardening items:
--
-- 1. WORM role-level grants (layer 2 of the append-only guarantee).
--    The trigger-based WORM in 0001_substrate.sql is layer 1. This
--    migration adds the role-level layer so that even a superuser
--    shell cannot silently UPDATE/DELETE audit rows without first
--    regranting privileges.
--
--    BEFORE RUNNING: replace <app_role> with the actual Postgres role
--    used by the kernel (e.g. the Fly.io managed-postgres user from
--    your OWNEVO_DATABASE_URL connection string).
--
--    If using Fly.io managed Postgres, find the role with:
--      fly pg connect -a ownevo-pg -c "\du"
--
-- REVOKE UPDATE, DELETE ON audit_entries FROM <app_role>;
--
-- 2. Sentinel guard on workflows.id.
--    GET /api/skills?workflow_id=_unscoped uses '_unscoped' as a magic
--    sentinel meaning "skills with no workflow". This constraint prevents
--    a real workflow from colliding with the sentinel.

ALTER TABLE workflows
    ADD CONSTRAINT workflows_id_not_sentinel
    CHECK (id <> '_unscoped');
