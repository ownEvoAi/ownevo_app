"""W6 demo loop — instruction-edit proposer (NL-gen agent guidance).

Per cycle of `nl_gen.loop.run_nl_gen_demo_loop`, the orchestrator hands
the dominant failure cluster to this module and gets back a 2-5 sentence
guidance addendum to inject into the next cycle's user message. The
agent is a stateless classifier — there's nothing structural to "edit"
between cycles — but its decision boundary moves when its per-call
context names the failure pattern explicitly. This proposer is the
write-half of that loop.

Mirrors the A4.6 / W5.2 pattern: single-turn Anthropic forced-tool-use,
frozen Pydantic schema, typed errors, JSON-string-wrapped payload
defensive recovery (the live-smoke quirk caught on opus 4.7 in A4.6).

Lives in the `agent` extra (anthropic dep). The schema is kernel-runtime
so the orchestrator + audit log can serialize InstructionEdit rows
without pulling anthropic in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .metric_def import MetricDefinition
from .spec import WorkflowSpec
from .workflow_spec_generator import NLGenError

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


SCHEMA_VERSION = "0.1"
"""`0.1` until W6 closes; bumps to 1.0 with the schema-freeze ritual."""

DEFAULT_MODEL = "claude-sonnet-4-6"
"""Sonnet 4.6 by default — the proposer is a 2-5 sentence write task,
opus is overkill. Sonnet's prompt-cache hit rate matters more here than
raw quality (the orchestrator will call this once per cycle)."""

DEFAULT_MAX_TOKENS = 1_500
"""Plenty for a few-sentence rationale + appended_text; well below the
sonnet 4.6 default 8k cap."""


# ---------------------------------------------------------------------------
# Failure example — orchestrator → proposer payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureExample:
    """One representative failure surfaced from the cluster.

    The orchestrator picks ≤5 of these from the dominant cluster (largest
    `cluster.size`) and hands them to the proposer. They give the LLM
    enough texture to write a guidance addendum that names the pattern
    without inventing details — `direction` (false-positive vs
    false-negative) is the load-bearing field; `provenance_kind` and
    `is_test_fold` matter only for debugging.

    `text_signature` is the same one-line embedding string the W5.3
    clustering pipeline used; the proposer reads it as-is.
    """

    case_id: str
    direction: Literal["false-positive", "false-negative"]
    provenance_kind: Literal["derived", "inferred"]
    is_test_fold: bool
    text_signature: str


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------


class InstructionEdit(BaseModel):
    """Frozen output of the instruction-edit proposer.

    `appended_text` is the only field the agent solver consumes — it
    becomes the `per_workflow_instruction` block in the next cycle's
    user message. `cluster_label` and `rationale` are audit-trail
    payload: they answer "why did the agent's guidance change between
    cycle N and N+1?" without re-running the proposer.

    Length budgets:
      * `cluster_label` ≤ 120 chars — short enough to fit on a UI card.
      * `rationale` ≤ 400 chars — one or two sentences.
      * `appended_text` ≤ 1,000 chars — 2-5 sentences. Long enough to
        teach a rule; short enough that the cumulative addendum across
        N cycles doesn't crowd out the trajectory in the user message.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster_label: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=400)
    appended_text: str = Field(min_length=1, max_length=1_000)
    schema_version: str = Field(default=SCHEMA_VERSION, pattern=r"^\d+\.\d+$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InstructionProposerError(NLGenError):
    """Base: proposer-level failure (API, validation, malformed payload)."""


class InstructionEditValidationError(InstructionProposerError):
    """Tool fired, but the input failed `InstructionEdit.model_validate`."""

    def __init__(
        self,
        message: str,
        *,
        raw_input: Any,
        pydantic_error: ValidationError | None = None,
    ):
        super().__init__(message)
        self.raw_input = raw_input
        self.pydantic_error = pydantic_error


class NoInstructionEditToolUseError(InstructionProposerError):
    """Model ended its turn without calling the proposer tool."""

    def __init__(
        self, message: str, *, stop_reason: str | None, content_preview: str
    ):
        super().__init__(message)
        self.stop_reason = stop_reason
        self.content_preview = content_preview


# ---------------------------------------------------------------------------
# Tool definition + system prompt
# ---------------------------------------------------------------------------


TOOL_NAME = "propose_instruction_edit"
TOOL_DESCRIPTION = (
    "Emit a structured InstructionEdit naming the cluster, why the "
    "addendum is appropriate, and the 2-5 sentence guidance the agent "
    "should consult on its next pass through this workflow's eval cases."
)


SYSTEM_PROMPT = (
    "You are ownEvo's instruction-edit proposer. You're the write-half "
    "of the W6 NL-gen demo loop: each cycle, an LLM agent classifier "
    "predicts True/False on a workflow's eval cases; the failures "
    "cluster; the dominant cluster lands on your desk. Your job is to "
    "write a short guidance addendum that, when injected into the "
    "agent's per-case context on the next cycle, helps it avoid the "
    "cluster's failure mode.\n\n"
    "What you receive:\n"
    "  * The workflow's spec (id, domain, description, success criterion).\n"
    "  * The gate metric (family, direction, target).\n"
    "  * The dominant failure cluster's label + size.\n"
    "  * Up to 5 representative failure examples (case_id, direction, "
    "    provenance, fold, text_signature).\n"
    "  * The current guidance addendum (or None on cycle 1).\n\n"
    "Rules:\n"
    "1. Call `propose_instruction_edit` exactly once.\n"
    "2. `appended_text` is what the agent will read on its next pass. "
    "Write it in second person ('When the trajectory shows X, lean "
    "toward Y') so the agent reads it as a directive, not a description "
    "of past errors.\n"
    "3. Name the failure pattern concretely. 'Pay closer attention to "
    "winter weeks' beats 'be more careful'. The text_signature of a "
    "representative case is your best source of concrete vocabulary.\n"
    "4. Respect the metric's asymmetry. A `recall`-gated workflow "
    "penalizes false-negatives — your edit should push toward True "
    "under uncertainty if the cluster is dominated by false-negatives. "
    "A `precision`-gated workflow is the mirror.\n"
    "5. Build on the current guidance — don't undo prior cycles. If a "
    "current addendum exists, your `appended_text` SHOULD layer on top "
    "of it (the orchestrator concatenates), not replace its rules.\n"
    "6. Stay within budgets: cluster_label ≤120 chars, rationale ≤400, "
    "appended_text ≤1,000 chars (~2-5 sentences). Concise edits "
    "outperform verbose ones — the agent has limited context budget."
)


# ---------------------------------------------------------------------------
# Tool definition (Anthropic format) — built lazily so model_json_schema
# doesn't fire at import time
# ---------------------------------------------------------------------------


def _build_tool_definition() -> dict[str, Any]:
    schema = dict(InstructionEdit.model_json_schema())
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {"edit": schema},
            "required": ["edit"],
            "additionalProperties": False,
        },
    }


