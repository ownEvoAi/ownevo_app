-- 0014_workflow_agent_model.sql — per-workflow agent model choice
--
-- Sovereignty thesis: the customer picks the model, not us. Each
-- workflow stores the LLM the agent solver should use for its iteration
-- runs. Stored as a `provider:model` slug (e.g. `anthropic:claude-sonnet-4-6`,
-- `fireworks:kimi-k2p6`) so a single text column captures both the
-- provider routing prefix and the model identifier. No FK to a separate
-- providers table — the allowlist is operator-controlled via env vars
-- (`OWNEVO_PROVIDER_*_ENABLED` + `OWNEVO_PROVIDER_*_MODELS`) and
-- validated at the API boundary against the runtime-enabled list.
--
-- New rows default to `anthropic:claude-sonnet-4-6` so existing surfaces
-- that don't yet read `workflows.agent_model_id` keep behaving as
-- before. Phase 2 wires the per-workflow choice through the iteration
-- runner; this migration only persists the choice + the audit trail.

ALTER TABLE workflows
    ADD COLUMN IF NOT EXISTS agent_model_id text NOT NULL
        DEFAULT 'anthropic:claude-sonnet-4-6';

-- New audit_kind: every change to a workflow's model lands a hash-
-- chained entry so the customer can prove who switched models when.
-- IF NOT EXISTS keeps the migration idempotent across re-runs.
ALTER TYPE audit_kind ADD VALUE IF NOT EXISTS 'workflow-agent-model-changed';
