"""LLM-as-judge: (proposal, explanation) → LLMJudgeApprovalJudgment via
Anthropic tool-use (W5.2).

Mirrors `clustering/label_eval/judge.py` and `nl_gen/meta_eval/judge.py`:
single-turn `messages.create` with a forced `tool_choice`, the tool's
`input_schema` is `LLMJudgeApprovalJudgment.model_json_schema()`
wrapped under a `judgment` key, and the tool input is unwrapped +
validated back into a `LLMJudgeApprovalJudgment`.

What the judge sees in the user message:

  1. The `case_id` (must be echoed verbatim in the output).
  2. The `proposal_summary` (technical description of the change).
  3. The `cluster_name` (the failure cluster the change addresses).
  4. The `metric_direction_expected` (the direction the metric *ought*
     to move — the judge compares this against the explanation's claim).
  5. The `explanation` text under test.

Why send the expected direction: the judge's `structural-but-wrong-
direction` bucket is the failure mode the spec explicitly targets
("admit only if direction is consistent with the cluster's bias").
Without telling the judge what `up` or `down` means for this cluster,
it would have to infer that from the cluster name + domain knowledge,
which is the most error-prone calibration axis.

Default model is `claude-opus-4-7` — the W5.2 calibration anchor.
The agent loop's solver runs on cheaper tiers (haiku 4.5 / sonnet 4.6
in the M5 + Tau3 sweeps); opus is strictly stronger and satisfies
"different model from the agent path".

Lives in the `agent` extra (anthropic dep). The judgment schema, the
fixtures, and the runner's data classes are kernel-runtime; only the
judge call itself is gated on the agent extra.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .fixtures import LabeledApprovalCase
from .judgment import SCHEMA_VERSION, LLMJudgeApprovalJudgment

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


DEFAULT_MODEL = os.environ.get("OWNEVO_APPROVER_MODEL") or "claude-opus-4-7"
"""Opus 4.7 by default — calibration anchor for the ≥0.85 W5.2 gate.
The runner accepts a `model=` override so a sonnet or haiku run for
diagnostic purposes is one flag away. Production callers (M5 + Tau3
condition C in W6 + W8) MAY override to a cheaper model once the gate
agreement is calibrated against opus."""


DEFAULT_MAX_TOKENS = 1_500
"""Slightly looser than B3.5's 1k — the judgment carries 3 per-element
quotes + a rationale; 1.5k headroom keeps a long quote from clipping."""


TOOL_NAME = "emit_llm_judge_approval_judgment"
TOOL_DESCRIPTION = (
    "Emit a structured LLMJudgeApprovalJudgment for the given (proposal, "
    "explanation) pair. Decide per-element: is the cluster referenced? "
    "Is the change named? Is the metric direction stated? Then emit the "
    "binary verdict (admit | reject) + a one- or two-sentence rationale "
    "+ the echoed case_id. Call this tool exactly once."
)


SYSTEM_PROMPT = (
    "You are ownEvo's LLM-judge stub approver. You receive a proposed "
    "change (the technical summary), the failure cluster the change is "
    "supposed to address, the metric direction the change ought to move "
    "the success metric, and the plain-English explanation that "
    "accompanies the proposal. Decide per-element whether the "
    "explanation contains the three structural elements, then emit a "
    "binary verdict.\n\n"
    "Per-element criteria:\n\n"
    "1. `cluster_referenced.present = true` iff the explanation refers "
    "   to the failure cluster the change addresses. Synonyms + "
    "   paraphrases count: 'the weekend snack cluster', 'the under-"
    "   forecast issue we found', 'the CA snack problem' all count "
    "   for a `CA snack weekend under-forecasts` cluster. A vague "
    "   'this fixes a problem' or 'addresses an issue' does NOT count "
    "   — the cluster's specific identity (geography / category / "
    "   failure mode) must appear.\n\n"
    "2. `change_named.present = true` iff the explanation describes "
    "   the change *specifically*: names a feature added, a threshold "
    "   tuned, a signal introduced, an algorithm swap. 'I made some "
    "   improvements' or 'this is a better approach' do NOT count. "
    "   Referents like 'this change' or 'the change' without content "
    "   do NOT count.\n\n"
    "3. `metric_direction_stated.present = true` iff the explanation "
    "   states which direction the success metric is expected to move "
    "   (up / down / increase / decrease / improve recall / reduce "
    "   false alerts). 'this should be better' or 'should improve "
    "   things' do NOT count — better in which direction is missing.\n\n"
    "Verdict criterion:\n\n"
    "- `admit` iff ALL three elements are present AND they are mutually "
    "  consistent. Mutually consistent means: the stated metric "
    "  direction matches the direction the change *ought* to move the "
    "  metric for this cluster's bias. If the cluster is "
    "  'CA snack weekend under-forecasts' and the metric_direction_"
    "  expected is `up`, an explanation saying 'recall should go down' "
    "  is contradictory — `reject`, even though all three elements "
    "  are technically present.\n\n"
    "- `reject` in every other case. False-positives drift M5 lift the "
    "  wrong direction, so the safe default is `reject` whenever the "
    "  judge is unsure.\n\n"
    "For each element's `quote` field: include a short verbatim or "
    "near-verbatim excerpt from the explanation that supports your "
    "`present` verdict. Empty string when `present=false`.\n\n"
    "Rules:\n"
    f"1. Set `schema_version` to {SCHEMA_VERSION!r} and `proposal_id` to "
    "the verbatim case_id from the input. Do NOT invent a new id.\n"
    "2. Quote excerpts in the per-element `quote` fields, NOT in the "
    "rationale. The rationale ties the verdict to the per-element "
    "decisions in one or two sentences.\n"
    "3. Be calibrated. If the eval set were balanced (it's not — there "
    "are more reject ground truths than admit), and you call everything "
    "`reject`, the gate is meaningless. Reach for `admit` only when "
    "all three elements are present AND consistent.\n"
    "4. Do NOT write an essay. The output token budget is 1.5k; "
    "responses over 800 tokens are almost always padding."
)


class LLMJudgeApproverError(Exception):
    """Base for all W5.2 judge-time errors."""


class LLMJudgeApprovalJudgmentValidationError(LLMJudgeApproverError):
    """Claude returned a tool input that failed schema validation.

    Carries the raw input + the underlying pydantic error so the runner
    can retry on transient malformations without losing the diagnostic.
    """

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


class NoLLMJudgeApprovalToolUseError(LLMJudgeApproverError):
    """Claude responded without calling the judge's tool."""

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


