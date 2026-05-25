"""Reverse-discovery summary for the trace-import entry point.

When an agent is connected by importing its traces (and sometimes its
exported definition), ownEvo can open the discovery conversation by
telling the operator what the agent *appears to do today* — a one- or
two-sentence "this agent does X" inference grounded in the observed
tools, outputs, and failure modes. The operator confirms or corrects it
before the dimension-by-dimension interview begins.

This is the trace-import analogue of the authoring surface, reversed:
the authoring agent reads a human-written description and produces a
spec; here we read the running agent and produce a human-confirmable
description. The confirmed/corrected text then flows downstream as the
agent's stated intent (the `agent_definition` the interviewer and the
spec-from-traces generator already consume).

The LLM call is a single plain-text completion (no tool). On any LLM
failure the caller can fall back to `fallback_reverse_discovery_summary`,
a deterministic render of the trace summary, so the conversation is
never blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .interviewer import (
    DEFAULT_INTERVIEWER_MODEL,
    InterviewerError,
    _is_anthropic_client,
)
from .trace_summary import TraceSummary

# Imported definitions can be large; cap what we inline. The trace
# summary already carries the behavioural picture.
_AGENT_DEFINITION_TRUNCATE = 4_000

# The summary is meant to be one or two sentences. Cap hard so a chatty
# model can't turn the opening turn into an essay.
_SUMMARY_CHAR_CAP = 600

# A short completion is plenty for one or two sentences.
DEFAULT_MAX_TOKENS = 300

# How many tool names to name in the deterministic fallback before
# collapsing the rest into "+N more".
_FALLBACK_TOOLS_NAMED = 4


@dataclass(frozen=True)
class ReverseDiscoverySummary:
    """A human-confirmable "this agent appears to do X" inference.

    `basis` records what evidence the summary was drawn from
    ("traces" or "definition+traces"); `is_fallback` is True when the
    summary was rendered deterministically because no LLM was available
    or the LLM call failed.
    """

    summary: str
    basis: str
    is_fallback: bool


SYSTEM_PROMPT = (
    "You are ownEvo's design agent, running at the moment a domain expert "
    "connects an agent that is ALREADY RUNNING by importing its traces. "
    "Your job right now is narrow: in ONE or TWO sentences, state what this "
    "agent appears to do today, so the expert can confirm or correct it "
    "before the discovery interview begins.\n\n"
    "Ground the statement only in the evidence you are given — the observed "
    "tools, sample arguments and outputs, failure modes, and (if present) "
    "the agent's own exported definition. Where the traces and the exported "
    "definition disagree, describe what the traces actually show; the "
    "definition is the agent's stated intent and may have drifted.\n\n"
    "Write for a non-developer: a supply-chain VP, chief risk officer, or "
    "clinical lead. Name the concrete job the agent does (e.g. 'forecasts "
    "next-week demand per SKU and flags stockout risk'), not the mechanics "
    "of how it calls tools. Do not speculate beyond the evidence; if the "
    "purpose is genuinely unclear, say so plainly.\n\n"
    "Output the sentence(s) only — no preamble, no markdown, no bullet "
    "points, no quotation marks."
)


def _build_user_message(
    *,
    summary: TraceSummary,
    agent_definition: str | None,
) -> str:
    parts: list[str] = [summary.as_prompt_text()]

    if agent_definition and agent_definition.strip():
        definition = agent_definition.strip()[:_AGENT_DEFINITION_TRUNCATE]
        parts.append(
            "## Imported agent definition\n"
            "The source platform also exported the agent's own definition "
            "(instructions / prompt / Solutions export). Treat it as stated "
            "intent, which may differ from what the traces show.\n"
            f"```\n{definition}\n```"
        )

    parts.append(
        "## Your task\nIn one or two sentences, state what this agent appears "
        "to do today. Output the sentence(s) only."
    )
    return "\n\n".join(parts)


def _extract_text(msg: Any) -> str:
    blocks = [
        getattr(b, "text", "")
        for b in getattr(msg, "content", [])
        if getattr(b, "type", None) == "text"
    ]
    return "".join(blocks).strip()


def fallback_reverse_discovery_summary(
    summary: TraceSummary,
    agent_definition: str | None = None,
) -> ReverseDiscoverySummary:
    """Deterministic "this agent does X" render, no LLM.

    Used when no Anthropic client is configured or the LLM call fails.
    Reads the tool rollup + error modes off the `TraceSummary` so the
    operator still gets a concrete, confirmable opening statement.
    """
    basis = "definition+traces" if (agent_definition or "").strip() else "traces"

    if summary.is_empty:
        return ReverseDiscoverySummary(
            summary=(
                "No decodable behaviour was found in the imported traces, so "
                "what this agent does could not be inferred automatically. "
                "Describe its purpose so discovery can proceed."
            ),
            basis=basis,
            is_fallback=True,
        )

    tool_names = [t.name for t in summary.tools]
    if tool_names:
        named = tool_names[:_FALLBACK_TOOLS_NAMED]
        rendered = ", ".join(f"`{n}`" for n in named)
        extra = len(tool_names) - len(named)
        if extra > 0:
            rendered += f" and {extra} other tool{'s' if extra != 1 else ''}"
        tool_clause = f"calls {rendered}"
    else:
        tool_clause = "performs no observable tool calls"

    trace_clause = (
        f"across {summary.trace_count} imported "
        f"trace{'s' if summary.trace_count != 1 else ''}"
    )

    text = f"This agent {tool_clause} {trace_clause}."

    errored = sum(t.error_count for t in summary.tools)
    if errored:
        text += (
            f" {errored} tool call{'s' if errored != 1 else ''} errored, so "
            "reliability is one thing the loop could improve."
        )

    return ReverseDiscoverySummary(
        summary=text[:_SUMMARY_CHAR_CAP],
        basis=basis,
        is_fallback=True,
    )


async def generate_reverse_discovery_summary(
    *,
    summary: TraceSummary,
    agent_definition: str | None = None,
    client: Any | None = None,
    model: str = DEFAULT_INTERVIEWER_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> ReverseDiscoverySummary:
    """Generate a one-to-two-sentence "this agent does X" summary.

    Raises `InterviewerError` when no usable client is supplied, the LLM
    call fails, or the model returns no text — callers should catch and
    fall back to `fallback_reverse_discovery_summary`.
    """
    if client is None or not _is_anthropic_client(client):
        raise InterviewerError(
            "No Anthropic client provided; reverse discovery cannot run."
        )

    user_message = _build_user_message(
        summary=summary, agent_definition=agent_definition
    )

    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        raise InterviewerError(
            f"reverse-discovery LLM call failed: {type(exc).__name__}: {exc}"
        ) from exc

    text = _extract_text(msg)
    if not text:
        raise InterviewerError(
            "reverse-discovery LLM returned no text — "
            f"stop_reason={getattr(msg, 'stop_reason', None)!r}"
        )

    basis = "definition+traces" if (agent_definition or "").strip() else "traces"
    return ReverseDiscoverySummary(
        summary=text[:_SUMMARY_CHAR_CAP],
        basis=basis,
        is_fallback=False,
    )


__all__ = [
    "ReverseDiscoverySummary",
    "SYSTEM_PROMPT",
    "fallback_reverse_discovery_summary",
    "generate_reverse_discovery_summary",
]
