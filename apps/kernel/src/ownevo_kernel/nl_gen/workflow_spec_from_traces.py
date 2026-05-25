"""Imported traces → WorkflowSpec — the trace-native generation entry.

The authoring path (`workflow_spec_generator.generate_workflow_spec`)
turns a plain-English description into a `WorkflowSpec`. The trace-import
path starts from a *running agent's traces* instead: the operator
connected an existing Copilot Studio / LangSmith / OTel agent, and we
have a `TraceSummary` of its observed behaviour plus (sometimes) the
agent's own exported definition.

This module produces the same typed `WorkflowSpec`, so every downstream
generator (sim / metric / eval) and the persistence path are reused
unchanged — they are spec-coupled, not description-coupled. Only the
seed differs: the LLM extracts the spec from observed tool calls /
outputs / failure modes rather than from prose the operator wrote.

It reuses the authoring generator's tool definition (the `WorkflowSpec`
JSON schema bound as `emit_workflow_spec`), validation-retry loop, and
error types — the structured-output contract is identical. Only the
system prompt and the user message change, because only the starting
material changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._validation_retry import (
    DEFAULT_MAX_RETRIES,
    NoToolUseSignal,
    RetryExhaustedError,
    call_with_validation_retry,
    truncate_for_error,
)
from .spec import SCHEMA_VERSION, WorkflowSpec
from .workflow_spec_generator import (
    _RETRY_FEEDBACK,
    _TOOL_DEFINITION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    TOOL_NAME,
    NoToolUseError,
    WorkflowSpecValidationError,
    _normalize_payload,
)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

    from ..design_agent.trace_summary import TraceSummary


_AGENT_DEFINITION_TRUNCATE = 4_000

SYSTEM_PROMPT = (
    "You are ownEvo's NL-gen workflow spec generator, running at "
    "trace-import time. Instead of a written description, you are given a "
    "summary of an agent that is ALREADY RUNNING — the tools it calls, "
    "sample arguments and outputs, observed failure modes — and sometimes "
    "the agent's own exported definition. You reverse-engineer the workflow "
    "the agent is implementing and emit it by calling the emit_workflow_spec "
    "tool exactly once.\n\n"
    "Read the observed behaviour as ground truth for what the agent DOES; "
    "read the agent definition (when present) as its stated intent, which "
    "may have drifted. When they conflict, model what the traces show and "
    "let the metric/eval stages surface the gap.\n\n"
    "Rules:\n"
    "1. Every tool, persona, env_generator, and data_source MUST carry a "
    "Provenance. Use `kind: \"derived\"` with `source` set to a verbatim "
    "observed signal — a tool name the traces show being called, or a "
    "quoted phrase from the agent definition — when the artifact comes "
    "directly from the import. Use `kind: \"inferred\"` with `source` set to "
    "a named domain pattern (e.g., \"supply chain forecasting domain "
    "pattern\") when you fill in something the import did not show.\n"
    "2. Map each observed tool call into `spec.tools` — the tool names and "
    "argument shapes in the summary are the most reliable signal you have "
    "about what the agent operates on.\n"
    "3. Turn observed failure modes into `known_past_misses` — each error "
    "mode the summary lists is a real failure this agent already produced. "
    "Quote it. Downstream stages turn these into eval cases.\n"
    "4. Pick UI primitives from the 8-variant set, choosing what fits the "
    "domain implied by the tools and outputs:\n"
    "   - tabular / forecasting: MetricCards + TimeSeriesChart + TableView + AlertList\n"
    "   - document / contract review: DocumentReader + SideBySideView + AlertList\n"
    "   - ticket / case flow: KanbanBoard + ConversationView + MetricCards\n"
    "   - portfolio / risk: MetricCards + TimeSeriesChart + TableView\n"
    "5. Distinguish `data_sources` (external systems with fixed schemas) "
    "from `env_generators` (synthetic data the simulator produces). They "
    "drive different downstream code generation.\n"
    "6. Distinguish `personas` (simulated users in the loop) from `reviewer` "
    "(the human who approves the agent's outputs). The reviewer shows up in "
    "the approval queue UI and the audit trail.\n"
    "7. The `success_criterion` is a stub — name a target metric, set "
    "direction, and describe what counts as a correct outcome based on what "
    "the agent appears to optimise for. A later step generates the full "
    "metric formula.\n"
    "8. Use kebab-case for `id` (lowercase letters, digits, dashes only).\n"
    f"9. Set `schema_version` to \"{SCHEMA_VERSION}\". Do not invent extra "
    "fields.\n"
    "10. **Provenance is ONLY allowed on `tool`, `persona`, `env_generator`, "
    "and `data_source` objects.** Do NOT add a `provenance` field to "
    "entities, `environment`, `success_criterion`, `ui`, or any other "
    "object. Pydantic rejects extra fields with `extra_forbidden`.\n"
    "11. **Tool `outputs[].type` MUST be one of these 7 literal strings:** "
    "`string`, `int`, `float`, `bool`, `date`, `datetime`, `category`. Do "
    "NOT use `array`, `list`, `object`, `dict`, `number`, `text`, or any "
    "other JSON-Schema type name. If a tool returns a list, model it as a "
    "single output with `type: \"string\"` and describe the list shape in "
    "the output's `description` field instead.\n"
    "12. The same `type` enum constraint applies to "
    "`environment.entities[].fields[].type` and any other `type` field in "
    "the spec — same 7 literals only."
)


def _build_user_message(
    *,
    summary: TraceSummary,
    agent_definition: str | None,
    design_brief_block: str | None,
) -> str:
    parts: list[str] = [
        "Reverse-engineer the WorkflowSpec for the imported agent described "
        "below, then call emit_workflow_spec exactly once.",
        summary.as_prompt_text(),
    ]
    if agent_definition and agent_definition.strip():
        definition = agent_definition.strip()[:_AGENT_DEFINITION_TRUNCATE]
        parts.append(
            "## Imported agent definition (stated intent)\n"
            f"```\n{definition}\n```"
        )
    if design_brief_block:
        parts.append(design_brief_block)
    return "\n\n".join(parts)


async def generate_workflow_spec_from_traces(
    client: AsyncAnthropic,
    summary: TraceSummary,
    *,
    agent_definition: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    design_brief_block: str | None = None,
) -> WorkflowSpec:
    """Reverse-engineer a typed WorkflowSpec from imported agent traces.

    Args:
        client: An AsyncAnthropic client (any /v1/messages-compatible endpoint).
        summary: Deterministic `TraceSummary` of the imported traces.
        agent_definition: The agent's own exported definition / prompt, if
            the source platform provided one. Treated as stated intent.
        model: Anthropic model id. Default opus 4.7.
        max_tokens: Output cap. Default matches the authoring generator.
        max_retries: Retries on `ValidationError`, sending pydantic errors
            back as a tool_result so the model can correct.
        design_brief_block: Pre-formatted trace-import interview answers
            (from `design_brief_context.format_dimensions_block(...,
            SPEC_DIMENSIONS)`). When set, the operator's negotiated answers
            are treated as hard constraints over the trace-derived shape.

    Returns:
        A validated `WorkflowSpec`.

    Raises:
        NoToolUseError: Claude stopped without calling emit_workflow_spec.
        WorkflowSpecValidationError: All attempts produced tool inputs that
            failed `WorkflowSpec.model_validate`.
    """
    user_message = _build_user_message(
        summary=summary,
        agent_definition=agent_definition,
        design_brief_block=design_brief_block,
    )
    try:
        spec, _raw = await call_with_validation_retry(
            client=client,
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tool_definition=_TOOL_DEFINITION,
            tool_name=TOOL_NAME,
            initial_user_message=user_message,
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
    "NoToolUseError",
    "SYSTEM_PROMPT",
    "WorkflowSpecValidationError",
    "generate_workflow_spec_from_traces",
]
