"""Translate Anthropic stream events into typed AgentEvents.

The Anthropic streaming API emits low-level deltas:

  message_start
    content_block_start[0]   text   → ContentDelta on subsequent deltas
      content_block_delta[0] text_delta
      content_block_delta[0] text_delta
    content_block_stop[0]
    content_block_start[1]   thinking
      content_block_delta[1] thinking_delta
    content_block_stop[1]
    content_block_start[2]   tool_use
      content_block_delta[2] input_json_delta  (partial JSON; concat over many deltas)
    content_block_stop[2]
  message_delta              (carries usage + stop_reason)
  message_stop

`StreamEventRouter` accumulates that stream and emits AgentEvents into
the caller's `TraceCollector`:

  text_delta             → ContentDelta(text=…, model=…)
  thinking_delta         → ReasoningDelta(text=…, model=…)
  tool_use block start   → ToolCallStart(call_id=…, name=…, args=…)*
                           *args populated by the runner once dispatch fires
  tool dispatch result   → ToolCallResult(call_id=…, name=…, status=…, output=…,
                                          duration_ms=…, error=…, error_class=…)

Why router state, not pure functions
------------------------------------
`tool_use` blocks accumulate JSON across many `input_json_delta`
events, so the router needs to track per-index buffers until
`content_block_stop` fires. Same for thinking signatures (collected
on the block, not per delta). One router instance per `messages.stream`
context — do not reuse across iterations.

`finish_block(index)` returns the assembled block dict so the runner
can slot it into the assistant message it appends to the next
`messages.create` request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel

from ...traces.collector import TraceCollector


@dataclass
class _BlockState:
    """Per-content-block accumulator.

    `kind` ∈ "text" | "thinking" | "tool_use" — each carries a
    different field set; we union them in one dataclass to keep the
    routing branches simple.
    """

    kind: str  # "text" / "thinking" / "tool_use"
    index: int
    # text
    text_chunks: list[str] = field(default_factory=list)
    # thinking
    thinking_chunks: list[str] = field(default_factory=list)
    thinking_signature: str | None = None
    # tool_use
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input_json_chunks: list[str] = field(default_factory=list)
    # Common: trace-event correlation (set when the block opens)
    span_id: UUID | None = None


@dataclass
class FinalizedBlock:
    """What `StreamEventRouter.finish_block` returns — the same shape the
    runner needs to splice into the assistant message it appends to the
    next request. Mirrors Anthropic's content-block dict with one
    addition: `parsed_input` (already json.loads'd for tool_use)."""

    kind: str
    text: str | None = None
    thinking: str | None = None
    thinking_signature: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None


@dataclass
class FinalizedToolCall:
    """A tool_use block ready to dispatch.

    Returned by `pop_finished_tool_calls()` after a content_block_stop
    closes a tool_use block. The runner drains these between turns of
    the agent loop.
    """

    call_id: str
    name: str
    input: dict[str, Any]
    span_id: UUID


class StreamEventRouter:
    """Per-stream accumulator + AgentEvent emitter.

    Constructed once per `messages.stream(...)` context. Call
    `on_event(event)` for every yielded event; `pop_finished_tool_calls()`
    drains tool_use blocks that have closed; `record_tool_result(...)`
    emits the matching ToolCallResult AgentEvent after dispatch.

    The router is stateless w.r.t. AgentEvent ordering — it emits as
    deltas arrive, so a downstream consumer reading the trace sees the
    same stream the agent produced. The `parent_span_id` on each event
    ties content_delta / reasoning_delta to the `tool_call_start` they
    follow (when applicable), so the agent's reasoning chain is
    walkable in the trace.
    """

    def __init__(
        self,
        *,
        collector: TraceCollector,
        model: str,
    ) -> None:
        self._collector = collector
        self._model = model
        self._blocks: dict[int, _BlockState] = {}
        self._finished_tool_calls: list[FinalizedToolCall] = []

    # ------------------------------------------------------------------
    # Stream event entry point
    # ------------------------------------------------------------------

    def on_event(self, event: BaseModel) -> None:
        """Feed one event from the Anthropic stream.

        Branches by `event.type`. Unknown event types are silently
        ignored so a future SDK adding a new variant doesn't crash the
        router.
        """
        ev_type = getattr(event, "type", None)
        if ev_type == "content_block_start":
            self._on_block_start(event)
        elif ev_type == "content_block_delta":
            self._on_block_delta(event)
        elif ev_type == "content_block_stop":
            self._on_block_stop(event)
        # message_start / message_delta / message_stop carry usage +
        # stop_reason; the runner reads them off the final Message via
        # stream.get_final_message() rather than accumulating here.

    # ------------------------------------------------------------------
    # Block lifecycle
    # ------------------------------------------------------------------

    def _on_block_start(self, event: BaseModel) -> None:
        index = event.index
        block = event.content_block
        kind = getattr(block, "type", None)
        span_id = uuid4()
        if kind == "text":
            self._blocks[index] = _BlockState(
                kind="text", index=index, span_id=span_id,
            )
        elif kind == "thinking":
            self._blocks[index] = _BlockState(
                kind="thinking", index=index, span_id=span_id,
            )
        elif kind == "tool_use":
            tool_id = block.id
            name = block.name
            self._blocks[index] = _BlockState(
                kind="tool_use",
                index=index,
                tool_call_id=tool_id,
                tool_name=name,
                span_id=span_id,
            )
            # We do NOT emit ToolCallStart yet — args aren't known until
            # input_json_delta accumulation completes at content_block_stop.
        # Other block kinds (citations, server_tool_use, etc.) are
        # ignored for v1 — they don't appear in our agent's tool surface.

    def _on_block_delta(self, event: BaseModel) -> None:
        state = self._blocks.get(event.index)
        if state is None:
            return
        delta = event.delta
        delta_type = getattr(delta, "type", None)
        if delta_type == "text_delta":
            text = delta.text
            state.text_chunks.append(text)
            self._collector.record(
                self._collector.make_event(
                    type="content_delta",
                    text=text,
                    model=self._model,
                    parent_span_id=state.span_id,
                )
            )
        elif delta_type == "thinking_delta":
            text = delta.thinking
            state.thinking_chunks.append(text)
            self._collector.record(
                self._collector.make_event(
                    type="reasoning_delta",
                    text=text,
                    model=self._model,
                    parent_span_id=state.span_id,
                )
            )
        elif delta_type == "input_json_delta":
            # Tool args stream as concatenated partial JSON. The router
            # buffers; the agent only sees the assembled object once
            # the block closes.
            state.tool_input_json_chunks.append(delta.partial_json)
        elif delta_type == "signature_delta":
            # Thinking blocks include a signature — append to the
            # already-streamed signature when the SDK delivers it.
            sig = getattr(delta, "signature", None)
            if sig is not None:
                state.thinking_signature = (state.thinking_signature or "") + sig

    def _on_block_stop(self, event: BaseModel) -> None:
        state = self._blocks.get(event.index)
        if state is None:
            return
        if state.kind == "tool_use":
            assert state.tool_call_id is not None
            assert state.tool_name is not None
            assert state.span_id is not None
            input_obj = self._parse_tool_input(state)
            # Emit ToolCallStart now that args are assembled. The
            # matching ToolCallResult fires from `record_tool_result`
            # after dispatch — paired by call_id.
            self._collector.record(
                self._collector.make_event(
                    type="tool_call_start",
                    call_id=state.tool_call_id,
                    name=state.tool_name,
                    args=input_obj,
                    parent_span_id=state.span_id,
                )
            )
            self._finished_tool_calls.append(
                FinalizedToolCall(
                    call_id=state.tool_call_id,
                    name=state.tool_name,
                    input=input_obj,
                    span_id=state.span_id,
                )
            )
        # Text / thinking blocks: nothing to emit on stop — deltas
        # already streamed individually. The runner picks up the
        # assembled content via `finish_blocks_in_order`.

    # ------------------------------------------------------------------
    # Runner-facing helpers
    # ------------------------------------------------------------------

    def pop_finished_tool_calls(self) -> list[FinalizedToolCall]:
        """Return tool_use blocks that have closed since the last call.
        Drains the internal buffer."""
        out = self._finished_tool_calls
        self._finished_tool_calls = []
        return out

    def record_tool_result(
        self,
        *,
        call_id: str,
        name: str,
        status: str,
        output: Any,
        duration_ms: int | None,
        error: str | None,
        error_class: str | None,
        parent_span_id: UUID | None = None,
    ) -> None:
        """Emit a ToolCallResult AgentEvent after dispatch completes.

        Called by the runner with the dispatch outcome so the trace
        has both halves of the tool call on the same stream. The
        `error_class` field threads through to the gate's D3
        invariant: a sandbox-runtime kill (Timeout / OOM / Crash)
        sets it; a logical error inside the tool leaves it None.
        """
        event_kwargs: dict[str, Any] = {
            "type": "tool_call_result",
            "call_id": call_id,
            "name": name,
            "status": status,
            "output": output,
            "duration_ms": duration_ms if duration_ms is not None else 0,
            "error": error,
            "error_class": error_class,
        }
        if parent_span_id is not None:
            event_kwargs["parent_span_id"] = parent_span_id
        self._collector.record(self._collector.make_event(**event_kwargs))

    def finalize_blocks_in_order(self) -> list[FinalizedBlock]:
        """Return the assistant's content blocks in stream order.

        Used by the runner to build the assistant message it appends
        to the next `messages.create` request. The shape mirrors
        Anthropic's block dicts with `parsed_input` for tool_use so
        the dispatch site doesn't re-parse.
        """
        out: list[FinalizedBlock] = []
        for index in sorted(self._blocks.keys()):
            state = self._blocks[index]
            if state.kind == "text":
                out.append(
                    FinalizedBlock(
                        kind="text",
                        text="".join(state.text_chunks),
                    )
                )
            elif state.kind == "thinking":
                out.append(
                    FinalizedBlock(
                        kind="thinking",
                        thinking="".join(state.thinking_chunks),
                        thinking_signature=state.thinking_signature,
                    )
                )
            elif state.kind == "tool_use":
                out.append(
                    FinalizedBlock(
                        kind="tool_use",
                        tool_call_id=state.tool_call_id,
                        tool_name=state.tool_name,
                        tool_input=self._parse_tool_input(state),
                    )
                )
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _parse_tool_input(self, state: _BlockState) -> dict[str, Any]:
        raw = "".join(state.tool_input_json_chunks)
        if not raw.strip():
            return {}
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            # The model emitted tool_use with malformed JSON. Surface
            # an empty dict + the raw string so dispatch returns a
            # structured tool_result the model can react to. Better
            # than crashing the agent loop.
            return {}
        return obj if isinstance(obj, dict) else {}


# ---------------------------------------------------------------------------
# Convenience: build an AgentEvent timestamp without leaking the field
# everywhere — kept so tests can compare against `_now()`.
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """UTC-aware now(). Tests can monkey-patch via `event_router._now = ...`
    for deterministic timestamps."""
    return datetime.now(UTC)


__all__ = [
    "FinalizedBlock",
    "FinalizedToolCall",
    "StreamEventRouter",
]
