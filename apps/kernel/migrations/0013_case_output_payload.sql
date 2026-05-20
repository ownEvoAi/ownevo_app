-- 0013_case_output_payload.sql — domain-shaped agent output payload
--
-- iteration_case_outputs.output_json today carries the eval-pass/fail
-- frame: {case_id, predicted: bool, expected: bool, rationale}. Useful
-- for the gate, useless for an operator who wants to see the agent's
-- actual recommendation in domain-native shape (a 28-day forecast
-- curve, a clause-by-clause redline pair, a recommended-action table).
--
-- output_payload carries that domain-shaped artifact. The agent emits
-- it via the optional `output_payload` arg on predict_label; the
-- Operate-tab resolver reads it and dispatches to the workflow-
-- declared primitives (TimeSeriesChart / TableView / SideBySideView /
-- AlertList / KanbanBoard / DocumentReader). Nullable for backfill +
-- for models that don't yet emit a payload — they keep working,
-- Operate just stays empty for those rows.
--
-- Shape is intentionally freeform JSONB: the operate-tab resolver
-- introspects the spec's declared primitives and maps payload keys
-- accordingly. No DB-side schema constraint — fast iteration on shape
-- as new primitive renderers land.

ALTER TABLE iteration_case_outputs
    ADD COLUMN IF NOT EXISTS output_payload jsonb;
