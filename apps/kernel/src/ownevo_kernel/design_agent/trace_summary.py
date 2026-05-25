"""Summarise imported agent traces into prompt-ready text.

The trace-import design surface starts from an existing agent's traces
rather than a human-written description. Both the trace-import
interviewer and the spec-from-traces generator need the same input: a
compact, faithful description of *what the imported agent actually
does* — which tools it calls, in what order, on what arguments, what it
produces, and where it fails.

This module turns the raw `AgentEvent` dicts (as stored in the `traces`
table's JSONB `events` column) into:

  * a structured `TraceSummary` — tool-usage rollup, sample arguments,
    sample text outputs, observed error modes, and run counts; and
  * a rendered prompt block (`as_prompt_text`) the LLM sees in place of
    a free-text workflow description.

The summary is deterministic and contains no LLM call, so it is cheap
to build, easy to unit-test, and identical across the interviewer and
the generator (they must see the same picture of the agent).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg

# Truncation caps keep the rendered block bounded regardless of how
# large any single trace is. The LLM only needs a representative
# picture, not the full transcript.
_ARG_VALUE_TRUNCATE = 160
_OUTPUT_TRUNCATE = 240
_TEXT_TRUNCATE = 400
_ERROR_TRUNCATE = 120
_MAX_TOOLS_RENDERED = 20
_MAX_SAMPLE_OUTPUTS = 5
_MAX_ERROR_MODES = 8
_MAX_TEXT_SAMPLES = 3

# Cap on how many traces to load for a single summary. A handful of
# representative traces describe the agent's shape; loading thousands
# adds cost without changing the picture the LLM needs.
_MAX_SUMMARY_TRACES = 50

# Cap on events-per-trace processed by the summariser. The OTLP receiver
# already enforces a 10 000-event cap at ingest time, but a legacy or
# hand-crafted trace could exceed that. Without a cap here, a single
# runaway trace would make summarise_events iterate unboundedly.
_MAX_EVENTS_PER_TRACE = 10_000


@dataclass
class ToolUsage:
    """Per-tool rollup across the summarised traces."""

    name: str
    call_count: int = 0
    error_count: int = 0
    sample_args: dict[str, Any] | None = None


@dataclass
class TraceSummary:
    """Structured, deterministic summary of a set of imported traces."""

    trace_count: int = 0
    event_count: int = 0
    tools: list[ToolUsage] = field(default_factory=list)
    sample_outputs: list[str] = field(default_factory=list)
    text_samples: list[str] = field(default_factory=list)
    error_modes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.event_count == 0

    def as_prompt_text(self) -> str:
        """Render the summary as a markdown block for the LLM user message.

        Reads as an observational report ("this agent does X") rather
        than an instruction, matching the trace-import stance: the
        starting material is a running agent, not a request.
        """
        lines: list[str] = ["## Imported agent — observed behaviour"]
        if self.is_empty:
            lines.append(
                "No decodable events were found in the imported traces. "
                "Treat the agent's purpose as unknown and ask the operator "
                "to describe it."
            )
            return "\n".join(lines)

        lines.append(
            f"Summarised from {self.trace_count} imported "
            f"trace{'s' if self.trace_count != 1 else ''} "
            f"({self.event_count} events total)."
        )

        if self.tools:
            lines.append("")
            lines.append("### Tools the agent calls")
            for tool in self.tools[:_MAX_TOOLS_RENDERED]:
                detail = f"- `{tool.name}` — called {tool.call_count}×"
                if tool.error_count:
                    detail += f", {tool.error_count} errored"
                lines.append(detail)
                if tool.sample_args:
                    lines.append(
                        f"    e.g. args: {_render_args(tool.sample_args)}"
                    )
        else:
            lines.append("")
            lines.append(
                "### Tools the agent calls\n(no tool calls observed in "
                "these traces)"
            )

        if self.text_samples:
            lines.append("")
            lines.append("### Sample assistant text")
            for sample in self.text_samples[:_MAX_TEXT_SAMPLES]:
                lines.append(f"- {sample}")

        if self.sample_outputs:
            lines.append("")
            lines.append("### Sample tool outputs")
            for sample in self.sample_outputs[:_MAX_SAMPLE_OUTPUTS]:
                lines.append(f"- {sample}")

        if self.error_modes:
            lines.append("")
            lines.append("### Observed failure modes")
            for mode in self.error_modes[:_MAX_ERROR_MODES]:
                lines.append(f"- {mode}")

        return "\n".join(lines)


def _truncate(value: str, limit: int) -> str:
    value = value.strip().replace("\n", " ")
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _render_args(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, raw in args.items():
        rendered = raw if isinstance(raw, str) else json.dumps(raw, default=str)
        parts.append(f"{key}={_truncate(str(rendered), _ARG_VALUE_TRUNCATE)}")
    return ", ".join(parts)


def _stringify_output(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, default=str)
    except (TypeError, ValueError):
        return str(output)


def summarize_events(
    trace_rows: list[tuple[UUID, list[dict[str, Any]]]],
) -> TraceSummary:
    """Build a `TraceSummary` from `(trace_id, events)` pairs.

    Pure function over decoded event dicts — no DB, no LLM. The event
    dict shape matches the `AgentEvent` discriminated union stored in
    `traces.events` (discriminator field `type`).
    """
    summary = TraceSummary(trace_count=len(trace_rows))
    tool_index: dict[str, ToolUsage] = {}
    seen_outputs: set[str] = set()
    seen_text: set[str] = set()
    seen_errors: set[str] = set()

    for _trace_id, events in trace_rows:
        for ev in events:
            summary.event_count += 1
            ev_type = ev.get("type")

            if ev_type == "tool_call_start":
                name = ev.get("name") or "unknown-tool"
                usage = tool_index.get(name)
                if usage is None:
                    usage = ToolUsage(name=name)
                    tool_index[name] = usage
                    summary.tools.append(usage)
                usage.call_count += 1
                args = ev.get("args")
                if usage.sample_args is None and isinstance(args, dict) and args:
                    usage.sample_args = args

            elif ev_type == "tool_call_result":
                name = ev.get("name") or "unknown-tool"
                usage = tool_index.get(name)
                if usage is None:
                    usage = ToolUsage(name=name)
                    tool_index[name] = usage
                    summary.tools.append(usage)
                if ev.get("status") == "error":
                    usage.error_count += 1
                    mode = _build_error_mode(ev)
                    if mode not in seen_errors:
                        seen_errors.add(mode)
                        summary.error_modes.append(mode)
                else:
                    rendered = _stringify_output(ev.get("output"))
                    if rendered:
                        truncated = _truncate(rendered, _OUTPUT_TRUNCATE)
                        if truncated and truncated not in seen_outputs:
                            seen_outputs.add(truncated)
                            summary.sample_outputs.append(
                                f"`{name}` → {truncated}"
                            )

            elif ev_type == "content_delta":
                text = ev.get("cumulative_text") or ev.get("text") or ""
                truncated = _truncate(str(text), _TEXT_TRUNCATE)
                if truncated and truncated not in seen_text:
                    seen_text.add(truncated)
                    summary.text_samples.append(truncated)

    return summary


def _build_error_mode(error_event: dict[str, Any]) -> str:
    name = error_event.get("name") or "unknown-tool"
    error_class = error_event.get("error_class") or "logical-error"
    error_msg = _truncate(str(error_event.get("error") or ""), _ERROR_TRUNCATE)
    base = f"{error_class} in `{name}`"
    return f"{base}: {error_msg}" if error_msg else base


async def load_trace_events(
    conn: asyncpg.Connection,
    trace_ids: list[UUID],
) -> list[tuple[UUID, list[dict[str, Any]]]]:
    """Load decoded event arrays for the given trace ids.

    Order follows `started_at` (oldest first) so the summary reads in
    run order. Capped at `_MAX_SUMMARY_TRACES`. Unknown ids are silently
    dropped — the caller validates that at least one trace resolved.
    """
    if not trace_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT id, events
        FROM traces
        WHERE id = ANY($1::uuid[])
        ORDER BY started_at ASC
        LIMIT $2
        """,
        trace_ids,
        _MAX_SUMMARY_TRACES,
    )
    result: list[tuple[UUID, list[dict[str, Any]]]] = []
    for row in rows:
        events = row["events"]
        if isinstance(events, str):
            events = json.loads(events)
        result.append((row["id"], list(events or [])[:_MAX_EVENTS_PER_TRACE]))
    return result


__all__ = [
    "ToolUsage",
    "TraceSummary",
    "load_trace_events",
    "summarize_events",
]
