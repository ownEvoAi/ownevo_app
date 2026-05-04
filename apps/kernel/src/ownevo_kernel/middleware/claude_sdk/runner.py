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
from uuid import uuid4

from .event_router import FinalizedBlock, FinalizedToolCall, StreamEventRouter, _OpenAIStreamAccumulator
from .tool_definitions import (
    KernelContext,
    ToolDispatchResult,
    dispatch_tool,
    kernel_tool_definitions,
    kernel_tool_definitions_openai,
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

DEFAULT_MAX_TOKENS_OPENAI = 16384
"""Per-turn output cap for OpenAI-compatible backends (Ollama, vLLM,
LM Studio). 16K fits within a 32K-65K context window after several
turns of accumulated history. Each agent turn is typically <4K tokens
(tool call JSON + brief reasoning); 16K leaves headroom for extended
chain-of-thought models (e.g. qwen3 thinking mode)."""

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

    async def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: Sequence[dict[str, Any]],
        system: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        thinking: dict[str, Any] | None = None,
        output_config: dict[str, Any] | None = None,
    ) -> Any: ...


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
    no_stream: bool = False,
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

    `no_stream` calls `messages.create()` instead of `messages.stream()`.
    Trace events are coarser (no per-token content_delta) but tool
    dispatch is identical. Useful for backends where streaming breaks
    tool-call translation (e.g., Ollama via LiteLLM proxy).
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

        call_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "system": system,
            "tools": tools,
        }
        if thinking is not None:
            call_kwargs["thinking"] = thinking
        if output_config is not None:
            call_kwargs["output_config"] = output_config

        if no_stream:
            final_message, assistant_blocks, finished_tools = (
                await _run_turn_no_stream(client, call_kwargs, collector, model)
            )
        else:
            async with client.messages.stream(**call_kwargs) as stream:
                async for event in stream:
                    router.on_event(event)
                final_message = await stream.get_final_message()
            assistant_blocks = router.finalize_blocks_in_order()
            finished_tools = router.pop_finished_tool_calls()

        # Accumulate usage from this turn into the run-level totals.
        usage = getattr(final_message, "usage", None)
        if usage is not None:
            for key in token_usage:
                value = getattr(usage, key, 0) or 0
                token_usage[key] += int(value)

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


# ---------------------------------------------------------------------------
# Non-streaming Anthropic turn helper
# ---------------------------------------------------------------------------


async def _run_turn_no_stream(
    client: AnthropicClientProtocol,
    kwargs: dict[str, Any],
    collector: TraceCollector,
    model: str,
) -> tuple[Any, list[FinalizedBlock], list[FinalizedToolCall]]:
    """Call messages.create() and parse the complete Message into blocks.

    Produces the same (final_message, assistant_blocks, finished_tools)
    triple that the streaming path does, so the rest of run_agent_turn
    is format-agnostic. No per-token content_delta events are emitted —
    only tool_call_start (at parse time) and tool_call_result (after
    dispatch, via the caller).
    """
    message = await client.messages.create(**kwargs)  # type: ignore[attr-defined]
    blocks: list[FinalizedBlock] = []
    finished: list[FinalizedToolCall] = []

    for cb in message.content:
        kind = getattr(cb, "type", None)
        if kind == "text":
            blocks.append(FinalizedBlock(kind="text", text=cb.text))
        elif kind == "tool_use":
            span_id = uuid4()
            input_obj = cb.input if isinstance(cb.input, dict) else {}
            blocks.append(
                FinalizedBlock(
                    kind="tool_use",
                    tool_call_id=cb.id,
                    tool_name=cb.name,
                    tool_input=input_obj,
                )
            )
            collector.record(
                collector.make_event(
                    type="tool_call_start",
                    call_id=cb.id,
                    name=cb.name,
                    args=input_obj,
                    parent_span_id=span_id,
                )
            )
            finished.append(
                FinalizedToolCall(
                    call_id=cb.id,
                    name=cb.name,
                    input=input_obj,
                    span_id=span_id,
                )
            )

    return message, blocks, finished


