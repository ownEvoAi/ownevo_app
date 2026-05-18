"""WorkflowSpec → MetricDefinition via Anthropic tool-use (A4.2).

Mirrors `eval_generator.generate_eval_case_set` (A4.1):
single-turn `messages.create` with a forced `tool_choice`, the tool's
`input_schema` is `MetricDefinition.model_json_schema()` wrapped under
a `metric_definition` key, and the tool input is unwrapped + validated
back into a `MetricDefinition`.

Why a structured artifact (not free-form Python) is generated:

  * The Inspect AI integration (A4.3) wraps the result as a scorer,
    which requires a typed family + bounds + direction; a Python lambda
    would push the AST safety surface that `sim_render` solved onto the
    metric path too.
  * Direction lock is load-bearing — A3.1's `success_criterion.direction`
    decides whether the gate treats higher or lower as better; the
    metric must agree. The generator pre-flights `direction` agreement
    before validating, so a model that picks the wrong direction fails
    fast with a clear error instead of falling through to a generic
    pydantic validation.
  * The closed `MetricFamily` union means the generator's job is "pick
    a family + threshold + provenance" — small models do this reliably,
    while authoring per-workflow Python doesn't survive the
    devstral/qwen tier.

Lives in the `agent` extra (same as A3.1 / A3.2 / A4.1) — anthropic is
not a kernel-runtime dep.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .metric_def import SCHEMA_VERSION, MetricDefinition
from .spec import WorkflowSpec
from .workflow_spec_generator import NLGenError

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


DEFAULT_MODEL = os.environ.get("OWNEVO_NL_GEN_MODEL") or "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 4_000
"""Smaller than the eval-case generator's 12k — a metric definition is
one structured object, not a 10-30 element list."""

TOOL_NAME = "emit_metric_definition"
TOOL_DESCRIPTION = (
    "Emit a structured MetricDefinition for the workflow's success "
    "criterion. Pick one binary-classification family, a target threshold "
    "the regression gate scores against, and an explicit value range. "
    "Call this tool exactly once."
)


SYSTEM_PROMPT = (
    "You are ownEvo's NL-gen success-metric generator. You receive a "
    "WorkflowSpec and produce a MetricDefinition — the typed success "
    "metric the regression gate uses to decide whether a proposed skill "
    "change is an improvement.\n\n"
    "The metric is computed over a list of bool-labeled eval cases "
    "(A4.1's EvalCaseSet). Each case carries an `expected_value: bool` "
    "ground-truth label; the agent's behavior at replay produces an "
    "`actual_value: bool`. Your metric is one of a closed family of "
    "binary-classification scores over the (expected, actual) pairs.\n\n"
    "Rules:\n"
    "1. `family` MUST be one of: `pass_rate`, `precision`, `recall`, "
    "`f1`, `balanced_accuracy`, `specificity`. Pick based on the "
    "WorkflowSpec's `success_criterion.description` and "
    "`known_past_misses`:\n"
    "   - `recall` if missing positives is the documented past miss "
    "(e.g. 'we missed the spike').\n"
    "   - `precision` if false-firing is the documented past miss "
    "(e.g. 'too many false alerts').\n"
    "   - `f1` when both error modes are mentioned without a clear "
    "asymmetry — the safe default for composite criteria.\n"
    "   - `balanced_accuracy` when class imbalance would let "
    "`pass_rate` look good while one class is ignored.\n"
    "   - `specificity` when the negative class is the costly one to "
    "confuse (e.g. flagging clauses that are actually fine).\n"
    "   - `pass_rate` only when the spec explicitly frames success as "
    "raw accuracy on a balanced suite.\n"
    "2. `direction` MUST equal the WorkflowSpec's "
    "`success_criterion.direction`. All families above are naturally "
    "`maximize`; if the spec's direction is `minimize`, fail rather "
    "than emit a contradictory definition (the gate would silently "
    "treat regressions as wins).\n"
    "3. `lower_bound` MUST be `0.0` and `upper_bound` MUST be `1.0` — "
    "every supported family has range [0, 1].\n"
    "4. `target_value` MUST lie inside [`lower_bound`, `upper_bound`]. "
    "Pick a realistic threshold for an MVP-stage agent: 0.7-0.85 for "
    "`pass_rate`/`precision`/`recall`/`f1`/`balanced_accuracy`/"
    "`specificity`. Do not pick 1.0 (impossible to clear) or 0.0 "
    "(trivial to clear) — the gate needs both to be reachable and to "
    "have headroom.\n"
    "5. `name` MUST be kebab-case. Prefer the spec's "
    "`success_criterion.target_metric_name` when it's already kebab-"
    "case and accurate; rename to make the chosen family explicit if "
    "the original name was generic (e.g. `markdown-alert-recall` "
    "instead of `markdown_window_composite`).\n"
    "6. `description` is plain English the supply chain VP would read. "
    "Quote the success_criterion.description's framing where possible.\n"
    "7. `rationale` is one line explaining why this family fits this "
    "workflow — quote the past-miss phrase or name the asymmetric "
    "error cost. Surfaces in the audit trail next to the metric.\n"
    "8. `provenance.kind` is `\"derived\"` if a verbatim phrase from "
    "the spec's `success_criterion.description` or `known_past_misses` "
    "drove the family choice; `\"inferred\"` otherwise. "
    "`provenance.source` is that verbatim phrase, or a named domain "
    "pattern (e.g., `\"supply chain false-negative cost pattern\"`).\n"
    f"9. Set `schema_version` to {SCHEMA_VERSION!r}. Set "
    "`workflow_spec_id` to the WorkflowSpec's id verbatim."
)


class MetricDefinitionValidationError(NLGenError):
    """Claude returned a tool input that failed MetricDefinition validation."""

    def __init__(
        self, message: str, *, raw_input: Any, pydantic_error: ValidationError
    ):
        super().__init__(message)
        self.raw_input = raw_input
        self.pydantic_error = pydantic_error


class NoMetricToolUseError(NLGenError):
    """Claude responded without calling the emit_metric_definition tool."""

    def __init__(
        self, message: str, *, stop_reason: str | None, content_preview: str
    ):
        super().__init__(message)
        self.stop_reason = stop_reason
        self.content_preview = content_preview


class MetricDirectionMismatchError(NLGenError):
    """Generated metric's direction disagrees with the spec's success_criterion.

    Distinct from `MetricDefinitionValidationError` because the model
    produced a structurally valid MetricDefinition that violates the
    cross-spec invariant — the gate would silently treat regressions as
    wins. Surface this loudly so a regression in the model's prompt
    adherence shows up in CI, not in the audit log.
    """

    def __init__(
        self, message: str, *, definition: MetricDefinition, spec: WorkflowSpec
    ):
        super().__init__(message)
        self.definition = definition
        self.spec = spec


def _build_tool_definition() -> dict[str, Any]:
    """Anthropic tool definition.

    Same `{"metric_definition": <MetricDefinition>}` wrapping pattern as
    A3.1/A3.2/A4.1 — small models nest deep object outputs under an
    outer field even when the schema is flat. `$defs` are hoisted to
    the input_schema root so `$ref` pointers resolve correctly.
    """
    md_schema = MetricDefinition.model_json_schema()
    defs = md_schema.pop("$defs", {})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"metric_definition": md_schema},
        "required": ["metric_definition"],
    }
    if defs:
        input_schema["$defs"] = defs
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": input_schema,
    }


_TOOL_DEFINITION: dict[str, Any] = _build_tool_definition()
"""Computed once at import time — MetricDefinition schema is static."""


def _format_user_message(workflow_spec: WorkflowSpec) -> str:
    """Render the WorkflowSpec as the user message.

    Sent as JSON via `model_dump_json` (same pattern as
    `eval_generator._format_user_message`). The metric generator only
    needs the spec — eval cases are scored by the metric, not consumed
    when defining it.
    """
    spec_payload = workflow_spec.model_dump_json(indent=2)
    return (
        "Here is the WorkflowSpec to generate a success metric for. Read "
        "`success_criterion` (your direction MUST match it) and "
        "`known_past_misses` (the past-miss phrasing usually decides "
        "whether precision or recall is the right family).\n\n"
        f"WorkflowSpec:\n```json\n{spec_payload}\n```"
    )


async def generate_metric_definition(
    client: AsyncAnthropic,
    workflow_spec: WorkflowSpec,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> MetricDefinition:
    """Generate a typed MetricDefinition from a WorkflowSpec.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible endpoint).
        workflow_spec: The A3.1 artifact this metric is for.
        model: Anthropic model id. Default opus 4.7.
        max_tokens: Output cap. Default 4k — metric definitions are
            single structured objects.

    Returns:
        A validated `MetricDefinition`. Compute scores via
        `metric_compute.compute_metric`.

    Raises:
        NoMetricToolUseError: Claude stopped without calling the tool.
        MetricDefinitionValidationError: Claude called the tool but the
            input failed `MetricDefinition.model_validate`.
        MetricDirectionMismatchError: The generated metric's direction
            disagrees with `workflow_spec.success_criterion.direction`,
            or its `workflow_spec_id` doesn't match `workflow_spec.id`.
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
                "content": _format_user_message(workflow_spec),
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
        raise NoMetricToolUseError(
            f"Model {model} did not call {TOOL_NAME} (stop_reason={msg.stop_reason!r})",
            stop_reason=msg.stop_reason,
            content_preview=preview,
        )

    raw_input = tool_blocks[0].input
    if (
        isinstance(raw_input, dict)
        and "metric_definition" in raw_input
        and len(raw_input) == 1
    ):
        md_payload = raw_input["metric_definition"]
    else:
        md_payload = raw_input

    try:
        definition = MetricDefinition.model_validate(md_payload)
    except ValidationError as exc:
        try:
            preview = json.dumps(raw_input)[:500]
        except (TypeError, ValueError):
            preview = repr(raw_input)[:500]
        raise MetricDefinitionValidationError(
            f"Tool input failed MetricDefinition validation: {exc.error_count()} "
            f"errors. Input preview: {preview}",
            raw_input=raw_input,
            pydantic_error=exc,
        ) from exc

    if definition.workflow_spec_id != workflow_spec.id:
        raise MetricDirectionMismatchError(
            f"definition.workflow_spec_id={definition.workflow_spec_id!r} "
            f"does not match workflow_spec.id={workflow_spec.id!r}",
            definition=definition,
            spec=workflow_spec,
        )
    if definition.direction != workflow_spec.success_criterion.direction:
        raise MetricDirectionMismatchError(
            f"definition.direction={definition.direction!r} does not match "
            f"workflow_spec.success_criterion.direction="
            f"{workflow_spec.success_criterion.direction!r} — the gate "
            f"would silently treat regressions as improvements",
            definition=definition,
            spec=workflow_spec,
        )

    return definition


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "NoMetricToolUseError",
    "MetricDefinitionValidationError",
    "MetricDirectionMismatchError",
    "generate_metric_definition",
]
