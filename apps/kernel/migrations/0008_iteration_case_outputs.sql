-- 0008_iteration_case_outputs.sql — per-case structured agent output
--
-- PLAN row 8.4.9 (Phase A of operator-shell parity). The iteration
-- runner today persists one bool per case (pass/fail vs expected_behavior)
-- inside trace events; the gate scores on that bool and that's enough
-- for the lift chart. The operator-shell mocks (s26-rk7p3/28-31) need
-- richer per-case output — recommended action, confidence, rationale,
-- alerts — which the TableView / AlertList primitives bind to.
--
-- This table stores the agent's structured output per case per iteration,
-- alongside the bool the gate already uses. Phase B (PLAN row 8.4.10)
-- wires TableView to read from here.
--
-- One row per (iteration, eval_case). ON DELETE CASCADE for both parents
-- so deleting a workflow / iteration / eval case cleans up automatically.

CREATE TABLE IF NOT EXISTS iteration_case_outputs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    iteration_id    uuid NOT NULL REFERENCES iterations(id) ON DELETE CASCADE,
    eval_case_id    uuid NOT NULL REFERENCES eval_cases(id) ON DELETE CASCADE,
    output_json     jsonb NOT NULL,            -- agent's structured output (submit_case_output tool args)
    passed          boolean NOT NULL,          -- mirrors the bool the gate scores on
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (iteration_id, eval_case_id)
);

CREATE INDEX IF NOT EXISTS iteration_case_outputs_iter_idx
    ON iteration_case_outputs(iteration_id);

CREATE INDEX IF NOT EXISTS iteration_case_outputs_case_idx
    ON iteration_case_outputs(eval_case_id);
