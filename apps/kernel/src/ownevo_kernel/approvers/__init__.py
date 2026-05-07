"""LLM-judge stub approver (W5.2).

Stand-in for a human approver, used in unattended benchmark runs (W5.4
7-day M5 replay, W6 30-day replay). The judge does NOT replace the
human approver in the demo — `approver_type=ApproverType.LLM_JUDGE` is
recorded on the audit row so a reviewer can always tell which decisions
came from the stub vs a human.

The decision rule (per PLAN.md § Week 5 5.2):

    admit iff (a) the regression gate passed AND
              (b) the proposal's plain-language explanation contains
                  three structural elements:
                    1. references the failure cluster being addressed,
                    2. names the change being made to the skill,
                    3. states an expected direction on the metric
                       (consistent with the metric's improvement axis).
    reject otherwise.

(a) is mechanical; (b) is the LLM's job. The W5.2 module ships only
the LLM judgment side. The runtime wiring (`decide_via_judge`) combines
gate-pass with the judgment and calls into `approvals.service.approve_proposal`
or `reject_proposal` with `approver_type=LLM_JUDGE`.

The judge is graded against `JUDGE_EVAL_SET` — 30 hand-labeled
(proposal-context, explanation, expected_admit) records spanning
admit/reject buckets. Agreement target: ≥0.85 (higher than meta-eval's
0.7 because false-positives drift the M5 lift the wrong direction).
"""

from .judgment import (
    SCHEMA_VERSION,
    ApprovalJudgment,
    StructuralCheck,
    StructuralVerdict,
)
from .llm_judge import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    SYSTEM_PROMPT,
    TOOL_NAME,
    JudgeProposalIdMismatchError,
    JudgmentValidationError,
    NoJudgeToolUseError,
    ProposalContext,
    judge_proposal,
)

__all__ = [
    "SCHEMA_VERSION",
    "ApprovalJudgment",
    "StructuralCheck",
    "StructuralVerdict",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "SYSTEM_PROMPT",
    "TOOL_NAME",
    "JudgmentValidationError",
    "NoJudgeToolUseError",
    "JudgeProposalIdMismatchError",
    "ProposalContext",
    "judge_proposal",
]
