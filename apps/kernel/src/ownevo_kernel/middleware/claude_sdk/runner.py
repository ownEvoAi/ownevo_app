"""run_agent_turn — manual agentic loop over Anthropic Messages API.

Drives a Claude conversation with the 5 kernel tools:

  loop:
    open client.messages.stream(...) with current `messages`, `tools`
    feed each yielded event into StreamEventRouter (emits AgentEvents)
    drain finalized tool_use blocks, dispatch each via tool_definitions
    record ToolCallResult AgentEvents
    if stop_reason in {end_turn, refusal, max_tokens}: break
    else: append assistant message + user tool_result message; continue

The loop is **manual** rather than using `client.beta.messages.tool_runner`
because the trace contract demands per-token streaming events, which
the tool_runner does not surface (it returns complete BetaMessage per
iteration). See PLAN.md W2.1 follow-up + the claude-api skill's tool-use
guidance for the trade-off.

Shape contract
--------------
Inputs:
  client            -- AsyncAnthropic (mockable Protocol below for tests)
  system            -- system prompt (frozen string; cached via prefix)
  user_message      -- the kickoff user content
  kernel_context    -- dependencies the 5 tools execute against
  collector         -- TraceCollector the run emits AgentEvents into
  model             -- "claude-opus-4-7" by default (Opus 4.7)
  max_tokens        -- per-turn output cap; defaults to 64000 (streaming)
  thinking          -- {"type": "adaptive"} | {"type": "disabled"} | None
  effort            -- "low" | "medium" | "high" | "xhigh" | "max" | None
  max_iterations    -- safety cap on tool-use turns; defaults to 25

Output (`AgentTurnResult`):
  stop_reason       -- end_turn / refusal / max_tokens / max_iterations
                       / sandbox_error_propagated
  iterations        -- number of model turns consumed
  final_text        -- the agent's final assistant text (last turn's
                       text blocks concatenated)
  token_usage       -- summed across turns: input/output/cache_*
  tool_call_count   -- total successful + errored dispatches
  tool_error_count  -- subset where the dispatcher returned is_error

Why the loop owns the trace lifecycle (and the collector's not optional)
-----------------------------------------------------------------------
Every tool dispatch must produce a paired ToolCallStart / ToolCallResult
in the trace. Letting the caller "skip" the collector would leave the
gate runner with an event stream missing tool_call_result events, and
the SANDBOX_ERROR short-circuit would never fire. So `collector` is
required, not optional.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .event_router import FinalizedToolCall, StreamEventRouter
from .tool_definitions import (
    KernelContext,
    ToolDispatchResult,
    dispatch_tool,
    kernel_tool_definitions,
)

if TYPE_CHECKING:
    from ...traces.collector import TraceCollector

DEFAULT_MODEL = "claude-opus-4-7"
"""Per the claude-api skill: ALWAYS default to Opus 4.7. Callers can
override via `model=` for a cheaper Sonnet/Haiku variant on testing
paths, but the spine should stay on Opus 4.7."""

DEFAULT_MAX_TOKENS = 64000
"""Streaming-required default for Opus 4.7. The skill notes >16K
needs streaming to avoid SDK HTTP timeouts; we always stream so the
trace events surface live."""

DEFAULT_MAX_ITERATIONS = 25
"""Maximum number of model-turn → tool-dispatch cycles per run.
Bounds runaway loops (model proposes → tool errors → model retries
→ ...). 25 is generous for the M5 baseline (typical: 3-8 turns); the
gate runner would have already failed long before this cap."""


# ---------------------------------------------------------------------------
# Protocol — tests mock this without depending on AsyncAnthropic
# ---------------------------------------------------------------------------


class _StreamCtx(Protocol):
    """Minimal Protocol for `client.messages.stream(...)` async context.

    Anthropic's `MessageStreamManager` exposes more, but we only need:
      * async iteration over events
      * `get_final_message()` → returns the assembled Message
    """

    async def __aenter__(self) -> _StreamProtocol: ...
    async def __aexit__(self, *args: Any) -> Any: ...


class _StreamProtocol(Protocol):
    def __aiter__(self) -> _StreamProtocol: ...
    async def __anext__(self) -> Any: ...
    async def get_final_message(self) -> Any: ...


class _MessagesAPI(Protocol):
    def stream(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: Sequence[dict[str, Any]],
        system: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        thinking: dict[str, Any] | None = None,
        output_config: dict[str, Any] | None = None,
    ) -> _StreamCtx: ...


class AnthropicClientProtocol(Protocol):
    """The slice of AsyncAnthropic this runner consumes.

    Tests pass a fake matching this Protocol — see
    `test_middleware_claude_sdk.py`'s `_FakeClient`. Production passes
    a real `AsyncAnthropic` instance.
    """

    @property
    def messages(self) -> _MessagesAPI: ...


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentTurnResult:
    """What `run_agent_turn` returns to the caller.

    The trace itself (events, metrics) lives on the collector — this
    result is the lifecycle summary the gate runner / lift chart /
    iteration row consume.
    """

    stop_reason: str
    """Last turn's stop_reason: end_turn / max_tokens / refusal /
    pause_turn / max_iterations / sandbox_error_propagated. Per the
    claude-api skill's stop-reason table."""

    iterations: int
    """How many model turns the loop ran. 1 means "agent answered
    immediately, no tools." Higher = more tool turns."""

    final_text: str
    """Concatenation of text blocks in the final assistant turn.
    Empty string when the run terminated mid-tool-use (e.g.,
    sandbox_error_propagated)."""

    tool_call_count: int
    tool_error_count: int

    token_usage: dict[str, int] = field(default_factory=dict)
    """Summed across all turns: input_tokens, output_tokens,
    cache_creation_input_tokens, cache_read_input_tokens. Useful for
    the iteration row's `token_budget_used`."""

    @property
    def succeeded(self) -> bool:
        """True when the agent ended on its own (`end_turn`) — not on
        a cap, refusal, or propagated sandbox error."""
        return self.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_agent_turn(
    client: AnthropicClientProtocol,
    *,
    system: str,
    user_message: str,
    kernel_context: KernelContext,
    collector: TraceCollector,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    thinking: dict[str, Any] | None = None,
    effort: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    short_circuit_on_sandbox_error: bool = True,
) -> AgentTurnResult:
    """Drive one agent run and emit AgentEvents into `collector`.

    `thinking` defaults to None (off) per Opus 4.7 default. Set
    `{"type": "adaptive"}` to enable; pair with `effort="xhigh"` for
    the recommended coding/agentic configuration.

    `effort` goes inside `output_config={"effort": ...}` per the
    claude-api skill — passing it as a top-level kw here keeps the
    call site simple.

    `short_circuit_on_sandbox_error` propagates a sandbox-runtime kill
    (Timeout / OOM / Crash from `run_pipeline`) up out of the loop
    rather than letting the agent retry. Default True because the
    gate's D3 contract is "don't trust val_score on sandbox errors" —
    retrying a Timeout in-agent would be the kernel hiding the failure
    from the gate. Set False for exploratory benchmarks where retry is
    OK.
    """
    if max_iterations <= 0:
        raise ValueError(f"max_iterations must be positive; got {max_iterations}")
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be positive; got {max_tokens}")

    tools = kernel_tool_definitions()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_message},
    ]

    output_config: dict[str, Any] | None = (
        {"effort": effort} if effort is not None else None
    )

    iterations = 0
    tool_call_count = 0
    tool_error_count = 0
    token_usage: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    last_stop_reason = "max_iterations"
    last_text = ""

    for _ in range(max_iterations):
        iterations += 1
        router = StreamEventRouter(collector=collector, model=model)

        stream_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "system": system,
            "tools": tools,
        }
        if thinking is not None:
            stream_kwargs["thinking"] = thinking
        if output_config is not None:
            stream_kwargs["output_config"] = output_config

        async with client.messages.stream(**stream_kwargs) as stream:
            async for event in stream:
                router.on_event(event)
            final_message = await stream.get_final_message()

        # Accumulate usage from this turn into the run-level totals.
        usage = getattr(final_message, "usage", None)
        if usage is not None:
            for key in token_usage:
                value = getattr(usage, key, 0) or 0
                token_usage[key] += int(value)

        # Gather the assistant's content blocks in stream order. We use
        # the router's view (carries assembled tool_input dicts), not
        # the SDK's content list, because the dispatch site needs the
        # parsed inputs anyway and round-tripping through Anthropic's
        # `ToolUseBlock.input` would lose nothing but adds a branch.
        assistant_blocks = router.finalize_blocks_in_order()

        # The runner records the tool_call_start events (via the
        # router's on_event); now drain the closed tool_uses and
        # dispatch them. Order matches stream order so trace event
        # ordering is preserved.
        finished_tools = router.pop_finished_tool_calls()

        last_stop_reason = (
            getattr(final_message, "stop_reason", None) or "unknown"
        )
        last_text = _concatenate_text_blocks(assistant_blocks)

        if not finished_tools:
            # No tool calls this turn → terminal. The model's
            # stop_reason tells the caller whether it answered
            # (`end_turn`), hit the cap (`max_tokens`), or refused
            # (`refusal`).
            return AgentTurnResult(
                stop_reason=last_stop_reason,
                iterations=iterations,
                final_text=last_text,
                tool_call_count=tool_call_count,
                tool_error_count=tool_error_count,
                token_usage=token_usage,
            )

        # Dispatch every tool the agent asked for. The Anthropic
        # protocol expects ALL tool_results in one user message, so
        # collect them before appending.
        tool_results = await _dispatch_tools(
            finished_tools, kernel_context, router,
        )
        tool_call_count += len(tool_results)
        tool_error_count += sum(
            1 for r in tool_results if r.get("is_error")
        )

        # Short-circuit: if any tool returned a sandbox-runtime
        # error_class, don't loop. The gate refuses to trust val_score
        # under sandbox errors anyway; letting the agent retry would
        # mask the underlying failure mode the gate is supposed to
        # surface.
        if short_circuit_on_sandbox_error:
            sandbox_error = next(
                (
                    r for r in tool_results
                    if r.get("_error_class") is not None
                ),
                None,
            )
            if sandbox_error is not None:
                return AgentTurnResult(
                    stop_reason="sandbox_error_propagated",
                    iterations=iterations,
                    final_text=last_text,
                    tool_call_count=tool_call_count,
                    tool_error_count=tool_error_count,
                    token_usage=token_usage,
                )

        # Append assistant turn (including tool_use blocks) + user
        # tool_results to history; loop.
        messages.append(
            {
                "role": "assistant",
                "content": _assistant_blocks_to_api_shape(assistant_blocks),
            }
        )
        messages.append(
            {"role": "user", "content": _strip_internal_keys(tool_results)},
        )

    # Hit the iteration cap without a terminal stop_reason.
    return AgentTurnResult(
        stop_reason="max_iterations",
        iterations=iterations,
        final_text=last_text,
        tool_call_count=tool_call_count,
        tool_error_count=tool_error_count,
        token_usage=token_usage,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _dispatch_tools(
    finished: Sequence[FinalizedToolCall],
    kernel_context: KernelContext,
    router: StreamEventRouter,
) -> list[dict[str, Any]]:
    """Dispatch each tool_use sequentially; emit ToolCallResult events.

    Sequential rather than parallel: the kernel surface (asyncpg
    connection, sandbox) isn't designed for concurrent access from
    one connection, and the M5 baseline in particular wants serial
    sandbox runs (cgroup memory pressure on parallel containers is
    unbounded). When parallelism becomes important, switch to
    `asyncio.gather` here and give each branch its own DB connection.
    """
    out: list[dict[str, Any]] = []
    for call in finished:
        result: ToolDispatchResult = await dispatch_tool(
            call.name, call.input, kernel_context,
        )
        # Trace event — pairs with the ToolCallStart the router
        # already recorded at content_block_stop.
        router.record_tool_result(
            call_id=call.call_id,
            name=call.name,
            status="error" if result.is_error else "ok",
            output=result.output,
            duration_ms=result.duration_ms,
            error=(
                _stringify_output(result.output) if result.is_error else None
            ),
            error_class=result.error_class,
            parent_span_id=call.span_id,
        )
        # Anthropic tool_result block content: a string or list of
        # content blocks. We always use a string (JSON-serialized for
        # dicts) — simpler, and the agent reads it as text either way.
        content = (
            result.output if isinstance(result.output, str)
            else _json_stringify(result.output)
        )
        api_result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": call.call_id,
            "content": content,
            "is_error": result.is_error,
            # `_error_class` is internal — short-circuit decision
            # uses it; stripped before sending to Anthropic via
            # `_strip_internal_keys`.
            "_error_class": result.error_class,
        }
        out.append(api_result)
    return out


