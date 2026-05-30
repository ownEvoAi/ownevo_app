"""LLM-driven design-agent interviewer for the trace-import entry point.

The authoring-time interviewer (`interviewer.pick_next_question`) starts
from a free-text description the operator wrote. The trace-import
interviewer starts from a *running agent's traces* — the operator
imported an existing Copilot Studio / LangSmith / OTel agent and ownEvo
already has a `TraceSummary` of what it does.

The decision surface is identical: the same seven `DesignDimension`s
still have to be covered before the kernel can generate a faithful
WorkflowSpec / SimulationPlan / MetricDefinition. What changes is the
conversational stance — observational ("your traces show this agent
calls `forecast_demand` then `flag_stockout_risk`; what should success
look like?") rather than interrogative ("describe the workflow").

This module reuses the authoring interviewer's tool schema
(`ASK_QUESTION_TOOL`), structured-output contract (`QuestionBrief`),
dimension coverage logic, and validation wholesale — only the system
prompt and the user-message builder differ, because only the starting
material differs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from .dimensions import DimensionSpec, dimensions_remaining, spec_for
from .interviewer import (
    _VIEW_VOCABULARY,
    ASK_QUESTION_TOOL,
    DEFAULT_INTERVIEWER_MODEL,
    DEFAULT_MAX_TOKENS,
    InterviewerError,
    PriorAnswer,
    QuestionBrief,
    _is_anthropic_client,
)
from .trace_summary import TraceSummary

# Imported agent definitions can be long (a full Copilot Studio Solutions
# export or a LangSmith prompt). Cap what we inline so a pathological
# import doesn't blow the context window; the trace summary already
# carries the behavioural picture.
_AGENT_DEFINITION_TRUNCATE = 4_000


SYSTEM_PROMPT = (
    "You are ownEvo's design-agent interviewer, running at the moment a "
    "domain expert (a supply-chain VP, chief risk officer, labour-relations "
    "counsel, or similar) connects an agent that is ALREADY RUNNING — its "
    "traces have just been imported. Your job is to define the WorkflowSpec "
    "/ SimulationPlan / EvalCaseSet / MetricDefinition for that agent before "
    "the improvement loop attaches to it. You are NOT a chatbot; you are a "
    "decision facilitator.\n\n"
    "What makes this different from a from-scratch interview: you can SEE "
    "what the agent does. The user message gives you a summary of the "
    "imported traces — the tools it calls, sample arguments and outputs, and "
    "any failure modes — plus (sometimes) the agent's own exported "
    "definition. Ground every question in that evidence. Open observationally "
    "('your traces show the agent calls X on Y'), then ask what the operator "
    "wants — the trace shows what the agent DID, never what 'done' was "
    "supposed to mean.\n\n"
    "Your one job per turn: pick the highest-leverage open dimension and "
    "produce a single decision brief on it. Use the `ask_question` tool "
    "exactly once. Never call it twice; never reply in prose.\n\n"
    "Tone:\n"
    "- Direct. Never sycophantic. Take a position on every recommendation.\n"
    "- Evidence-grounded. Reference the observed tools / outputs / errors "
    "verbatim where possible. Generic 'consider both options' answers are "
    "forbidden.\n"
    "- Domain-aware. Match vocabulary to what the trace reveals about the "
    "domain (forecasting, credit risk, clinical trials, contract review).\n"
    "- Honest about tradeoffs. Every option has at least one downside.\n\n"
    "Hard rules:\n"
    "1. Target a dimension from the OPEN list the user message provides. "
    "Closed dimensions are off-limits.\n"
    "2. Options must be mutually exclusive. 'Yes / no / depends' is almost "
    "always wrong — replace 'depends' with a real third option.\n"
    "3. Recommendation must point at a real option index. The rationale must "
    "reference the observed trace behaviour, the agent definition, or a prior "
    "answer — never a generic best practice.\n"
    "4. Stakes line must name a concrete downstream failure mode, not a "
    "vague risk."
)


def _build_user_message(
    *,
    summary: TraceSummary,
    agent_definition: str | None,
    prior_answers: Sequence[PriorAnswer],
    open_dimensions: tuple[DimensionSpec, ...],
) -> str:
    parts: list[str] = [summary.as_prompt_text()]

    if agent_definition and agent_definition.strip():
        definition = agent_definition.strip()[:_AGENT_DEFINITION_TRUNCATE]
        parts.append(
            "## Imported agent definition\n"
            "The source platform also exported the agent's own definition "
            "(instructions / prompt / Solutions export). Treat it as the "
            "agent's stated intent — which may have drifted from what the "
            "traces show.\n"
            f"```\n{definition}\n```"
        )

    if prior_answers:
        lines = []
        for i, pa in enumerate(prior_answers, 1):
            spec = spec_for(pa.dimension) if pa.dimension else None
            dim_label = (spec.label if spec else pa.dimension) or "(no dimension)"
            if pa.is_skip():
                ans = "[skipped]"
            else:
                bits: list[str] = []
                if pa.chosen_option:
                    bits.append(f"chose: {pa.chosen_option}")
                if pa.free_text and pa.free_text.strip():
                    bits.append(f"elaboration: {pa.free_text.strip()}")
                ans = " · ".join(bits) if bits else "[empty]"
            lines.append(f"  {i}. [{dim_label}] Q: {pa.question}\n     A: {ans}")
        parts.append("## Prior Q&A trail\n" + "\n".join(lines))
    else:
        parts.append("## Prior Q&A trail\n(none — this is the first question)")

    parts.append("## Open dimensions (pick exactly one)")
    open_lines = [f"- `{d.key}` ({d.label}): {d.intent}" for d in open_dimensions]
    parts.append("\n".join(open_lines))

    views = "\n".join(f"  - {name}: {hint}" for name, hint in _VIEW_VOCABULARY)
    parts.append(
        "## Operate-UI view vocabulary (only relevant for the "
        "`operate_ui_primitives` dimension)\n" + views
    )

    parts.append(
        "## Your task\nCall `ask_question` exactly once. Target ONE open "
        "dimension. Open the question observationally from the trace evidence "
        "above, then offer 2-4 mutually-exclusive options with concrete "
        "labels, a recommendation, and a rationale grounded in what the "
        "traces (or the agent definition) actually show."
    )
    return "\n\n".join(parts)


async def pick_next_question_from_traces(
    *,
    summary: TraceSummary,
    agent_definition: str | None,
    prior_answers: Sequence[PriorAnswer],
    client: Any | None = None,
    model: str = DEFAULT_INTERVIEWER_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> QuestionBrief | None:
    """Pick the next trace-import question, or None when the interview is done.

    Mirrors `interviewer.pick_next_question`, but the LLM sees a
    `TraceSummary` + optional agent definition instead of a free-text
    description. Returns `None` once every dimension is covered. Raises
    `InterviewerError` on LLM failure or malformed output — callers
    should catch and fall through to the static trace-import prompt set.
    """
    if client is None or not _is_anthropic_client(client):
        raise InterviewerError(
            "No Anthropic client provided; the interviewer cannot run."
        )

    covered: set[str] = {pa.dimension for pa in prior_answers if pa.dimension}
    open_dims = dimensions_remaining(covered)
    if not open_dims:
        return None

    user_message = _build_user_message(
        summary=summary,
        agent_definition=agent_definition,
        prior_answers=prior_answers,
        open_dimensions=open_dims,
    )

    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tools=[ASK_QUESTION_TOOL],
            tool_choice={"type": "tool", "name": "ask_question"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        raise InterviewerError(
            f"trace-import interviewer LLM call failed: {type(exc).__name__}: {exc}"
        ) from exc

    tool_blocks = [
        b
        for b in getattr(msg, "content", [])
        if getattr(b, "type", None) == "tool_use"
        and getattr(b, "name", None) == "ask_question"
    ]
    if not tool_blocks:
        raise InterviewerError(
            "trace-import interviewer LLM did not call ask_question — "
            f"stop_reason={getattr(msg, 'stop_reason', None)!r}"
        )

    raw = tool_blocks[0].input
    if not isinstance(raw, dict):
        raise InterviewerError(
            f"ask_question input is not a dict: {type(raw).__name__}"
        )

    open_keys = {d.key for d in open_dims}
    if raw.get("dimension") not in open_keys:
        raise InterviewerError(
            f"trace-import interviewer targeted a closed/unknown dimension: "
            f"{raw.get('dimension')!r}; expected one of {sorted(open_keys)}"
        )

    try:
        brief = QuestionBrief.model_validate(raw)
    except ValidationError as exc:
        raise InterviewerError(
            f"ask_question input failed validation: {exc}"
        ) from exc

    if brief.recommendation_index >= len(brief.options):
        raise InterviewerError(
            f"recommendation_index={brief.recommendation_index} is out of "
            f"range for options of length {len(brief.options)}"
        )

    return brief


__all__ = [
    "SYSTEM_PROMPT",
    "pick_next_question_from_traces",
]
