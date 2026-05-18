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

import os
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ._validation_retry import (
    DEFAULT_MAX_RETRIES,
    NoToolUseSignal,
    RetryExhaustedError,
    call_with_validation_retry,
    truncate_for_error,
)
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


def _normalize_payload(payload: Any) -> Any:
    """Force-overwrite fields where local models' training priors
    consistently violate the schema.

    Observed on `qwen/qwen3.6-35b-a3b`: the model emits
    `schema_version: "1.1"` regardless of the system prompt ("Set
    schema_version to {SCHEMA_VERSION!r}"), the retry feedback, and
    five attempts of correction. The training prior is stronger than
    any in-prompt signal we've tried. Override it transparently rather
    than burning retries on a value the model won't yield on.
    Cloud frontier models already emit the correct value, so this
    is a no-op for them.
    """
    if isinstance(payload, dict) and "schema_version" in payload:
        return {**payload, "schema_version": SCHEMA_VERSION}
    return payload


_RETRY_FEEDBACK = (
    "Reminders from the system prompt:\n"
    f"- `schema_version` MUST be the exact literal \"{SCHEMA_VERSION}\".\n"
    "- `provenance` is ONLY allowed on `tool`, `persona`, `env_generator`, "
    "and `data_source` objects. Remove it from anywhere else (entities, "
    "environment, success_criterion, ui).\n"
    "- `type` fields on tool outputs and entity fields MUST be one of: "
    "`string`, `int`, `float`, `bool`, `date`, `datetime`, `category`. "
    "Reject `array`, `list`, `object`, `dict`, `number`, `text`, `entity`, "
    "etc.\n"
    "- Do not invent extra fields. Pydantic rejects unknown keys with "
    "`extra_forbidden`."
)
"""Domain-specific rules echoed into the tool_result on validation
retry. Restating the system-prompt constraints next to the concrete
errors is what gets local-LLM proposers to actually correct their
output — see `_validation_retry.py`."""


async def generate_workflow_spec(
    client: AsyncAnthropic,
    description: str,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> WorkflowSpec:
    """Generate a typed WorkflowSpec from a plain-English description.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible endpoint).
        description: The user's plain-English workflow description.
        model: Anthropic model id. Default opus 4.7.
        max_tokens: Output cap. Default 16k — enough for a full spec across
            the 3 fixtures we ship (~6-8k tokens of structured output).
        max_retries: On `ValidationError`, retry up to this many times,
            sending the pydantic errors back as a `tool_result` so the
            model can correct. Default 4 (= 5 attempts total). Cloud
            models pass on attempt 1; local models benefit from retries.

    Returns:
        A validated `WorkflowSpec`.

    Raises:
        NoToolUseError: Claude stopped without calling emit_workflow_spec.
        WorkflowSpecValidationError: All attempts produced tool inputs
            that failed `WorkflowSpec.model_validate`.
    """
    try:
        spec, _raw = await call_with_validation_retry(
            client=client,
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tool_definition=_TOOL_DEFINITION,
            tool_name=TOOL_NAME,
            initial_user_message=description,
            schema_class=WorkflowSpec,
            envelope_key="spec",
            max_retries=max_retries,
            extra_feedback=_RETRY_FEEDBACK,
            normalize=_normalize_payload,
        )
        return spec
    except NoToolUseSignal as exc:
        raise NoToolUseError(
            f"Model {model} did not call {TOOL_NAME} (stop_reason={exc.stop_reason!r})",
            stop_reason=exc.stop_reason,
            content_preview=exc.content_preview,
        ) from exc
    except RetryExhaustedError as exc:
        preview = truncate_for_error(exc.raw_input)
        raise WorkflowSpecValidationError(
            f"Tool input failed WorkflowSpec validation after {exc.attempts} "
            f"attempts: {exc.pydantic_error.error_count()} errors. "
            f"Input preview: {preview}",
            raw_input=exc.raw_input,
            pydantic_error=exc.pydantic_error,
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
