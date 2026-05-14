-- 0005_workflow_sim_metric.sql
--
-- PLAN row 8.4.5. The iteration runner reads sim_plan + metric_definition
-- when it scores an agent run. Persisting them on the workflow row means
-- `run_nl_gen_demo_loop` doesn't have to regenerate them on every iteration.
--
-- Both columns are nullable: legacy workflows (m5-demand-prediction,
-- tau3-retail-v1) don't have JSONB sim/metric — they're code-driven by
-- their own benchmark runners. The iteration runner branches on
-- "is this an NL-gen workflow?" by checking simulation_plan IS NOT NULL.

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS simulation_plan jsonb,
    ADD COLUMN IF NOT EXISTS metric_definition jsonb;
