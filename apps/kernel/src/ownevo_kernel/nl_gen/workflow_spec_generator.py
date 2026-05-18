"""NL → WorkflowSpec — Anthropic tool-use call producing the A3.1 artifact.

Single-turn, structured-output via tool-use: we bind the JSON schema of
`WorkflowSpec` as the tool's `input_schema` and force `tool_choice` so Claude
must emit a tool_use block whose input parses back into a `WorkflowSpec`.

Why tool-use over JSON-mode-in-text:
  * Anthropic's tool input_schema is the well-tested structured-output path
    (matches the 8-tool surface our M5 agent uses).
  * `tool_choice={"type": "tool", "name": ...}` is a hard contract — Claude
    cannot answer in plain text.
  * Validation lands as `WorkflowSpecValidationError` rather than free-text
    parsing.

Lives in the `agent` extra (same as the M5 middleware adapter) — anthropic is
not a kernel-runtime dep.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .spec import SCHEMA_VERSION, WorkflowSpec

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


DEFAULT_MODEL = os.environ.get("OWNEVO_NL_GEN_MODEL") or "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 16_000

TOOL_NAME = "emit_workflow_spec"
TOOL_DESCRIPTION = (
    "Emit a structured WorkflowSpec extracted from the user's plain-English "
    "workflow description. Call this tool exactly once with the complete spec."
)

SYSTEM_PROMPT = (
    "You are ownEvo's NL-gen workflow spec generator. The user gives you a "
    "plain-English description of a workflow they want their agents to run. "
    "You extract the structure and emit it by calling the emit_workflow_spec "
    "tool exactly once.\n\n"
    "Rules:\n"
    "1. Every tool, persona, env_generator, and data_source MUST carry a "
    "Provenance. Use `kind: \"derived\"` with `source` set to a verbatim "
    "quoted phrase from the description when the artifact comes directly "
    "from something the user said. Use `kind: \"inferred\"` with `source` "
    "set to a named domain pattern (e.g., \"supply chain forecasting domain "
    "pattern\") when you're filling in something the user did not name.\n"
    "2. Extract every phrase the user mentions as a known failure mode "
    "into `known_past_misses` — phrases like \"we missed X\", \"we "
    "underweighted Y\", \"past misses: ...\". Quote the phrase verbatim. "
    "Downstream stages turn these into eval cases.\n"
    "3. Pick UI primitives from the 8-variant set, choosing what fits the "
    "domain:\n"
    "   - tabular / forecasting: MetricCards + TimeSeriesChart + TableView + AlertList\n"
    "   - document / contract review: DocumentReader + SideBySideView + AlertList\n"
    "   - ticket / case flow: KanbanBoard + ConversationView + MetricCards\n"
    "   - portfolio / risk: MetricCards + TimeSeriesChart + TableView\n"
    "4. Distinguish `data_sources` (external systems with fixed schemas — "
    "SAP, NOAA, Salesforce) from `env_generators` (synthetic data the "
    "simulator produces — synthetic catalogs, supplier behaviour, weather "
    "anomalies). They drive different downstream code generation.\n"
    "5. Distinguish `personas` (simulated users in the loop) from "
    "`reviewer` (the human who approves the agent's outputs). The reviewer "
    "shows up in the approval queue UI and the audit trail.\n"
    "6. The `success_criterion` is a stub — name a target metric, set "
    "direction, and describe what counts as a correct outcome in the "
    "user's words. A later step generates the full metric formula.\n"
    "7. Use kebab-case for `id` (lowercase letters, digits, dashes only).\n"
    f"8. Set `schema_version` to \"{SCHEMA_VERSION}\". Do not invent extra fields.\n"
    "9. **Provenance is ONLY allowed on `tool`, `persona`, `env_generator`, and "
    "`data_source` objects.** Do NOT add a `provenance` field to entities, "
    "`environment`, `success_criterion`, `ui`, or any other object. Pydantic "
    "rejects extra fields with `extra_forbidden`.\n"
    "10. **Tool `outputs[].type` MUST be one of these 7 literal strings:** "
    "`string`, `int`, `float`, `bool`, `date`, `datetime`, `category`. "
    "Do NOT use `array`, `list`, `object`, `dict`, `number`, `text`, or any "
    "other JSON-Schema type name. If a tool returns a list, model it as a "
    "single output with `type: \"string\"` and describe the list shape in "
    "the output's `description` field instead.\n"
    "11. The same `type` enum constraint applies to "
    "`environment.entities[].fields[].type` and any other `type` field "
    "in the spec — same 7 literals only."
)


class NLGenError(Exception):
    """Base for nl_gen errors."""


class WorkflowSpecValidationError(NLGenError):
    """Claude returned a tool input that failed WorkflowSpec validation."""

    def __init__(self, message: str, *, raw_input: Any, pydantic_error: ValidationError):
        super().__init__(message)
        self.raw_input = raw_input
        self.pydantic_error = pydantic_error


class NoToolUseError(NLGenError):
    """Claude responded without calling the emit_workflow_spec tool."""

    def __init__(self, message: str, *, stop_reason: str | None, content_preview: str):
        super().__init__(message)
        self.stop_reason = stop_reason
        self.content_preview = content_preview


def _build_tool_definition() -> dict[str, Any]:
    """Anthropic tool definition.

    Wraps `WorkflowSpec` under a `spec` parameter rather than inlining its
    schema as the tool's top-level input. Smaller models (Haiku 4.5 in
    particular) tend to nest deep object outputs under an outer field even
    when the schema is flat, producing `{"spec": {...}}` instead of `{...}`.
    Explicit wrapping matches that behavior so structured output is robust
    across the model tier; larger models accept the wrapper without
    issue. We unwrap in `generate_workflow_spec` before validating.

    `$defs` from the spec schema are hoisted to the `input_schema` root so
    that `$ref: "#/$defs/..."` pointers resolve correctly — JSON Schema `#/`
    refs resolve against the document root (`input_schema`), not the embedded
    `spec` sub-schema where Pydantic originally placed them.
    """
    spec_schema = WorkflowSpec.model_json_schema()
    defs = spec_schema.pop("$defs", {})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"spec": spec_schema},
        "required": ["spec"],
    }
    if defs:
        input_schema["$defs"] = defs
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": input_schema,
    }


_TOOL_DEFINITION: dict[str, Any] = _build_tool_definition()
"""Computed once at import time — WorkflowSpec schema is static."""


async def generate_workflow_spec(
    client: AsyncAnthropic,
    description: str,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> WorkflowSpec:
    """Generate a typed WorkflowSpec from a plain-English description.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible endpoint).
        description: The user's plain-English workflow description.
        model: Anthropic model id. Default opus 4.7.
        max_tokens: Output cap. Default 16k — enough for a full spec across
            the 3 fixtures we ship (~6-8k tokens of structured output).

    Returns:
        A validated `WorkflowSpec`.

    Raises:
        NoToolUseError: Claude stopped without calling emit_workflow_spec.
        WorkflowSpecValidationError: Claude called the tool but the input
            failed `WorkflowSpec.model_validate`.
    """
    msg = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        tools=[_TOOL_DEFINITION],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": description}],
    )

    tool_blocks = [
        b for b in msg.content
        if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == TOOL_NAME
    ]
    if not tool_blocks:
        text_blocks = [b for b in msg.content if getattr(b, "type", None) == "text"]
        preview = (text_blocks[0].text if text_blocks else "")[:300]
        raise NoToolUseError(
            f"Model {model} did not call {TOOL_NAME} (stop_reason={msg.stop_reason!r})",
            stop_reason=msg.stop_reason,
            content_preview=preview,
        )

    raw_input = tool_blocks[0].input
    # Tool param is `spec`; some models (rarely) emit the spec un-wrapped at
    # the top level — accept either shape so the schema-freeze is robust.
    if isinstance(raw_input, dict) and "spec" in raw_input and len(raw_input) == 1:
        spec_payload = raw_input["spec"]
    else:
        spec_payload = raw_input
    try:
        return WorkflowSpec.model_validate(spec_payload)
    except ValidationError as exc:
        # Truncate the raw input in the error message so logs stay readable
        # but keep the full payload on the exception for debugging.
        preview = json.dumps(raw_input)[:500]
        raise WorkflowSpecValidationError(
            f"Tool input failed WorkflowSpec validation: {exc.error_count()} "
            f"errors. Input preview: {preview}",
            raw_input=raw_input,
            pydantic_error=exc,
        ) from exc


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "NLGenError",
    "WorkflowSpecValidationError",
    "NoToolUseError",
    "generate_workflow_spec",
]
