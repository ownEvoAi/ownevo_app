-- 0044_rename_primitives_to_views.sql — rename the render-"primitive" concept to "view".
--
-- The workflow render-layer concept was named inconsistently: the
-- operator-facing UI said "view" while the code, routes, and spec said
-- "primitive". The codebase has been unified on "view"; this migration
-- brings the *stored* data in line with the renamed code so nothing breaks
-- on read.
--
-- Why this is mandatory, not cosmetic: the kernel re-parses stored
-- `workflows.spec` JSONB through `WorkflowSpec.model_validate` (e.g.
-- iteration_runner, eval_runner/try_runner, the workflow detail route), and
-- the spec models are `extra="forbid"`. With the field renamed
-- `UITab.primitives` -> `UITab.views`, any stored spec still keyed
-- `primitives` would fail validation (unknown field + missing required
-- field). So the stored JSONB must be rewritten key-for-key in lockstep.
--
-- Three stored surfaces, rewritten here:
--   1. proposal_kind enum value 'ui-primitive' -> 'ui-view' (RENAME VALUE is
--      transaction-safe and updates existing rows in place — no row rewrite).
--   2. workflows.spec : ui.tabs[].primitives -> ui.tabs[].views, and bump the
--      spec's schema_version to "1.5" (the version that carries this rename).
--   3. proposals.proposed_payload for ui-view proposals : {"primitives":[...]}
--      -> {"views":[...]}.
--
-- Deliberately NOT touched: audit_entries. The audit log is append-only (WORM:
-- UPDATE/DELETE revoked) and is a historical record of what happened — entries
-- that recorded kind "ui-primitive" / primitive_types stay as written.
--
-- RLS note: workflows and proposals have FORCE ROW LEVEL SECURITY, so the
-- UPDATEs below would be filtered to the session workspace for a non-bypass
-- role. Migrations run on the admin/superuser connection (the same one
-- 0001 needs for CREATE EXTENSION), which bypasses RLS, so these rewrites
-- apply across every workspace as intended.

-- 1. Enum value rename — in-place, no row rewrite.
ALTER TYPE proposal_kind RENAME VALUE 'ui-primitive' TO 'ui-view';

-- 2. workflows.spec : rename the per-tab key and bump schema_version.
UPDATE workflows w
SET spec = jsonb_set(
    jsonb_set(
        w.spec,
        '{ui,tabs}',
        (
            SELECT jsonb_agg(
                CASE
                    WHEN tab ? 'primitives'
                    THEN (tab - 'primitives')
                         || jsonb_build_object('views', tab -> 'primitives')
                    ELSE tab
                END
                ORDER BY ord
            )
            FROM jsonb_array_elements(w.spec -> 'ui' -> 'tabs')
                 WITH ORDINALITY AS t(tab, ord)
        )
    ),
    '{schema_version}',
    '"1.5"'::jsonb
)
WHERE w.spec ? 'ui'
  AND (w.spec -> 'ui') ? 'tabs'
  AND jsonb_typeof(w.spec -> 'ui' -> 'tabs') = 'array'
  AND EXISTS (
      SELECT 1
      FROM jsonb_array_elements(w.spec -> 'ui' -> 'tabs') AS e(tab)
      WHERE e.tab ? 'primitives'
  );

-- 3. proposals.proposed_payload : top-level key rename for view proposals.
UPDATE proposals
SET proposed_payload =
        (proposed_payload - 'primitives')
        || jsonb_build_object('views', proposed_payload -> 'primitives')
WHERE kind = 'ui-view'
  AND proposed_payload ? 'primitives';
