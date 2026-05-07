"""LLM-as-judge: ProposalContext → ApprovalJudgment via Anthropic tool-use (W5.2).

Mirrors `nl_gen.meta_eval.judge.judge_artifacts`:
single-turn `messages.create` with a forced `tool_choice`, the tool's
`input_schema` is `ApprovalJudgment.model_json_schema()` wrapped under
a `judgment` key, and the tool input is unwrapped + validated back
into an `ApprovalJudgment`.

What the judge sees in the user message:

  1. `proposal_id` (echoed back into the judgment for audit join).
  2. `cluster_label` — the failure cluster the proposal claims to
     address. The judge's `references_cluster` check is grounded
     against this string: the explanation must name it verbatim or
     paraphrase its observable behaviour.
  3. `cluster_summary` — a one-line description of what the cluster
     captures (e.g., "weekend demand spikes are systematically
     under-forecast"). Lets the judge pass paraphrases the kebab-case
     label couldn't match alone.
  4. `skill_id` — the skill being changed.
  5. `metric_name` + `metric_improvement_axis` (`lower-is-better` or
     `higher-is-better`) — grounds `states_direction`. An explanation
     that says "should increase RMSSE" on a lower-is-better metric
     fails the check; on a higher-is-better metric, it passes.
  6. The proposal's `explanation` — the full plain-language summary
     the human approver would read.

Why these six fields and not the full proposal context: the judge's
job is structural, not semantic — it does not need to read the diff
or the gate result to evaluate the explanation's structural elements.
Sending only the minimum keeps the prompt cheap, deterministic, and
hard for the judge to game (it can't infer "the gate passed so the
explanation is probably honest" if it doesn't see the gate result).

Lives in the `agent` extra (anthropic dep). The judge eval set
(`JUDGE_EVAL_SET`) and runner are kernel-runtime; only the judge
itself is gated on the agent extra.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from .judgment import SCHEMA_VERSION, ApprovalJudgment

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


DEFAULT_MODEL = "claude-opus-4-7"
"""Opus 4.7 by default — same calibration anchor as the A4.6 meta-eval
judge. The runner accepts a `model=` override so cheaper sub-runs
(haiku for triage, sonnet for spot-checks) are easy."""

DEFAULT_MAX_TOKENS = 3_000
"""Tighter than the meta-eval judge's 6k — this judge writes three
per-check rationales (≤400 chars each) + one overall_rationale
(≤600 chars), which fits comfortably under 3k."""


TOOL_NAME = "emit_approval_judgment"
TOOL_DESCRIPTION = (
    "Emit a structured ApprovalJudgment for the given (proposal, "
    "cluster, metric, explanation) context. Score three orthogonal "
    "structural elements (references_cluster, names_change, "
    "states_direction) with pass/fail + a rationale. Call this tool "
    "exactly once."
)


_IMPROVEMENT_AXIS_VALUES = ("lower-is-better", "higher-is-better")
MetricImprovementAxis = Literal["lower-is-better", "higher-is-better"]
"""Tells the judge whether the metric improves by going down (RMSSE,
MAE, regret, error count) or up (recall, precision, accuracy, lift).
Used to ground the `states_direction` check."""

_MAX_EXPLANATION_LEN = 4_000
"""Hard cap on explanation length. Explanations that exceed this are
rejected at ProposalContext construction time rather than silently
truncated — the caller should surface the error upstream rather than
letting the judge evaluate a truncated explanation."""


SYSTEM_PROMPT = (
    "You are ownEvo's LLM-judge stub approver. You receive a proposal's "
    "plain-language explanation and the structural context it was "
    "written against (cluster being addressed, skill being changed, "
    "metric and its improvement axis). Your job is to score three "
    "orthogonal structural elements with pass/fail + a one-line "
    "rationale.\n\n"
    "The three checks (apply each independently — do NOT let one "
    "check's outcome bias another):\n\n"
    "1. `references_cluster` — Does the explanation reference the "
    "failure cluster being addressed?\n"
    "   - `pass`: the explanation names the cluster (verbatim or "
    "paraphrased) OR describes the cluster's observable behaviour "
    "(e.g., 'weekend spikes' for cluster 'weekend-spike-under-"
    "forecast'). A substantive paraphrase counts.\n"
    "   - `fail`: the explanation refers only to generic 'errors' / "
    "'issues' / 'misses' / 'cases' without naming the specific "
    "failure mode, OR the cluster reference is to a *different* "
    "cluster (the explanation is for a workflow it shouldn't be).\n\n"
    "2. `names_change` — Does the explanation name what is changing "
    "in the skill?\n"
    "   - `pass`: the change is specified (e.g., 'added a Friday-"
    "the-13th feature flag', 'switched from rolling-mean to median "
    "imputation', 'lowered the outlier threshold from 3σ to 2σ', "
    "'added the holiday calendar to the feature pipeline'). The "
    "specifics make the diff understandable without reading the code.\n"
    "   - `fail`: vague non-specifics like 'tuned the model', 'made "
    "the predictor better', 'fixed it', 'improved feature engineering' "
    "without naming the lever pulled. 'Updated' or 'refactored' alone "
    "fail.\n\n"
    "3. `states_direction` — Does the explanation state an expected "
    "metric direction consistent with the metric's improvement axis?\n"
    "   - `pass`: the explanation states an expected effect on the "
    "named metric AND that effect is in the improvement direction "
    "(e.g., 'should reduce RMSSE by ~5%' is `pass` on a "
    "lower-is-better RMSSE; 'should lift recall on under-forecast "
    "cases' is `pass` on a higher-is-better recall).\n"
    "   - `fail`: NO direction stated (e.g., 'this will change the "
    "metric'), OR direction stated but in the WRONG direction (e.g., "
    "'should increase RMSSE' when RMSSE is lower-is-better — this is "
    "the structural-but-wrong-direction reject case the judge MUST "
    "catch). Naming a metric without naming a direction also fails.\n\n"
    "Rules:\n"
    "1. Quote the explanation's verbatim phrasing in rationales where "
    "possible — this is what makes the judgment auditable.\n"
    "2. Each rationale is one line (≤400 chars). overall_rationale "
    "ties the three check verdicts together (≤600 chars).\n"
    f"3. Set the top-level `schema_version` field to {SCHEMA_VERSION!r}. "
    "Do NOT add a `schema_version` field to the per-check sub-objects "
    "— they only carry `verdict` and `rationale`. Set `proposal_id` "
    "to the input's proposal_id verbatim.\n"
    "4. Be calibrated: the W5.2 spec ships ~30 hand-labeled pairs, "
    "balanced across one admit bucket and four reject buckets. If you "
    "call everything `pass` or everything `fail`, your judgments are "
    "useless to the unattended replay run."
)


@dataclass(frozen=True)
class ProposalContext:
    """Minimum context the judge needs to evaluate one proposal explanation.

    All six fields are surfaced in the judge prompt; nothing else
    about the proposal (diff, gate result, parent version) is sent.
    Keeping the surface small makes the judge's structural verdict
    independent of the proposal's actual quality — the judge measures
    *the explanation*, not the change.
    """

    proposal_id: str
    cluster_label: str
    cluster_summary: str
    skill_id: str
    metric_name: str
    metric_improvement_axis: MetricImprovementAxis
    explanation: str

    def __post_init__(self) -> None:
        if not self.proposal_id or not self.proposal_id.strip():
            raise ValueError("ProposalContext.proposal_id must be non-empty")
        if not self.cluster_label or not self.cluster_label.strip():
            raise ValueError("ProposalContext.cluster_label must be non-empty")
        if not self.cluster_summary or not self.cluster_summary.strip():
            raise ValueError("ProposalContext.cluster_summary must be non-empty")
        if not self.skill_id or not self.skill_id.strip():
            raise ValueError("ProposalContext.skill_id must be non-empty")
        if not self.metric_name or not self.metric_name.strip():
            raise ValueError("ProposalContext.metric_name must be non-empty")
        if self.metric_improvement_axis not in _IMPROVEMENT_AXIS_VALUES:
            raise ValueError(
                f"ProposalContext.metric_improvement_axis must be one of "
                f"{_IMPROVEMENT_AXIS_VALUES}; got "
                f"{self.metric_improvement_axis!r}",
            )
        if not self.explanation or not self.explanation.strip():
            raise ValueError("ProposalContext.explanation must be non-empty")
        if len(self.explanation) > _MAX_EXPLANATION_LEN:
            raise ValueError(
                f"ProposalContext.explanation exceeds max length "
                f"({len(self.explanation)} > {_MAX_EXPLANATION_LEN})",
            )


class ApproversError(Exception):
    """Base error for the approvers package."""


class JudgmentValidationError(ApproversError):
    """Claude returned a tool input that failed ApprovalJudgment validation."""

    def __init__(
        self,
        message: str,
        *,
        raw_input: Any,
        pydantic_error: ValidationError,
    ) -> None:
        super().__init__(message)
        self.raw_input = raw_input
        self.pydantic_error = pydantic_error


class NoJudgeToolUseError(ApproversError):
    """Claude responded without calling the emit_approval_judgment tool."""

    def __init__(
        self,
        message: str,
        *,
        stop_reason: str | None,
        content_preview: str,
    ) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason
        self.content_preview = content_preview


class JudgeProposalIdMismatchError(ApproversError):
    """Generated judgment's proposal_id doesn't match the input context.

    The judge is supposed to copy the proposal_id verbatim. If it
    picks a different id, downstream audit-log joins break silently
    — surface it as a typed error rather than letting it slip through.
    """

    def __init__(
        self,
        message: str,
        *,
        judgment: ApprovalJudgment,
        expected_id: str,
    ) -> None:
        super().__init__(message)
        self.judgment = judgment
        self.expected_id = expected_id


def _build_tool_definition() -> dict[str, Any]:
    """Anthropic tool definition.

    Same `{"judgment": <ApprovalJudgment>}` wrapping pattern as the
    NL-gen judges — small models nest deep object outputs under an
    outer field even when the schema is flat. `$defs` are hoisted to
    the input_schema root so `$ref` pointers resolve correctly.
    """
    j_schema = ApprovalJudgment.model_json_schema()
    defs = j_schema.pop("$defs", {})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"judgment": j_schema},
        "required": ["judgment"],
    }
    if defs:
        input_schema["$defs"] = defs
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": input_schema,
    }


_TOOL_DEFINITION: dict[str, Any] = _build_tool_definition()
_TOOL_CHOICE: dict[str, str] = {"type": "tool", "name": TOOL_NAME}


def _format_user_message(ctx: ProposalContext) -> str:
    """Assemble the judge's user message.

    The six context fields go in as labelled blocks; the explanation
    is fenced verbatim so multi-line / code-fragment content survives
    intact.
    """
    # Sanitize triple-backticks so the explanation can't break out of its
    # fence and inject new instructions into the judge prompt.
    safe_explanation = ctx.explanation.replace("```", "'''")
    return (
        "Here is the proposal context to judge. Apply the three "
        "structural checks independently. Quote the explanation's "
        "verbatim phrasing in rationales where possible.\n\n"
        f"proposal_id: {ctx.proposal_id}\n"
        f"cluster_label: {ctx.cluster_label}\n"
        f"cluster_summary: {ctx.cluster_summary}\n"
        f"skill_id: {ctx.skill_id}\n"
        f"metric_name: {ctx.metric_name}\n"
        f"metric_improvement_axis: {ctx.metric_improvement_axis}\n\n"
        "explanation (note: backtick-fences in the text below are "
        "represented as single-quotes to preserve delimiter integrity):\n"
        f"```\n{safe_explanation}\n```"
    )


async def judge_proposal(
    client: AsyncAnthropic,
    ctx: ProposalContext,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> ApprovalJudgment:
    """Judge one ProposalContext and return the structural verdict.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible endpoint).
        ctx: The proposal context (see `ProposalContext`).
        model: Anthropic model id. Default opus 4.7 (calibration anchor).
        max_tokens: Output cap. Default 3k.

    Returns:
        A validated `ApprovalJudgment`. Read `judgment.admits` for the
        binary admit/reject the runtime should act on.

    Raises:
        NoJudgeToolUseError: Claude stopped without calling the tool.
        JudgmentValidationError: Claude called the tool but the input
            failed `ApprovalJudgment.model_validate`.
        JudgeProposalIdMismatchError: The generated judgment's
            proposal_id doesn't match `ctx.proposal_id`.
    """
    msg = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        tools=[_TOOL_DEFINITION],
        tool_choice=_TOOL_CHOICE,
        messages=[
            {
                "role": "user",
                "content": _format_user_message(ctx),
            }
        ],
    )

    tool_blocks = [
        b for b in msg.content
        if getattr(b, "type", None) == "tool_use"
        and getattr(b, "name", None) == TOOL_NAME
    ]
    if not tool_blocks:
        text_blocks = [b for b in msg.content if getattr(b, "type", None) == "text"]
        preview = (text_blocks[0].text if text_blocks else "")[:300]
        raise NoJudgeToolUseError(
            f"Model {model} did not call {TOOL_NAME} (stop_reason={msg.stop_reason!r})",
            stop_reason=msg.stop_reason,
            content_preview=preview,
        )

    raw_input = tool_blocks[0].input
    if (
        isinstance(raw_input, dict)
        and "judgment" in raw_input
        and len(raw_input) == 1
    ):
        payload = raw_input["judgment"]
    else:
        payload = raw_input

    # Defensive parsing #1: opus 4.7 occasionally returns the wrapped
    # value as a JSON-encoded string rather than a dict (observed in
    # the A4.6 live smoke 2026-05-06). Try one round of JSON-decoding
    # if we see a string where a dict was expected; non-JSON strings
    # fall through to the normal validation path which raises the
    # typed error.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            pass  # let model_validate raise the typed error below

    # Defensive parsing #2: opus 4.7 sometimes propagates the top-level
    # `schema_version` field into each per-check sub-object. The check
    # schema is `extra='forbid'` so this would otherwise fail validation.
    # Strip the spurious key only — every other unexpected key still
    # fails loudly so a real schema regression doesn't slip through.
    if isinstance(payload, dict):
        for check_key in ("references_cluster", "names_change", "states_direction"):
            check_val = payload.get(check_key)
            if isinstance(check_val, dict) and "schema_version" in check_val:
                check_val.pop("schema_version", None)

    try:
        judgment = ApprovalJudgment.model_validate(payload)
    except ValidationError as exc:
        try:
            preview = json.dumps(raw_input)[:500]
        except (TypeError, ValueError):
            preview = repr(raw_input)[:500]
        raise JudgmentValidationError(
            f"Tool input failed ApprovalJudgment validation: {exc.error_count()} "
            f"errors. Input preview: {preview}",
            raw_input=raw_input,
            pydantic_error=exc,
        ) from exc

    if judgment.proposal_id != ctx.proposal_id:
        raise JudgeProposalIdMismatchError(
            f"judgment.proposal_id={judgment.proposal_id!r} does not match "
            f"ctx.proposal_id={ctx.proposal_id!r} — judge copied the wrong "
            f"id; downstream audit joins would break silently",
            judgment=judgment,
            expected_id=ctx.proposal_id,
        )

    return judgment


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "MetricImprovementAxis",
    "ProposalContext",
    "ApproversError",
    "JudgmentValidationError",
    "NoJudgeToolUseError",
    "JudgeProposalIdMismatchError",
    "judge_proposal",
]
