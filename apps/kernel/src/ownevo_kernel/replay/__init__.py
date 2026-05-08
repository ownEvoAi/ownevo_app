"""Replay loops (W5.4 — 7-day M5 demo).

Drives the substrate end-to-end (gate → audit → cluster-derived eval-case
growth → LLM-judge admit) over N simulated cycles so the demo loop has a
visibly climbing lift curve plus an audit trail and an eval set that grew
from clusters.

The orchestration is **substrate-real**: every iteration row, proposal
row, audit_entry, eval_case, and approval row is committed to the actual
database. The score signal is **synthetic**: a `SyntheticBenchmarkRunner`
returns deterministic improving rewards per cycle so the loop runs in
seconds without a sandbox / Anthropic / LightGBM. The W6 full-M5 30-day
replay swaps the runner; everything else stays.
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
