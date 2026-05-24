-- 0019_replay_sim_tier.sql — replay-tier config + captured sandbox runs
--
-- Track 9.0.3. The `replay` value on workflows.sim_tier was already
-- allowed by migration 0018's CHECK constraint; this migration adds
-- the operational config + the storage layer needed to actually run
-- a replay iteration.
--
-- Per-layer storage strategy:
--
--   * NL-gen layer (workflows whose agent goes through agent_solver):
--     replay reads from the existing `iteration_case_outputs` table.
--     `output_json.predicted` is the bool prediction, `output_json.rationale`
--     is the agent's explanation, `passed` is the gate-compare bit, and
--     `output_payload` is the domain-shaped artifact. The pair
--     `(iteration_id, eval_case_id)` is already unique-keyed, so per-case
--     lookup is a single index probe. NO NEW TABLE needed for this path.
--
--   * Benchmark layer (workflows whose agent goes through SandboxRuntime):
--     replay reads from a NEW `captured_sandbox_runs` table. Each row
--     is one Docker run captured during a real iteration, addressed by
--     (iteration_id, call_idx). The call_idx is a per-iteration
--     monotonically-increasing counter so ReplaySimSandbox can cursor
--     through them in the same order they were emitted.
--
-- Config shape (workflows.replay_sim_config jsonb):
--
--   {
--     "source_iteration_id": "<uuid of the iteration to replay against>",
--     "fallback": "error" | "mock" | "real"
--   }
--
-- `source_iteration_id` points at an iteration whose outputs were
-- captured during a real run. `fallback` controls what happens when a
-- case (NL-gen) or call_idx (benchmark) isn't present in the captured
-- set: 'error' raises, 'mock' degrades to MockAgentSolver / MockSimSandbox,
-- 'real' degrades to the live LLM / Docker. Default 'error' on
-- ReplaySimConfig — silent degradation hides real correctness gaps.

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS replay_sim_config jsonb DEFAULT NULL;

-- When sim_tier='replay', replay_sim_config must be non-NULL. Same
-- shape as the 'mock' constraint from migration 0018.
ALTER TABLE workflows
    ADD CONSTRAINT workflows_replay_sim_config_required
        CHECK (sim_tier <> 'replay' OR replay_sim_config IS NOT NULL);

-- =============================================================================
-- captured_sandbox_runs — per-iteration recorded Docker outputs
-- =============================================================================
-- Populated by CapturingSandbox (a decorator around LocalDockerSandbox)
-- during real iterations on benchmark workflows. ReplaySimSandbox cursors
-- through these in (iteration_id, call_idx) order to replay the same
-- sandbox responses without paying for Docker execution.
--
-- We keep the recorded result as raw jsonb (status / output / stderr /
-- duration_ms / error / error_class) rather than reconstructing the
-- SandboxResult dataclass at insert time. Read-side does the parse —
-- the contract is "what LocalDockerSandbox returned." Schema drift on
-- SandboxResult (adding a new field) is then a code-only change; the
-- jsonb survives.

CREATE TABLE IF NOT EXISTS captured_sandbox_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     text NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    iteration_id    uuid NOT NULL REFERENCES iterations(id) ON DELETE CASCADE,
    call_idx        integer NOT NULL,        -- 0-based, per-iteration monotonic
    result          jsonb NOT NULL,          -- {status, output, stderr, exit_code, duration_ms, error, error_class}
    captured_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (iteration_id, call_idx)
);

CREATE INDEX IF NOT EXISTS captured_sandbox_runs_iter_idx
    ON captured_sandbox_runs(iteration_id);
CREATE INDEX IF NOT EXISTS captured_sandbox_runs_workflow_idx
    ON captured_sandbox_runs(workflow_id);
