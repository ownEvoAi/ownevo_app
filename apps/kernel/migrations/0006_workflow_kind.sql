-- 0006_workflow_kind.sql
--
-- Tag rows in `workflows` as benchmark vs production so the web UI
-- can partition them into separate sidebar sections. Until multi-
-- tenant lands (post-customer-#2, per CLAUDE.md D4), benchmark and
-- customer workflows share one workspace; the kind column is the
-- cleanest way to keep them visually separate without inventing a
-- workspace_id retrofit ahead of schedule.
--
-- NULL is treated as production (default). Existing benchmark-only
-- rows seeded by `make m5-baseline` / tau-bench scripts are
-- back-filled by name pattern so the next page load shows them in
-- the right bucket; future benchmarks should set kind='benchmark'
-- explicitly at insert time.

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS kind text;

-- Back-fill: pre-existing benchmark workflows (M5 forecasting +
-- tau-bench-derived) carry deterministic ids. Anything else stays
-- NULL = production.
UPDATE workflows
SET kind = 'benchmark'
WHERE kind IS NULL
  AND (
       id LIKE 'm5-%'
    OR id LIKE 'tau-%'
    OR id LIKE 'tau2-%'
    OR id LIKE 'tau3-%'
    OR id LIKE 'taubench-%'
  );

CREATE INDEX IF NOT EXISTS workflows_kind_idx ON workflows(kind);