# ---------------------------------------------------------------------------
# OpenAI-compatible runner
# ---------------------------------------------------------------------------


async def run_agent_turn_openai(
    client: Any,
    *,
    system: str,
    user_message: str,
    kernel_context: KernelContext,
    collector: TraceCollector,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS_OPENAI,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    short_circuit_on_sandbox_error: bool = True,
) -> AgentTurnResult:
    """Drive one agent run using an OpenAI-compatible client (e.g. Ollama).

    Drop-in replacement for `run_agent_turn` when the backend speaks
    OpenAI's `/v1/chat/completions` format instead of Anthropic's
    `/v1/messages`. Accepts any `AsyncOpenAI`-compatible client.

    Key differences from `run_agent_turn`:
    - System prompt lives inside the messages array ({"role": "system"}).
    - Tool definitions use OpenAI function format (parameters, not input_schema).
    - Tool results are separate {"role": "tool"} messages per call, not
      batched in a single user message.
    - Assistant messages carry {"tool_calls": [...]} instead of content blocks.
    - Always streams (OpenAI streaming is well-supported by Ollama directly).
    """
    if max_iterations <= 0:
        raise ValueError(f"max_iterations must be positive; got {max_iterations}")
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be positive; got {max_tokens}")

    tools = kernel_tool_definitions_openai()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]

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
        acc = _OpenAIStreamAccumulator(collector=collector, model=model)

        stream = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            stream=True,
            stream_options={"include_usage": True},
        )
        async for chunk in stream:
            acc.on_chunk(chunk)

        # Accumulate usage
        for ant_key, val in acc.get_token_usage().items():
            token_usage[ant_key] = token_usage.get(ant_key, 0) + val

        assistant_blocks = acc.finalize_blocks_in_order()
        finished_tools = acc.pop_finished_tool_calls()

        finish_reason = acc.finish_reason or "stop"
        last_stop_reason = _openai_finish_to_stop_reason(finish_reason)
        last_text = _concatenate_text_blocks(assistant_blocks)

        if not finished_tools:
            return AgentTurnResult(
                stop_reason=last_stop_reason,
                iterations=iterations,
                final_text=last_text,
                tool_call_count=tool_call_count,
                tool_error_count=tool_error_count,
                token_usage=token_usage,
            )

        # Build the assistant message in OpenAI format
        tool_calls_oai = acc.assistant_tool_calls_for_history()
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": last_text or None,
        }
        if tool_calls_oai:
            assistant_msg["tool_calls"] = tool_calls_oai
        messages.append(assistant_msg)

        # Dispatch tools and collect results
        tool_results = await _dispatch_tools(finished_tools, kernel_context, acc)
        tool_call_count += len(tool_results)
        tool_error_count += sum(1 for r in tool_results if r.get("is_error"))

        if short_circuit_on_sandbox_error:
            sandbox_error = next(
                (r for r in tool_results if r.get("_error_class") is not None),
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

        # Tool results in OpenAI format: one {"role":"tool"} message per call
        for r in tool_results:
            content = r.get("content", "")
            if not isinstance(content, str):
                content = _json_stringify(content)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": r["tool_use_id"],
                    "content": content,
                }
            )

    return AgentTurnResult(
        stop_reason="max_iterations",
        iterations=iterations,
        final_text=last_text,
        tool_call_count=tool_call_count,
        tool_error_count=tool_error_count,
        token_usage=token_usage,
    )


def _openai_finish_to_stop_reason(finish_reason: str) -> str:
    """Map OpenAI finish_reason → the AgentTurnResult stop_reason vocabulary."""
    return {
        "stop": "end_turn",
        "tool_calls": "end_turn",  # turned into tool dispatch; only terminal if no tools
        "length": "max_tokens",
        "content_filter": "refusal",
    }.get(finish_reason, finish_reason)


__all__ = [
    "AgentTurnResult",
    "AnthropicClientProtocol",
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MAX_TOKENS_OPENAI",
    "DEFAULT_MODEL",
    "run_agent_turn",
    "run_agent_turn_openai",
]