# ---------------------------------------------------------------------------
# User-message assembly
# ---------------------------------------------------------------------------


def _format_failure_examples(examples: list[FailureExample]) -> str:
    if not examples:
        return "  (no representative failures provided)"
    lines = []
    for ex in examples:
        fold = "test" if ex.is_test_fold else "train"
        lines.append(
            f"  - {ex.case_id} · {ex.direction} · {ex.provenance_kind}-miss · {fold}\n"
            f"    signature: {ex.text_signature}"
        )
    return "\n".join(lines)


def _format_user_message(
    *,
    spec: WorkflowSpec,
    metric: MetricDefinition,
    cluster_label: str,
    cluster_size: int,
    failure_examples: list[FailureExample],
    current_instruction: str | None,
) -> str:
    current_block = (
        current_instruction.strip()
        if current_instruction and current_instruction.strip()
        else "(none — cycle 1; you're authoring the first addendum)"
    )
    success_desc = (spec.success_criterion.description or "").strip()
    return (
        f"## Workflow\n"
        f"id: `{spec.id}`  ·  domain: `{spec.domain}`\n"
        f"success criterion: {success_desc}\n\n"
        f"## Gate metric\n"
        f"family: `{metric.family}`  ·  direction: `{metric.direction}`  "
        f"·  target: {metric.target_value:.2f}\n\n"
        f"## Dominant failure cluster\n"
        f"label: {cluster_label}\n"
        f"size: {cluster_size} failure(s)\n\n"
        f"## Representative failures\n"
        f"{_format_failure_examples(failure_examples)}\n\n"
        f"## Current guidance addendum\n"
        f"{current_block}\n\n"
        f"## Task\n"
        f"Call `propose_instruction_edit` with a 2-5 sentence "
        f"`appended_text` that helps the agent avoid this cluster's "
        f"failure mode on the next cycle. Stay concise; respect the "
        f"metric asymmetry; build on (don't replace) the current "
        f"addendum."
    )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


