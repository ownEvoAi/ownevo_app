"""LLM-judge stub approver (W5.2 — PLAN.md § W5 § 5.2).

The stub for the eventual full LLM-as-judge approver. Admits a proposal
when its plain-language explanation contains three structural elements:

  1. A reference to the failure cluster the change addresses.
  2. A description of the change being made.
  3. An expected metric direction the change is meant to move.

Rejects everything else. Used for unattended benchmark runs (the
W5.2 + W6 M5 / Tau3 condition-C runs); the human + UI path is the
W2.5 surface in `apps/kernel/src/ownevo_kernel/approvals/`.

Public surface:

  * `LLMJudgeApprovalJudgment` — typed verdict (per-element booleans
    + binary admit/reject + rationale + echoed proposal_id).
  * `LabeledApprovalCase` + `LABELED_APPROVAL_CASES` — 30 hand-
    authored fixtures spanning four buckets:
        - `structural` — all three elements present (admit).
        - `vague-but-positive` — generic optimism, no structure (reject).
        - `structural-but-wrong-direction` — names cluster + change
          but states the wrong metric direction (reject).
        - `hand-wavy` — partial coverage (reject).
  * `judge_proposal_explanation(client, case)` — single-turn
    Anthropic tool-use; returns `LLMJudgeApprovalJudgment`.
  * `run_llm_judge_approver_eval(client, ...)` — drives the judge
    across the fixture set and reports judge-vs-human agreement.

W5.2 exit criterion: agreement ≥ 0.85 on `LABELED_APPROVAL_CASES`,
judged by opus 4.7 (D4: different model from the agent loop's solver,
which runs on cheaper tiers; opus is the calibration anchor and
strictly stronger than the labeler). The CLI
(`scripts/llm_judge_approver_eval.py`) wires `--require-agreement` so
on-demand runs exit 1 when the gate misses. The bar is higher than
B3.5's 0.7 (cluster-label) because false-positives here drift M5 lift
the wrong direction, not just mislabel a card.

`judge_proposal_explanation` and `run_llm_judge_approver_eval` live in
the `agent` extra (anthropic dep). They are imported lazily so
installs without that extra don't fail at import time. The schema,
fixtures, and the runner's data classes are kernel-runtime.
"""

from .fixtures import LABELED_APPROVAL_CASES, LabeledApprovalCase
from .judgment import (
    SCHEMA_VERSION,
    ApprovalVerdict,
    LLMJudgeApprovalJudgment,
    StructuralElement,
    verdict_score,
)

__all__ = [
    # Schema
    "SCHEMA_VERSION",
    "ApprovalVerdict",
    "StructuralElement",
    "LLMJudgeApprovalJudgment",
    "verdict_score",
    # Fixtures
    "LabeledApprovalCase",
    "LABELED_APPROVAL_CASES",
    # Judge — lazy (agent extra)
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "LLMJudgeApproverError",
    "LLMJudgeApprovalJudgmentValidationError",
    "NoLLMJudgeApprovalToolUseError",
    "LLMJudgeApprovalIdMismatchError",
    "judge_proposal_explanation",
    # Runner — lazy (agent extra; imports judge)
    "LLMJudgeApprovalEvalRecord",
    "LLMJudgeApprovalEvalReport",
    "run_llm_judge_approver_eval",
]


_JUDGE_LAZY_NAMES = {
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "LLMJudgeApproverError",
    "LLMJudgeApprovalJudgmentValidationError",
    "NoLLMJudgeApprovalToolUseError",
    "LLMJudgeApprovalIdMismatchError",
    "judge_proposal_explanation",
}

_RUNNER_LAZY_NAMES = {
    "LLMJudgeApprovalEvalRecord",
    "LLMJudgeApprovalEvalReport",
    "run_llm_judge_approver_eval",
}


def __getattr__(name: str):  # pragma: no cover - thin lazy-import shim
    if name in _JUDGE_LAZY_NAMES:
        from . import judge

        return getattr(judge, name)
    if name in _RUNNER_LAZY_NAMES:
        from . import runner

        return getattr(runner, name)
    raise AttributeError(name)
