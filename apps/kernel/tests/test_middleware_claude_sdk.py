"""Unit tests for the Claude Agent SDK middleware (W2.1).

Three layers exercised here:

  1. `StreamEventRouter` — event accumulation + AgentEvent emission.
  2. `dispatch_tool` (kernel-context branches) — covered by separate
     DB-backed tests in `test_agent_tools_*` already; we don't repeat.
  3. `run_agent_turn` — manual loop wiring tested via a script-driven
     fake `AsyncAnthropic` client. The kernel-side dispatcher is
     monkey-patched per test so we don't need a Postgres DB or Docker
     sandbox; the goal here is to verify protocol behavior, not the
     individual tool branches.

Why a script-driven fake instead of recording real traffic
----------------------------------------------------------
The real Anthropic API is a moving target (model picks vary across
runs); recording a fixture would be brittle. Scripted events are
deterministic, encode the exact deltas the router must handle, and
let one test drive multi-turn loops. A round-trip integration test
against the real API lives behind an env-var gate elsewhere (see
`OWNEVO_ANTHROPIC_LIVE` in PLAN.md W2.1).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from ownevo_kernel.middleware.claude_sdk import (
    AgentTurnResult,
    StreamEventRouter,
    ToolDispatchResult,
    run_agent_turn,
)
from ownevo_kernel.middleware.claude_sdk import runner as runner_mod
from ownevo_kernel.middleware.claude_sdk import tool_definitions as tooldefs
from ownevo_kernel.traces.collector import TraceCollector

# ---------------------------------------------------------------------------
# Event factory helpers (build the event objects the router expects)
# ---------------------------------------------------------------------------


def _block_start_text(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(type="text"),
    )


def _block_start_thinking(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(type="thinking"),
    )


def _block_start_tool_use(index: int, *, tool_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(type="tool_use", id=tool_id, name=name),
    )


def _delta_text(index: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def _delta_thinking(index: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="thinking_delta", thinking=text),
    )


def _delta_signature(index: int, sig: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="signature_delta", signature=sig),
    )


def _delta_tool_json(index: int, partial: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="input_json_delta", partial_json=partial),
    )


def _block_stop(index: int) -> SimpleNamespace:
    return SimpleNamespace(type="content_block_stop", index=index)


# ---------------------------------------------------------------------------
# Fake AsyncAnthropic client — the runner only calls
# `client.messages.stream(...)` and uses its async-context-manager + iteration.
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedTurn:
    """One scripted iteration of the agent loop.

    `events` is the sequence the fake stream yields; `final_message`
    is what `get_final_message()` returns. Tests build a list of these
    and feed them to `_FakeClient` in the order the runner will see
    them.
    """

    events: list[SimpleNamespace]
    final_message: SimpleNamespace


class _FakeStream:
    def __init__(self, turn: _ScriptedTurn) -> None:
        self._turn = turn
        self._iter: Iterable[SimpleNamespace] | None = None

    async def __aenter__(self) -> _FakeStream:
        self._iter = iter(self._turn.events)
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> SimpleNamespace:
        try:
            assert self._iter is not None
            return next(self._iter)  # type: ignore[arg-type]
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def get_final_message(self) -> SimpleNamespace:
        return self._turn.final_message


class _FakeMessagesAPI:
    def __init__(self, turns: list[_ScriptedTurn]) -> None:
        self._turns = turns
        self._index = 0
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _FakeStream:
        # Capture each call so tests can assert system / tools / messages
        # got threaded correctly.
        self.calls.append(kwargs)
        if self._index >= len(self._turns):
            raise AssertionError(
                f"Fake client exhausted: runner asked for turn "
                f"{self._index + 1} but only {len(self._turns)} were scripted",
            )
        turn = self._turns[self._index]
        self._index += 1
        return _FakeStream(turn)


class _FakeClient:
    def __init__(self, turns: list[_ScriptedTurn]) -> None:
        self.messages = _FakeMessagesAPI(turns)


def _final_message(
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        ),
    )


# ---------------------------------------------------------------------------
# Trace collector helper — no DB, just collect events for inspection.
# ---------------------------------------------------------------------------


def _new_collector() -> TraceCollector:
    return TraceCollector(workflow_id="test-wf")


def _events_of(collector: TraceCollector, type_name: str) -> list[Any]:
    return [e for e in collector.events if e.type == type_name]


# ---------------------------------------------------------------------------
# StreamEventRouter — pure tests (no client, no runner)
# ---------------------------------------------------------------------------


class TestStreamEventRouter:
    def test_text_delta_emits_content_delta(self) -> None:
        collector = _new_collector()
        router = StreamEventRouter(collector=collector, model="claude-opus-4-7")
        for ev in [
            _block_start_text(0),
            _delta_text(0, "Hello"),
            _delta_text(0, " world"),
            _block_stop(0),
        ]:
            router.on_event(ev)
        deltas = _events_of(collector, "content_delta")
        assert len(deltas) == 2
        assert [d.text for d in deltas] == ["Hello", " world"]
        assert all(d.model == "claude-opus-4-7" for d in deltas)
        # Both deltas share the same parent_span_id (their content block).
        assert deltas[0].parent_span_id == deltas[1].parent_span_id
        # Finalized blocks: one text block, joined chunks.
        finals = router.finalize_blocks_in_order()
        assert len(finals) == 1
        assert finals[0].kind == "text"
        assert finals[0].text == "Hello world"

    def test_thinking_delta_emits_reasoning_delta(self) -> None:
        collector = _new_collector()
        router = StreamEventRouter(collector=collector, model="claude-opus-4-7")
        for ev in [
            _block_start_thinking(0),
            _delta_thinking(0, "Considering"),
            _delta_thinking(0, " options"),
            _delta_signature(0, "sig-"),
            _delta_signature(0, "abc"),
            _block_stop(0),
        ]:
            router.on_event(ev)
        deltas = _events_of(collector, "reasoning_delta")
        assert [d.text for d in deltas] == ["Considering", " options"]
        finals = router.finalize_blocks_in_order()
        assert len(finals) == 1
        assert finals[0].kind == "thinking"
        assert finals[0].thinking == "Considering options"
        assert finals[0].thinking_signature == "sig-abc"

    def test_tool_use_block_emits_start_at_close(self) -> None:
        collector = _new_collector()
        router = StreamEventRouter(collector=collector, model="claude-opus-4-7")
        # JSON streamed in three chunks — router must concatenate.
        for ev in [
            _block_start_tool_use(0, tool_id="toolu_1", name="read_skill"),
            _delta_tool_json(0, '{"skill_'),
            _delta_tool_json(0, 'id":"m5'),
            _delta_tool_json(0, '.baseline.v1"}'),
        ]:
            router.on_event(ev)
        # No ToolCallStart yet — block hasn't closed.
        assert _events_of(collector, "tool_call_start") == []
        router.on_event(_block_stop(0))
        starts = _events_of(collector, "tool_call_start")
        assert len(starts) == 1
        assert starts[0].name == "read_skill"
        assert starts[0].args == {"skill_id": "m5.baseline.v1"}
        assert starts[0].call_id == "toolu_1"
        # And drained for dispatch.
        finished = router.pop_finished_tool_calls()
        assert len(finished) == 1
        assert finished[0].call_id == "toolu_1"
        assert finished[0].name == "read_skill"
        assert finished[0].input == {"skill_id": "m5.baseline.v1"}
        # Drain consumed the buffer.
        assert router.pop_finished_tool_calls() == []

    def test_malformed_tool_json_yields_empty_input(self) -> None:
        """Bad JSON shouldn't crash the loop — the agent gets a chance
        to retry with a corrected call."""
        collector = _new_collector()
        router = StreamEventRouter(collector=collector, model="claude-opus-4-7")
        for ev in [
            _block_start_tool_use(0, tool_id="toolu_x", name="read_skill"),
            _delta_tool_json(0, "not-valid-json"),
            _block_stop(0),
        ]:
            router.on_event(ev)
        starts = _events_of(collector, "tool_call_start")
        assert starts and starts[0].args == {}
        finished = router.pop_finished_tool_calls()
        assert finished[0].input == {}

    def test_finalize_preserves_block_order(self) -> None:
        """text(0) → thinking(1) → tool_use(2) → text(3) — finalized
        in the same order so the assistant message round-trip lands
        right on the next request."""
        collector = _new_collector()
        router = StreamEventRouter(collector=collector, model="claude-opus-4-7")
        events = [
            _block_start_thinking(0),
            _delta_thinking(0, "..."),
            _block_stop(0),
            _block_start_text(1),
            _delta_text(1, "Reading skill."),
            _block_stop(1),
            _block_start_tool_use(2, tool_id="toolu_a", name="read_skill"),
            _delta_tool_json(2, '{"skill_id":"x"}'),
            _block_stop(2),
            _block_start_text(3),
            _delta_text(3, " Done."),
            _block_stop(3),
        ]
        for ev in events:
            router.on_event(ev)
        finals = router.finalize_blocks_in_order()
        kinds = [b.kind for b in finals]
        assert kinds == ["thinking", "text", "tool_use", "text"]
        assert finals[1].text == "Reading skill."
        assert finals[2].tool_call_id == "toolu_a"
        assert finals[3].text == " Done."

    def test_record_tool_result_emits_paired_event(self) -> None:
        collector = _new_collector()
        router = StreamEventRouter(collector=collector, model="claude-opus-4-7")
        span = uuid4()
        router.record_tool_result(
            call_id="toolu_q",
            name="run_pipeline",
            status="error",
            output={"error": "boom"},
            duration_ms=42,
            error="boom",
            error_class="Timeout",
            parent_span_id=span,
        )
        results = _events_of(collector, "tool_call_result")
        assert len(results) == 1
        ev = results[0]
        assert ev.call_id == "toolu_q"
        assert ev.status == "error"
        assert ev.error_class == "Timeout"
        assert ev.duration_ms == 42
        assert ev.parent_span_id == span


# ---------------------------------------------------------------------------
# run_agent_turn — manual loop tests with a fake client + monkey-patched dispatch
# ---------------------------------------------------------------------------


def _kernel_ctx() -> tooldefs.KernelContext:
    """Build a KernelContext whose `conn` and `sandbox` are sentinels.

    Tests that exercise dispatch monkey-patch `runner_mod.dispatch_tool`,
    so the kernel surface is never actually touched. We just need
    SOME object to pass as `kernel_context`.
    """
    return tooldefs.KernelContext(
        conn=object(),  # type: ignore[arg-type]
        sandbox=object(),  # type: ignore[arg-type]
        actor="agent:test",
        default_workflow_id="test-wf",
    )


@pytest.fixture
def patch_dispatch(monkeypatch: pytest.MonkeyPatch):
    """Replace `dispatch_tool` with a queue of canned ToolDispatchResults.

    Each test enqueues the results it expects; the fixture returns the
    list of (name, args) tuples seen, in dispatch order, so the test
    can assert the agent's calls landed correctly.
    """
    canned: list[ToolDispatchResult] = []
    seen: list[tuple[str, dict[str, Any]]] = []

    async def fake_dispatch(
        name: str,
        args: dict[str, Any],
        ctx: tooldefs.KernelContext,
    ) -> ToolDispatchResult:
        seen.append((name, dict(args)))
        if not canned:
            raise AssertionError(
                f"dispatch_tool called with name={name!r} but no result queued",
            )
        return canned.pop(0)

    monkeypatch.setattr(runner_mod, "dispatch_tool", fake_dispatch)
    return SimpleNamespace(canned=canned, seen=seen)


class TestRunAgentTurn:
    async def test_no_tool_calls_terminates_in_one_turn(self) -> None:
        client = _FakeClient(
            [
                _ScriptedTurn(
                    events=[
                        _block_start_text(0),
                        _delta_text(0, "Hello, world."),
                        _block_stop(0),
                    ],
                    final_message=_final_message(stop_reason="end_turn"),
                ),
            ]
        )
        collector = _new_collector()
        result = await run_agent_turn(
            client,  # type: ignore[arg-type]
            system="You are a test agent.",
            user_message="Say hi",
            kernel_context=_kernel_ctx(),
            collector=collector,
        )
        assert isinstance(result, AgentTurnResult)
        assert result.stop_reason == "end_turn"
        assert result.iterations == 1
        assert result.final_text == "Hello, world."
        assert result.tool_call_count == 0
        assert result.tool_error_count == 0
        assert result.succeeded is True
        # Token usage flowed through.
        assert result.token_usage["input_tokens"] == 100
        assert result.token_usage["output_tokens"] == 50
        # ContentDelta events landed in the collector.
        assert _events_of(collector, "content_delta")

    async def test_two_turn_tool_use_loop(
        self, patch_dispatch: SimpleNamespace,
    ) -> None:
        """Turn 1: model emits a tool_use; runner dispatches; turn 2:
        model returns end_turn after seeing the tool_result."""
        patch_dispatch.canned.append(
            ToolDispatchResult(
                output={"found": True, "skill_id": "m5.v1", "content": "..."},
                is_error=False,
                error_class=None,
                duration_ms=12,
            )
        )
        client = _FakeClient(
            [
                _ScriptedTurn(
                    events=[
                        _block_start_text(0),
                        _delta_text(0, "I'll read it."),
                        _block_stop(0),
                        _block_start_tool_use(
                            1, tool_id="toolu_r1", name="read_skill",
                        ),
                        _delta_tool_json(1, '{"skill_id":"m5.v1"}'),
                        _block_stop(1),
                    ],
                    final_message=_final_message(stop_reason="tool_use"),
                ),
                _ScriptedTurn(
                    events=[
                        _block_start_text(0),
                        _delta_text(0, "All set."),
                        _block_stop(0),
                    ],
                    final_message=_final_message(
                        stop_reason="end_turn",
                        input_tokens=200,
                        output_tokens=20,
                    ),
                ),
            ]
        )
        collector = _new_collector()
        result = await run_agent_turn(
            client,  # type: ignore[arg-type]
            system="...",
            user_message="Read the skill",
            kernel_context=_kernel_ctx(),
            collector=collector,
        )
        assert result.stop_reason == "end_turn"
        assert result.iterations == 2
        assert result.tool_call_count == 1
        assert result.tool_error_count == 0
        assert result.final_text == "All set."
        # Dispatch saw the right call.
        assert patch_dispatch.seen == [("read_skill", {"skill_id": "m5.v1"})]
        # Token usage summed.
        assert result.token_usage["input_tokens"] == 300
        assert result.token_usage["output_tokens"] == 70
        # Collector saw both ToolCallStart and ToolCallResult.
        starts = _events_of(collector, "tool_call_start")
        results_evs = _events_of(collector, "tool_call_result")
        assert len(starts) == 1 and len(results_evs) == 1
        assert starts[0].call_id == results_evs[0].call_id == "toolu_r1"
        assert results_evs[0].status == "ok"
        # Second request to the model carried the tool_result.
        assert client.messages.calls[1]["messages"][-1]["role"] == "user"
        tool_result_block = client.messages.calls[1]["messages"][-1]["content"][0]
        assert tool_result_block["type"] == "tool_result"
        assert tool_result_block["tool_use_id"] == "toolu_r1"
        # Internal `_error_class` key was stripped before sending.
        assert "_error_class" not in tool_result_block

    async def test_sandbox_error_short_circuits(
        self, patch_dispatch: SimpleNamespace,
    ) -> None:
        """A run_pipeline result with error_class != None must end the
        loop with `sandbox_error_propagated`. Default behavior."""
        patch_dispatch.canned.append(
            ToolDispatchResult(
                output={"status": "error", "error_class": "Timeout"},
                is_error=True,
                error_class="Timeout",
                duration_ms=60_000,
            )
        )
        client = _FakeClient(
            [
                _ScriptedTurn(
                    events=[
                        _block_start_tool_use(
                            0, tool_id="toolu_t", name="run_pipeline",
                        ),
                        _delta_tool_json(0, '{"skill_content":"print(1)"}'),
                        _block_stop(0),
                    ],
                    final_message=_final_message(stop_reason="tool_use"),
                ),
            ]
        )
        collector = _new_collector()
        result = await run_agent_turn(
            client,  # type: ignore[arg-type]
            system="...",
            user_message="run it",
            kernel_context=_kernel_ctx(),
            collector=collector,
        )
        assert result.stop_reason == "sandbox_error_propagated"
        assert result.tool_call_count == 1
        assert result.tool_error_count == 1
        assert result.succeeded is False
        # ToolCallResult event carries the error_class for the gate.
        results_evs = _events_of(collector, "tool_call_result")
        assert results_evs[0].error_class == "Timeout"
        # The fake client was only asked for one turn — short-circuit fired.
        assert len(client.messages.calls) == 1

    async def test_sandbox_error_short_circuit_disabled(
        self, patch_dispatch: SimpleNamespace,
    ) -> None:
        """With short_circuit_on_sandbox_error=False the loop continues
        and the agent gets a chance to react to the error."""
        patch_dispatch.canned.extend(
            [
                ToolDispatchResult(
                    output={"status": "error", "error_class": "Timeout"},
                    is_error=True,
                    error_class="Timeout",
                    duration_ms=60_000,
                ),
            ]
        )
        client = _FakeClient(
            [
                _ScriptedTurn(
                    events=[
                        _block_start_tool_use(
                            0, tool_id="toolu_t", name="run_pipeline",
                        ),
                        _delta_tool_json(0, '{"skill_content":"x"}'),
                        _block_stop(0),
                    ],
                    final_message=_final_message(stop_reason="tool_use"),
                ),
                _ScriptedTurn(
                    events=[
                        _block_start_text(0),
                        _delta_text(0, "Giving up."),
                        _block_stop(0),
                    ],
                    final_message=_final_message(stop_reason="end_turn"),
                ),
            ]
        )
        collector = _new_collector()
        result = await run_agent_turn(
            client,  # type: ignore[arg-type]
            system="...",
            user_message="run it",
            kernel_context=_kernel_ctx(),
            collector=collector,
            short_circuit_on_sandbox_error=False,
        )
        assert result.stop_reason == "end_turn"
        assert result.iterations == 2
        assert result.tool_error_count == 1

    async def test_max_iterations_cap(self, patch_dispatch: SimpleNamespace) -> None:
        """Agent that just keeps emitting tool_use forever hits the cap
        and returns stop_reason='max_iterations'."""
        # Three turns, each with a tool_use; we'll cap at 2 to trigger.
        for _ in range(2):
            patch_dispatch.canned.append(
                ToolDispatchResult(
                    output={"found": False},
                    is_error=False,
                    error_class=None,
                    duration_ms=1,
                )
            )
        turns = []
        for i in range(3):
            turns.append(
                _ScriptedTurn(
                    events=[
                        _block_start_tool_use(
                            0, tool_id=f"toolu_{i}", name="read_skill",
                        ),
                        _delta_tool_json(0, '{"skill_id":"x"}'),
                        _block_stop(0),
                    ],
                    final_message=_final_message(stop_reason="tool_use"),
                )
            )
        client = _FakeClient(turns)
        result = await run_agent_turn(
            client,  # type: ignore[arg-type]
            system="...",
            user_message="loop forever",
            kernel_context=_kernel_ctx(),
            collector=_new_collector(),
            max_iterations=2,
        )
        assert result.stop_reason == "max_iterations"
        assert result.iterations == 2
        # Two dispatches happened (one per turn).
        assert result.tool_call_count == 2
        assert len(patch_dispatch.seen) == 2

    async def test_max_tokens_stop_reason_terminates(self) -> None:
        """`max_tokens` is terminal: even though the model didn't say
        end_turn, there are no tool_uses queued, so the loop ends."""
        client = _FakeClient(
            [
                _ScriptedTurn(
                    events=[
                        _block_start_text(0),
                        _delta_text(0, "Truncated mid-sentence"),
                        _block_stop(0),
                    ],
                    final_message=_final_message(stop_reason="max_tokens"),
                ),
            ]
        )
        result = await run_agent_turn(
            client,  # type: ignore[arg-type]
            system="...",
            user_message="...",
            kernel_context=_kernel_ctx(),
            collector=_new_collector(),
        )
        assert result.stop_reason == "max_tokens"
        assert result.succeeded is False

    async def test_thinking_blocks_round_tripped_to_next_request(
        self, patch_dispatch: SimpleNamespace,
    ) -> None:
        """Thinking blocks stay in the assistant message we append on
        the next request — Anthropic requires the signature to be
        echoed back when interleaved thinking is on."""
        patch_dispatch.canned.append(
            ToolDispatchResult(
                output={"found": True, "skill_id": "x"},
                is_error=False,
                error_class=None,
                duration_ms=1,
            )
        )
        client = _FakeClient(
            [
                _ScriptedTurn(
                    events=[
                        _block_start_thinking(0),
                        _delta_thinking(0, "Consider..."),
                        _delta_signature(0, "sig-xyz"),
                        _block_stop(0),
                        _block_start_tool_use(
                            1, tool_id="toolu_t", name="read_skill",
                        ),
                        _delta_tool_json(1, '{"skill_id":"x"}'),
                        _block_stop(1),
                    ],
                    final_message=_final_message(stop_reason="tool_use"),
                ),
                _ScriptedTurn(
                    events=[
                        _block_start_text(0),
                        _delta_text(0, "ok"),
                        _block_stop(0),
                    ],
                    final_message=_final_message(stop_reason="end_turn"),
                ),
            ]
        )
        await run_agent_turn(
            client,  # type: ignore[arg-type]
            system="...",
            user_message="...",
            kernel_context=_kernel_ctx(),
            collector=_new_collector(),
            thinking={"type": "adaptive"},
        )
        # The assistant message on turn 2 carries the thinking block + signature.
        second_request = client.messages.calls[1]
        assert second_request["thinking"] == {"type": "adaptive"}
        assistant_blocks = second_request["messages"][-2]["content"]
        thinking_blocks = [b for b in assistant_blocks if b["type"] == "thinking"]
        assert thinking_blocks
        assert thinking_blocks[0]["thinking"] == "Consider..."
        assert thinking_blocks[0]["signature"] == "sig-xyz"
        # Tool_use block also echoed.
        tool_use_blocks = [b for b in assistant_blocks if b["type"] == "tool_use"]
        assert tool_use_blocks[0]["id"] == "toolu_t"
        assert tool_use_blocks[0]["input"] == {"skill_id": "x"}

    async def test_effort_threads_into_output_config(self) -> None:
        client = _FakeClient(
            [
                _ScriptedTurn(
                    events=[
                        _block_start_text(0),
                        _delta_text(0, "ok"),
                        _block_stop(0),
                    ],
                    final_message=_final_message(stop_reason="end_turn"),
                )
            ]
        )
        await run_agent_turn(
            client,  # type: ignore[arg-type]
            system="...",
            user_message="...",
            kernel_context=_kernel_ctx(),
            collector=_new_collector(),
            effort="xhigh",
        )
        first = client.messages.calls[0]
        assert first["output_config"] == {"effort": "xhigh"}
        # Default model + max_tokens come from the runner.
        assert first["model"] == "claude-opus-4-7"
        assert first["max_tokens"] == 64_000

    async def test_multiple_tools_in_one_turn(
        self, patch_dispatch: SimpleNamespace,
    ) -> None:
        """When the model emits two tool_uses in one turn, both must be
        dispatched and both tool_results land in the same user message."""
        patch_dispatch.canned.extend(
            [
                ToolDispatchResult(
                    output={"found": True, "skill_id": "a"},
                    is_error=False,
                    error_class=None,
                    duration_ms=1,
                ),
                ToolDispatchResult(
                    output={"found": True, "skill_id": "b"},
                    is_error=False,
                    error_class=None,
                    duration_ms=2,
                ),
            ]
        )
        client = _FakeClient(
            [
                _ScriptedTurn(
                    events=[
                        _block_start_tool_use(
                            0, tool_id="toolu_a", name="read_skill",
                        ),
                        _delta_tool_json(0, '{"skill_id":"a"}'),
                        _block_stop(0),
                        _block_start_tool_use(
                            1, tool_id="toolu_b", name="read_skill",
                        ),
                        _delta_tool_json(1, '{"skill_id":"b"}'),
                        _block_stop(1),
                    ],
                    final_message=_final_message(stop_reason="tool_use"),
                ),
                _ScriptedTurn(
                    events=[
                        _block_start_text(0),
                        _delta_text(0, "got both"),
                        _block_stop(0),
                    ],
                    final_message=_final_message(stop_reason="end_turn"),
                ),
            ]
        )
        result = await run_agent_turn(
            client,  # type: ignore[arg-type]
            system="...",
            user_message="...",
            kernel_context=_kernel_ctx(),
            collector=_new_collector(),
        )
        assert result.tool_call_count == 2
        # Both calls dispatched in stream order.
        assert [n for n, _ in patch_dispatch.seen] == ["read_skill", "read_skill"]
        assert [a["skill_id"] for _, a in patch_dispatch.seen] == ["a", "b"]
        # Second request: one user message with both tool_results.
        tool_results = client.messages.calls[1]["messages"][-1]["content"]
        assert len(tool_results) == 2
        assert {b["tool_use_id"] for b in tool_results} == {"toolu_a", "toolu_b"}

    async def test_invalid_max_iterations_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_iterations must be positive"):
            await run_agent_turn(
                _FakeClient([]),  # type: ignore[arg-type]
                system="...",
                user_message="...",
                kernel_context=_kernel_ctx(),
                collector=_new_collector(),
                max_iterations=0,
            )

    async def test_invalid_max_tokens_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            await run_agent_turn(
                _FakeClient([]),  # type: ignore[arg-type]
                system="...",
                user_message="...",
                kernel_context=_kernel_ctx(),
                collector=_new_collector(),
                max_tokens=0,
            )