async def propose_instruction_edit(
    client: "AsyncAnthropic",
    *,
    spec: WorkflowSpec,
    metric: MetricDefinition,
    cluster_label: str,
    cluster_size: int,
    failure_examples: list[FailureExample],
    current_instruction: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> InstructionEdit:
    """Propose a guidance addendum for the next demo-loop cycle.

    Args:
        client: AsyncAnthropic client (any /v1/messages-compatible endpoint).
        spec: WorkflowSpec the agent is solving — drives domain framing.
        metric: A4.2 MetricDefinition — locks the asymmetry the proposer
            should respect.
        cluster_label: Human-readable label for the dominant cluster
            (the W5.3 / B3 labeler's output).
        cluster_size: How many failures the cluster contains. Surfaces
            in the rationale so the proposer can scale conviction.
        failure_examples: Up to 5 representative failures from the
            cluster. The orchestrator picks them; this function does
            not re-rank.
        current_instruction: The cumulative guidance from prior cycles
            (or None on cycle 1). The proposer SHOULD build on it.
        model: Anthropic model id. Default sonnet 4.6.
        max_tokens: Output cap. Default 1.5k.

    Returns:
        Validated `InstructionEdit`.

    Raises:
        NoInstructionEditToolUseError: model didn't call the tool.
        InstructionEditValidationError: tool fired with invalid input.
    """
    user_message = _format_user_message(
        spec=spec,
        metric=metric,
        cluster_label=cluster_label,
        cluster_size=cluster_size,
        failure_examples=failure_examples,
        current_instruction=current_instruction,
    )

    msg = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        tools=[_build_tool_definition()],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": user_message}],
    )

    tool_blocks = [
        b for b in msg.content
        if getattr(b, "type", None) == "tool_use"
        and getattr(b, "name", None) == TOOL_NAME
    ]
    if not tool_blocks:
        text_blocks = [b for b in msg.content if getattr(b, "type", None) == "text"]
        preview = (text_blocks[0].text if text_blocks else "")[:300]
        raise NoInstructionEditToolUseError(
            f"Model {model} did not call {TOOL_NAME} "
            f"(stop_reason={msg.stop_reason!r})",
            stop_reason=msg.stop_reason,
            content_preview=preview,
        )

    raw_input = tool_blocks[0].input
    # Mirror A4.6's wrapped-payload + JSON-string-fallback recovery: opus
    # 4.7 sometimes returns the payload as a JSON-encoded string under the
    # `edit` key. Sonnet 4.6 hasn't shown the quirk but the recovery is
    # near-free.
    if (
        isinstance(raw_input, dict)
        and "edit" in raw_input
        and len(raw_input) == 1
    ):
        payload = raw_input["edit"]
    else:
        payload = raw_input

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            pass  # let model_validate raise the typed error below

    try:
        return InstructionEdit.model_validate(payload)
    except ValidationError as exc:
        try:
            preview = json.dumps(raw_input)[:500]
        except (TypeError, ValueError):
            preview = repr(raw_input)[:500]
        raise InstructionEditValidationError(
            f"Tool input failed InstructionEdit validation: "
            f"{exc.error_count()} errors. Input preview: {preview}",
            raw_input=raw_input,
            pydantic_error=exc,
        ) from exc


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "FailureExample",
    "InstructionEdit",
    "InstructionEditValidationError",
    "InstructionProposerError",
    "NoInstructionEditToolUseError",
    "SCHEMA_VERSION",
    "SYSTEM_PROMPT",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "propose_instruction_edit",
]
