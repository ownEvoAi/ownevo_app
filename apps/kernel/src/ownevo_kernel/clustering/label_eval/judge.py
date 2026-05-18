"""LLM-as-judge: (case, candidate_label) → ClusterLabelJudgment via Anthropic
tool-use (B3.5).

Mirrors `nl_gen/meta_eval/judge.py`'s shape: single-turn `messages.create`
with a forced `tool_choice`, the tool's `input_schema` is
`ClusterLabelJudgment.model_json_schema()` wrapped under a `judgment`
key, and the tool input is unwrapped + validated back into a
`ClusterLabelJudgment`.

What the judge sees in the user message:

  1. The cluster's `domain_context` (one-line operator-readable framing).
  2. The cluster's `member_signatures` (the same strings the labeler saw).
  3. The `ground_truth_label` (the human-authored expected name).
  4. The `candidate_label` (what the labeler emitted).
  5. The `cluster_id` (must be echoed verbatim in the output).

Why send all five: the judge needs the members to ground its verdict
(otherwise it's just comparing two strings, and "snack under-forecasts"
vs "snack over-forecasts" both look plausible without seeing the
hint signs). The domain_context anchors the workflow framing — the
same label might be `agree` in one domain and `disagree` in another.

The judge does NOT have access to:

  * The cluster centroid / persistence — those are reducer/clusterer
    artifacts, not part of the labeling contract.
  * Any other cluster's label — pairwise comparisons across clusters
    are a separate eval (deferred; not the W3 deliverable).

Default model is `claude-opus-4-7` — the labeler is sonnet 4.6, so
opus satisfies D4 (different model) while providing stronger reasoning
for the binary verdict. Empirically validated at 0.90 agreement on the
20-case fixture set (W3 Track B gate, 2026-05-06).

Lives in the `agent` extra (anthropic dep). The judgment schema, the
fixtures, and the runner's data classes are kernel-runtime; only the
judge call itself is gated on the agent extra.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .fixtures import LabeledClusterCase
from .judgment import SCHEMA_VERSION, ClusterLabelJudgment

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


DEFAULT_MODEL = os.environ.get("OWNEVO_CLUSTER_JUDGE_MODEL") or "claude-opus-4-7"
"""Opus 4.7 by default — strictly stronger than the labeler model (D4
calls for "different model from the labeler"). The runner accepts a
`model=` override so calibration runs are easy. `OWNEVO_CLUSTER_JUDGE_MODEL`
env var overrides the module default for operators pointing at a
local-LLM judge — see `docs/local-model-testing.md`."""


DEFAULT_MAX_TOKENS = 1_000
"""Tighter than meta_eval's 6k — the judge writes one binary verdict +
a concise rationale; 1k headroom is generous."""


TOOL_NAME = "emit_cluster_label_judgment"
TOOL_DESCRIPTION = (
    "Emit a structured ClusterLabelJudgment for the given cluster "
    "(domain_context, member_signatures, ground_truth_label, "
    "candidate_label) input. Decide whether candidate_label is "
    "semantically equivalent to ground_truth_label given the members. "
    "Return verdict (agree|disagree) + a one-line rationale + the "
    "echoed cluster_id. Call this tool exactly once."
)


SYSTEM_PROMPT = (
    "You are ownEvo's cluster-label judge. You receive a cluster of "
    "forecasting failures (the same `text_signature` strings the "
    "labeler saw), a hand-authored ground-truth label, and a "
    "candidate label produced by the production labeler. Decide "
    "whether the candidate is semantically equivalent to the "
    "ground-truth, given the members.\n\n"
    "Verdict criteria:\n\n"
    "- `agree` — the candidate names the SAME failure mode as the "
    "ground-truth. Synonyms, reordered phrasing, and equivalent "
    "domain vocabulary are fine ('weekend snack under-forecasts' ≈ "
    "'CA snack under-forecasts on weekends'). The candidate may be "
    "MORE specific than the ground-truth as long as the extra "
    "specificity is supported by the members (e.g., naming a state "
    "that all members share). The candidate may also be slightly "
    "LESS specific (drops a non-load-bearing qualifier).\n\n"
    "- `disagree` — the candidate names a DIFFERENT failure mode "
    "(over-forecast vs under-forecast, zero-inflated vs flat-prediction, "
    "different category or geography), OR introduces a domain not "
    "present in the members (hallucination — e.g., labels a cluster "
    "of grocery failures as 'pet supplies'), OR contradicts the "
    "members' bias direction (says 'over-forecast' when the peak "
    "values are negative).\n\n"
    "Reading the members:\n"
    "- `hints=[under-forecast]` or `peak -X.XX` → cluster is missing "
    "demand (predicted < actual). 'over-forecast' as a candidate is "
    "WRONG.\n"
    "- `hints=[over-forecast]` or `peak +X.XX` → predicted > actual.\n"
    "- `hints=[zero-inflated]` → many zero-actual days. 'flat-line' "
    "may overlap but is distinct.\n"
    "- `hints=[flat-prediction]` → model emits constants; actuals "
    "swing.\n"
    "- `hints=[high-variance]` → actuals swing widely; sign of peak "
    "is not directional.\n"
    "- `[CAT/DEPT @ STATE/STORE]` → check whether all members share a "
    "state, store, or department. The label may legitimately mention "
    "the shared dimension.\n\n"
    "Rules:\n"
    "1. Quote the candidate and ground-truth verbatim in the rationale "
    "where possible. The rationale is what surfaces in the audit "
    "trail when an operator triages a `disagree`.\n"
    f"2. Set `schema_version` to {SCHEMA_VERSION!r} and `cluster_id` to "
    "the verbatim cluster_id from the input. Do NOT invent a new id.\n"
    "3. Keep the rationale concise. Do NOT write an essay — one or two "
    "sentences is enough. The output token budget is 1k.\n"
    "4. Be calibrated: not every imperfect candidate is `disagree`. "
    "If the eval set is balanced and you call everything `disagree` "
    "(or everything `agree`), your judgments are useless to the gate. "
    "Reach for `disagree` only when the candidate is materially wrong."
)


class ClusterLabelEvalError(Exception):
    """Base for all B3.5 eval-time errors."""


class ClusterLabelJudgmentValidationError(ClusterLabelEvalError):
    """Claude returned a tool input that failed ClusterLabelJudgment validation.

    Carries the raw input + the underlying pydantic error so the runner
    can retry on transient malformations without losing the diagnostic."""

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


class NoClusterLabelToolUseError(ClusterLabelEvalError):
    """Claude responded without calling the emit_cluster_label_judgment tool."""

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


class ClusterLabelIdMismatchError(ClusterLabelEvalError):
    """Generated judgment's cluster_id doesn't match the input case.

    The judge is supposed to copy the cluster_id verbatim. If it picks
    a different id, concurrent `runner.gather()` calls would correlate
    judgments to the wrong fixture — surface it as a typed error."""

    def __init__(
        self,
        message: str,
        *,
        judgment: ClusterLabelJudgment,
        expected_id: str,
    ) -> None:
        super().__init__(message)
        self.judgment = judgment
        self.expected_id = expected_id


def _build_tool_definition() -> dict[str, Any]:
    """Anthropic tool definition.

    `{"judgment": <ClusterLabelJudgment>}` wrapping mirrors A3.1/A3.2/
    A4.1/A4.2/A4.6 — small models nest deep object outputs under an
    outer field even when the schema is flat. `$defs` are hoisted to
    the input_schema root so any `$ref` pointers resolve correctly.
    """
    j_schema = ClusterLabelJudgment.model_json_schema()
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


def _format_user_message(case: LabeledClusterCase, candidate_label: str) -> str:
    """Assemble the judge's user message.

    Members go in as a bulleted list (one signature per bullet) — same
    shape the production `AnthropicLabeler` uses, so the judge sees the
    cluster the same way the labeler did."""
    member_block = "\n".join(f"- {sig}" for sig in case.member_signatures)
    return (
        "Here is the cluster to judge. Quote the candidate and ground-truth "
        "phrasing in your rationale.\n\n"
        f"cluster_id: {case.cluster_id}\n"
        f"domain_context: {case.domain_context}\n\n"
        f"Cluster members:\n{member_block}\n\n"
        f"ground_truth_label: {case.ground_truth_label!r}\n"
        f"candidate_label: {candidate_label!r}\n\n"
        "Decide: is candidate_label semantically equivalent to "
        "ground_truth_label given the members? Emit one "
        f"{TOOL_NAME} tool call with verdict + rationale + cluster_id."
    )


async def judge_label_match(
    client: AsyncAnthropic,
    case: LabeledClusterCase,
    candidate_label: str,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> ClusterLabelJudgment:
    """Judge one (case, candidate_label) pair and return the verdict.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible endpoint).
        case: The hand-labeled cluster fixture. Provides cluster_id,
            domain_context, member_signatures, ground_truth_label.
        candidate_label: The labeler's proposed name for the cluster.
            Sent verbatim — not normalized, not stripped, not truncated.
        model: Anthropic model id. Default sonnet 4.6 (separate from
            the haiku-4.5 labeler).
        max_tokens: Output cap. Default 1k (binary verdict + concise
            rationale fits with headroom).

    Returns:
        A validated `ClusterLabelJudgment` with cluster_id == case.cluster_id.

    Raises:
        NoClusterLabelToolUseError: Claude stopped without calling the tool.
        ClusterLabelJudgmentValidationError: Claude called the tool but the
            input failed `ClusterLabelJudgment.model_validate`.
        ClusterLabelIdMismatchError: Generated judgment's cluster_id doesn't
            match `case.cluster_id`.
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
                "content": _format_user_message(case, candidate_label),
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
        raise NoClusterLabelToolUseError(
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

    # Defensive parsing: A4.6's live smoke caught two opus-4.7 quirks
    # (string-wrapped payload + dimension-level schema_version leakage).
    # B3.5 has neither (one-level schema, sonnet not opus), but we keep
    # the JSON-string fallback because it's nearly free and would cost
    # us a re-run if the same quirk surfaces on sonnet under load.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            pass  # let model_validate raise the typed error below

    try:
        judgment = ClusterLabelJudgment.model_validate(payload)
    except ValidationError as exc:
        try:
            preview = json.dumps(raw_input)[:500]
        except (TypeError, ValueError):
            preview = repr(raw_input)[:500]
        raise ClusterLabelJudgmentValidationError(
            f"Tool input failed ClusterLabelJudgment validation: "
            f"{exc.error_count()} errors. Input preview: {preview}",
            raw_input=raw_input,
            pydantic_error=exc,
        ) from exc

    if judgment.cluster_id != case.cluster_id:
        raise ClusterLabelIdMismatchError(
            f"judgment.cluster_id={judgment.cluster_id!r} does not match "
            f"case.cluster_id={case.cluster_id!r} — judge invented an id; "
            f"concurrent runner.gather() would correlate to the wrong fixture",
            judgment=judgment,
            expected_id=case.cluster_id,
        )

    return judgment


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "ClusterLabelEvalError",
    "ClusterLabelJudgmentValidationError",
    "NoClusterLabelToolUseError",
    "ClusterLabelIdMismatchError",
    "judge_label_match",
]