def _concatenate_text_blocks(blocks: Sequence[Any]) -> str:
    return "".join(b.text for b in blocks if b.kind == "text" and b.text)


def _assistant_blocks_to_api_shape(blocks: Sequence[Any]) -> list[dict[str, Any]]:
    """Convert the router's FinalizedBlock view into the dict shape
    Anthropic accepts on the next request's assistant message.
    Skips empty blocks defensively."""
    out: list[dict[str, Any]] = []
    for b in blocks:
        if b.kind == "text":
            if b.text:
                out.append({"type": "text", "text": b.text})
        elif b.kind == "thinking":
            if b.thinking:
                shaped: dict[str, Any] = {"type": "thinking", "thinking": b.thinking}
                if b.thinking_signature is not None:
                    shaped["signature"] = b.thinking_signature
                out.append(shaped)
        elif b.kind == "tool_use":
            out.append(
                {
                    "type": "tool_use",
                    "id": b.tool_call_id,
                    "name": b.tool_name,
                    "input": b.tool_input or {},
                }
            )
    return out


def _strip_internal_keys(
    tool_results: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in tool_results
    ]


def _json_stringify(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, default=str)
    except (TypeError, ValueError):
        return str(obj)


def _stringify_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    return _json_stringify(output)


__all__ = [
    "AgentTurnResult",
    "AnthropicClientProtocol",
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "run_agent_turn",
]
