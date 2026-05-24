-- 0018_mock_sim_tier.sql — per-workflow simulation tier + mock config
--
-- Track 9.0.2. Adds two operational columns to `workflows`:
--
--   * `sim_tier` — discriminator picking how an iteration's agent runs
--     against the eval case set:
--       'real'   (default): existing behaviour, LLM-backed agent_solver
--       'mock'  : MockAgentSolver — deterministic scripted predictions
--                  driven by `mock_sim_config.accuracy_per_iteration[]`.
--                  Zero LLM spend, sub-second per iteration. Used for
--                  fast inner-loop dev, CI integration tests, and
--                  control-logic experiments where LLM cost or
--                  determinism would otherwise dominate.
--       'replay' : reserved for Track 9.0.3 — replay against captured
--                  production traces. Schema lands now so the CHECK
--                  constraint doesn't fight a future migration.
--
--   * `mock_sim_config` — JSONB carrying the mock scripting payload.
--     Schema for sim_tier='mock' (NL-gen workflows):
--       {
--         "accuracy_per_iteration": [0.50, 0.65, 0.77, 0.80],
--         "default_accuracy": 0.80,
--         "seed": 42
--       }
--     `accuracy_per_iteration[N]` applies to iteration N (0-indexed);
--     `default_accuracy` applies when N >= len(curve); `seed` makes the
--     per-case correct/incorrect assignment reproducible.
--
--     For sim_tier='mock' on benchmark workflows (M5/τ³ — go through
--     SandboxRuntime), an additional `sandbox_script` field carries
--     canned SandboxResult plans; see MockSimSandbox docs.
--
--     NULL when sim_tier != 'mock'.
--
-- Sim-tier placement: on the `workflows` row, not on `workflows.spec`
-- (the frozen NL-gen WorkflowSpec). Same precedent as `agent_model_id`
-- in 0014 — operational config lives on the row, NL-gen artifacts in
-- the JSONB. Avoids a WorkflowSpec schema bump and keeps the per-spec
-- generation pipeline untouched.
--
-- Backward compatible: existing rows get sim_tier='real' on default
-- backfill and behave identically to today. The iteration runner only
-- branches when sim_tier='mock'; everything else short-circuits to the
-- existing path.

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS sim_tier text NOT NULL DEFAULT 'real';

ALTER TABLE workflows
    ADD CONSTRAINT workflows_sim_tier_check
        CHECK (sim_tier IN ('real', 'mock', 'replay'));

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS mock_sim_config jsonb DEFAULT NULL;

-- When sim_tier='mock', mock_sim_config must be non-NULL (a mock run
-- needs a script). Other tiers may have it NULL or not — the runner
-- ignores the column for them.
ALTER TABLE workflows
    ADD CONSTRAINT workflows_mock_sim_config_required
        CHECK (sim_tier <> 'mock' OR mock_sim_config IS NOT NULL);
