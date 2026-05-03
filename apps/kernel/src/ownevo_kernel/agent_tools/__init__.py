"""Agent tool surface (W2.1).

Five kernel-side functions the coding agent calls:

  read_skill(conn, skill_id)             → SkillReadResult | None
  write_skill(conn, skill_id, content)   → RegisterResult
  run_pipeline(sandbox, ...)             → PipelineResult
  read_metrics(conn, trace_id)           → dict | None
  analyze_failures(conn, workflow_id=, k=10) → list[FailureSnapshot]

Agent-SDK wiring (Claude Agent SDK middleware adapter that exposes these
as tool definitions and emits AgentEvents into a TraceCollector) is a
separate slice — these functions are usable directly from the gate
runner and tests without taking the SDK as a dep.

Train/test discipline lives in the read tools (`read_metrics`,
`analyze_failures`): both refuse to surface traces stamped
`fold == "test"` in `metric_outputs` unless the caller explicitly opts
in. Gate runner is the only call site that opts in.
"""

from .metrics import (
    FOLD_KEY,
    TEST_FOLD,
    FailureSnapshot,
    TestFoldAccessRefused,
    analyze_failures,
    read_metrics,
)
from .run_pipeline import PipelineResult, run_pipeline
from .skills import SkillFormatError, SkillReadResult, read_skill, write_skill

__all__ = [
    "FOLD_KEY",
    "FailureSnapshot",
    "PipelineResult",
    "SkillFormatError",
    "SkillReadResult",
    "TEST_FOLD",
    "TestFoldAccessRefused",
    "analyze_failures",
    "read_metrics",
    "read_skill",
    "run_pipeline",
    "write_skill",
]
