# State Machines

Every state transition writes an `audit_entries` row. The state machines
below define which transitions are legal and which audit kind they
produce.

## Proposal

```
                    ┌─────────────┐
                    │   pending   │
                    └──────┬──────┘
                           │ gate-run-started
                           ▼
                    ┌─────────────┐
                    │   in-gate   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────────────────────┐
              │            │                            │
   gate fails │      regression                no improvement
   technically│      blocked                   blocked
              ▼            ▼                            ▼
       ┌─────────────┐ ┌──────────────┐         ┌──────────────┐
       │ gate-failed │ │  rejected    │         │  rejected    │
       │  (sandbox   │ │ (regression) │         │ (no-improve) │
       │   error)    │ │              │         │              │
       └──────┬──────┘ └──────────────┘         └──────────────┘
              │
              │  human-retried (Phase 2; not MVP)
              ▼
              X (drop)

   gate passes (val_score > best_ever AND prior suite still passes)
              │
              ▼
       ┌──────────────────┐
       │   gate-passed    │
       └───────┬──────────┘
               │
   ┌───────────┴───────────────────────┐
   │ workflow.mode = autonomous        │ workflow.mode = gated
   │ (auto-approved immediately)       │
   │                                   │ human / LLM-judge decides
   │                         ┌─────────┴──────────┐
   │                         │                    │
   │                     approves             rejects
   │                         │                    ▼
   │                         │             ┌──────────┐
   │                         │             │ rejected │
   │                         │             └──────────┘
   │                         │
   └─────────────────────────┤
                             ▼
                    ┌──────────────────────┐
                    │ approved-            │
                    │ awaiting-deploy      │
                    └────────┬─────────────┘
                             │ deploy job runs
                             ▼
                    ┌──────────────────┐
                    │     deployed     │
                    └────────┬─────────┘
                             │ (demo rollback runbook)
                             ▼
                    ┌──────────────────┐
                    │   rolled-back    │
                    └──────────────────┘
```

### Transition rules

| From | To | Trigger | `approver_type` | Audit kind |
|------|-----|---------|-----------------|------------|
| (none) | `pending` | Agent emits proposal via `write_skill` | — | `proposal-created` |
| `pending` | `in-gate` | Gate runner picks up the proposal | — | `gate-run-started` |
| `in-gate` | `gate-failed` | Sandbox returned `error_class` (Timeout/OOM/Crash) | — | `gate-run-completed` (state=sandbox-error) |
| `in-gate` | `rejected` (regression) | Step 1 of gate failed | — | `gate-run-completed` + `proposal-rejected` |
| `in-gate` | `rejected` (no-improve) | Step 2 of gate failed | — | `gate-run-completed` + `proposal-rejected` |
| `in-gate` | `gate-passed` | All 3 steps passed | — | `gate-run-completed` |
| `gate-passed` | `approved-awaiting-deploy` | `workflow.mode = autonomous` — auto-approved without human review | `autonomous` | `proposal-approved` |
| `gate-passed` | `approved-awaiting-deploy` | Human approves | `human` | `proposal-approved` |
| `gate-passed` | `approved-awaiting-deploy` | LLM-judge approves | `llm-judge` | `proposal-approved` |
| `gate-passed` | `rejected` | Human or LLM-judge rejects | `human` / `llm-judge` | `proposal-rejected` |
| `approved-awaiting-deploy` | `deployed` | Deploy job advances HEAD on `skills` table | — | `proposal-deployed` |
| `deployed` | `rolled-back` | `make revert-skill` operator script (see `docs/runbooks/demo-rollback.md`) | — | `proposal-rolled-back` |

### Invariants

- A proposal CAN'T skip `in-gate` — gate runs on every proposal regardless of mode or source.
- `rejected` is terminal. If the same agent wants to retry, it creates a NEW proposal (new `proposals.id`).
- `gate-failed` (technical) is distinct from `rejected` (logical). The agent's `learnings.md` records both, but only `rejected` counts toward "3 failures on the same hypothesis → abandon."
- `autonomous` mode does NOT write a human `approvals` row — the `approved-awaiting-deploy` transition is driven directly by the gate runner, with `approver_type = 'autonomous'` recorded in the audit payload.
- `autonomous` mode is per-workflow (`workflows.mode`), not per-proposal. Switching a workflow between modes mid-run is not supported in MVP.
- The `became_eval_case_id` on `approvals` is non-null when (decision = reject) AND (comment is non-empty). Not applicable in autonomous mode (no rejection path from auto-approve).

## Iteration

```
   ┌──────────┐
   │ running  │
   └─────┬────┘
         │ agent emits proposal AND gate completes
         ▼
   ┌─────────────────────────────────────────┐
   │  gate-pass | gate-blocked-regression |  │
   │  gate-blocked-no-improvement |          │
   │  sandbox-error                          │
   └─────────────────────────────────────────┘
   (terminal — iterations are immutable once ended)
```

`best_ever_score_after = best_ever_score_before` UNLESS state = `gate-pass`,
in which case `best_ever_score_after = max(best_ever_score_before, val_score)`.

A `sandbox-error` state does **not** advance `best_ever_score`.

## Workflow (NL-gen lifecycle)

```
   ┌────────────────────┐
   │     created        │  (description received)
   └──────────┬─────────┘
              │ NL-gen sim_generator runs
              ▼
   ┌────────────────────┐
   │   sim-generated    │
   └──────────┬─────────┘
              │ NL-gen eval_generator + metric_generator run
              ▼
   ┌────────────────────┐
   │   artifacts-built  │
   └──────────┬─────────┘
              │ meta-eval runs
              ▼
   ┌────────────────────────────────────────────┐
   │  meta-eval-passed | meta-eval-failed       │
   └────────────────────────────────────────────┘
              │
              ▼  (only if meta-eval-passed)
   ┌────────────────────┐
   │   loop-eligible    │  (agent loop can run)
   └────────────────────┘
```

Workflow lifecycle is implicit in the database (sim_skill_id null vs set; meta_eval_score < threshold vs ≥) rather than explicit in a `workflows.state` column. Adding the column is trivial if observability needs it; deferred for MVP.

## Audit kind → related_id mapping

| audit kind | `related_id` references |
|-----------|------------------------|
| `skill-version-created` | `skill_versions.id` |
| `gate-run-started` | `iterations.id` |
| `gate-run-completed` | `iterations.id` |
| `proposal-created` | `proposals.id` |
| `proposal-approved` | `proposals.id` |
| `proposal-rejected` | `proposals.id` |
| `proposal-deployed` | `proposals.id` |
| `proposal-rolled-back` | `proposals.id` |
| `eval-case-added` | `eval_cases.id` |
| `cluster-created` | `failure_clusters.id` |
| `cluster-relabeled` | `failure_clusters.id` |
| `workflow-created` | `workflows.id` (cast to uuid via lookup, or use payload.workflow_id) |
| `meta-eval-result` | `workflows.id` |
| `schema-migration` | null (payload contains migration filename) |
| `deployment-created` | `skill_deployments.id` |
| `deployment-updated` | `skill_deployments.id` |

## Test coverage requirements (eng review)

Every transition above must have:

1. **Unit test** that asserts the transition is legal under the right conditions.
2. **Negative test** that asserts an illegal transition raises (e.g., `pending → deployed` skipping `in-gate`).
3. **Audit-coupling test** that asserts the right `audit_kind` is appended on each transition.

These live at `apps/kernel/tests/state_machines/test_proposal_states.py`.