class LLMJudgeApprovalIdMismatchError(LLMJudgeApproverError):
    """Generated judgment's proposal_id doesn't match the input case.

    The judge is supposed to copy the case_id verbatim. If it picks
    a different id, concurrent `runner.gather()` calls would correlate
    judgments to the wrong fixture — surface it as a typed error."""

    def __init__(
        self,
        message: str,
        *,
        judgment: LLMJudgeApprovalJudgment,
        expected_id: str,
    ) -> None:
        super().__init__(message)
        self.judgment = judgment
        self.expected_id = expected_id


def _build_tool_definition() -> dict[str, Any]:
    """Anthropic tool definition.

    `{"judgment": <LLMJudgeApprovalJudgment>}` wrapping mirrors
    A3.1/A3.2/A4.1/A4.2/A4.6/B3.5 — small models nest deep object
    outputs under an outer field even when the schema is flat.
    `$defs` are hoisted to the input_schema root so any `$ref`
    pointers resolve correctly.
    """
    j_schema = dict(LLMJudgeApprovalJudgment.model_json_schema())
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


def _format_user_message(case: LabeledApprovalCase) -> str:
    """Assemble the judge's user message.

    The expected metric direction is sent verbatim as `up` or `down`
    so the judge doesn't have to infer it from the cluster name.
    """
    return (
        "Here is one (proposal, explanation) pair to judge. Quote the "
        "explanation's relevant phrases in the per-element `quote` "
        "fields.\n\n"
        f"case_id: {case.case_id}\n"
        f"cluster_name: {case.cluster_name}\n"
        f"metric_direction_expected: {case.metric_direction_expected}\n\n"
        f"proposal_summary:\n{case.proposal_summary}\n\n"
        f"explanation under test:\n{case.explanation}\n\n"
        "Decide per-element (cluster_referenced, change_named, "
        "metric_direction_stated) → emit one "
        f"{TOOL_NAME} tool call with verdict + rationale + proposal_id "
        "echoed back."
    )


async def judge_proposal_explanation(
    client: AsyncAnthropic,
    case: LabeledApprovalCase,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> LLMJudgeApprovalJudgment:
    """Judge one (proposal, explanation) pair and return the verdict.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible
            endpoint).
        case: The hand-labeled fixture (carries case_id, proposal
            summary, cluster name, expected direction, explanation).
            In production, the agent loop will construct an equivalent
            shape from a real `Proposal`.
        model: Anthropic model id. Default opus 4.7 (W5.2 calibration
            anchor). Production callers MAY override to a cheaper
            model once the gate is calibrated.
        max_tokens: Output cap. Default 1.5k (3 per-element quotes +
            rationale fits with headroom).

    Returns:
        A validated `LLMJudgeApprovalJudgment` with proposal_id == case.case_id.

    Raises:
        NoLLMJudgeApprovalToolUseError: Claude stopped without calling
            the tool.
        LLMJudgeApprovalJudgmentValidationError: Claude called the tool
            but the input failed `LLMJudgeApprovalJudgment.model_validate`.
        LLMJudgeApprovalIdMismatchError: Generated judgment's
            proposal_id doesn't match `case.case_id`.
    """
    msg = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        tools=[_TOOL_DEFINITION],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": _format_user_message(case),
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
        raise NoLLMJudgeApprovalToolUseError(
            f"Model {model} did not call {TOOL_NAME} "
            f"(stop_reason={msg.stop_reason!r})",
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

    # Defensive parsing: A4.6's live smoke caught two opus-4.7 quirks
    # (string-wrapped payload + nested schema_version leakage).
    # Keeping the JSON-string fallback because it's free and would
    # cost a re-run if the same quirk surfaces under load.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            pass  # let model_validate raise the typed error below

    try:
        judgment = LLMJudgeApprovalJudgment.model_validate(payload)
    except ValidationError as exc:
        try:
            preview = json.dumps(raw_input)[:500]
        except (TypeError, ValueError):
            preview = repr(raw_input)[:500]
        raise LLMJudgeApprovalJudgmentValidationError(
            f"Tool input failed LLMJudgeApprovalJudgment validation: "
            f"{exc.error_count()} errors. Input preview: {preview}",
            raw_input=raw_input,
            pydantic_error=exc,
        ) from exc

    if judgment.proposal_id != case.case_id:
        raise LLMJudgeApprovalIdMismatchError(
            f"judgment.proposal_id={judgment.proposal_id!r} does not "
            f"match case.case_id={case.case_id!r} — judge invented an "
            f"id; concurrent runner.gather() would correlate to the "
            f"wrong fixture",
            judgment=judgment,
            expected_id=case.case_id,
        )

    return judgment


__all__ = [
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
]
