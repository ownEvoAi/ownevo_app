"""Replay loops — synthetic-reward + real-substrate cycles for demos and ablations.

Drives the substrate end-to-end (gate → audit → cluster-derived eval-case
growth → LLM-judge admit) over N simulated cycles so the demo loop has a
visibly climbing lift curve plus an audit trail and an eval set that grew
from clusters.

The orchestration is **substrate-real**: every iteration row, proposal
row, audit_entry, eval_case, and approval row is committed to the actual
database. The score signal is **synthetic**: a `SyntheticBenchmarkRunner`
returns deterministic improving rewards per cycle so the loop runs in
seconds without a sandbox / Anthropic / LightGBM. The 30-day replay
(``thirty_day.py``) swaps the runner for the real M5 path; everything
else stays.

Two entry points:

- ``run_seven_day_replay(...)`` — short demo loop. Single condition,
  default settings; used for the W5.4 7-day demo and as a sanity check
  before kicking off a multi-condition run.
- ``run_all_conditions_parallel(...)`` — multi-condition 30-day
  comparison. Each condition runs as its own ``workflow_id`` so its
  gate history is independent; ``merge_results`` produces the
  cross-condition lift table at the end.

Conditions (exported as constants for use by call sites and scripts):

- ``CONDITION_A_FROZEN`` — baseline. No proposer, no gate-driven
  evolution; the agent runs the v1 skill against every iteration.
  Establishes the floor.
- ``CONDITION_B_STATIC_LLM`` — static frontier LLM, no improvement
  loop. Same agent, no gate, no eval-case growth. Demonstrates that
  better-model-alone isn't enough. (Stub today; wired into the runner
  when a fresh frontier model justifies the cloud spend.)
- ``CONDITION_C_LOOP_AUTONOMOUS`` — full improvement loop, autonomous
  approval (gate-pass auto-deploys). Tests the loop without human
  bottleneck.
- ``CONDITION_D_LOOP_GATED`` — full improvement loop, human (or
  LLM-judge) approval gate. The path real customers run.

``approver_mode_for_condition(c)`` maps each constant to a workflow
``mode`` value (see ``docs/STATE_MACHINES.md`` for the ApproverType ×
mode matrix). ``workflow_id_for_condition(c)`` derives the deterministic
per-condition ``workflow_id`` so re-runs are idempotent.

Env-override semantics: ``run_improvement_loop_subprocess`` accepts an
``env`` dict that **merges onto** ``os.environ`` for the subprocess. Use
this to point a single condition at a different LLM backend or DB without
mutating the orchestrator's environment.
"""

from .seven_day import (
    CycleSummary,
    ReplayConfig,
    ReplayReport,
    run_seven_day_replay,
)
from .thirty_day import (
    CONDITION_A_FROZEN,
    CONDITION_B_STATIC_LLM,
    CONDITION_C_LOOP_AUTONOMOUS,
    CONDITION_D_LOOP_GATED,
    DEFAULT_WORKFLOW_PREFIX,
    SUPPORTED_CONDITIONS,
    ConditionResult,
    ConditionSpec,
    IterationOutcome,
    ProgressCallback,
    SubprocessResult,
    ThirtyDayReport,
    approver_mode_for_condition,
    merge_results,
    run_all_conditions_parallel,
    run_condition_loop,
    run_improvement_loop_subprocess,
    workflow_id_for_condition,
)

__all__ = [
    "CONDITION_A_FROZEN",
    "CONDITION_B_STATIC_LLM",
    "CONDITION_C_LOOP_AUTONOMOUS",
    "CONDITION_D_LOOP_GATED",
    "ConditionResult",
    "ConditionSpec",
    "CycleSummary",
    "DEFAULT_WORKFLOW_PREFIX",
    "IterationOutcome",
    "ProgressCallback",
    "ReplayConfig",
    "ReplayReport",
    "SUPPORTED_CONDITIONS",
    "SubprocessResult",
    "ThirtyDayReport",
    "approver_mode_for_condition",
    "merge_results",
    "run_all_conditions_parallel",
    "run_condition_loop",
    "run_improvement_loop_subprocess",
    "run_seven_day_replay",
    "workflow_id_for_condition",
]
