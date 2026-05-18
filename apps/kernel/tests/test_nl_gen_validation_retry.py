"""Tests for `nl_gen/_validation_retry.py`.

Covers the core retry state machine with a fully scripted async client so
no network is required:

  - Happy path: first attempt succeeds, exactly one `messages.create` call.
  - Retry path: attempt 1 fails validation, attempt 2 succeeds, tool_result
    with `is_error=True` appears in the second call's messages.
  - NoToolUseSignal: raised immediately, not retried.
  - RetryExhaustedError: all max_retries+1 attempts fail, error carries the
    final ValidationError and attempt count.
  - normalize callback: applied before model_validate on every attempt,
    receives the unwrapped payload.
  - extra_feedback: present verbatim in the tool_result content on retry.
  - truncate_for_error: truncates long inputs.
  - Invalid max_retries: raises ValueError for negative values.
  - _format_validation_feedback: includes error loc/msg and extra_rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, Field
from ownevo_kernel.nl_gen._validation_retry import (
    DEFAULT_MAX_RETRIES,
    NoToolUseSignal,
    RetryExhaustedError,
    _format_validation_feedback,
    call_with_validation_retry,
    truncate_for_error,
)


# ---------------------------------------------------------------------------
# Minimal schema for testing
# ---------------------------------------------------------------------------


class _Widget(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    value: int = Field(ge=0)


_TOOL_NAME = "emit_widget"
_TOOL_DEF: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "Emit a widget",
    "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "integer"}}},
}

_GOOD_PAYLOAD = {"name": "foo", "value": 42}
_BAD_PAYLOAD = {"name": "bar", "value": -1}  # value < 0 fails Field(ge=0)


# ---------------------------------------------------------------------------
# Scripted async client
# ---------------------------------------------------------------------------


def _tool_block(payload: dict, tool_id: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=_TOOL_NAME, input=payload, id=tool_id)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


@dataclass
class _ScriptedMessages:
    """Returns scripted responses in order; records every call's kwargs."""

    responses: list[SimpleNamespace]
    calls: list[dict] = field(default_factory=list)
    _idx: int = field(default=0, init=False, repr=False)

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        resp = self.responses[self._idx]
        self._idx += 1
        return resp


@dataclass
class _ScriptedClient:
    responses: list[SimpleNamespace]
    messages: _ScriptedMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _ScriptedMessages(self.responses)


def _resp(*blocks: SimpleNamespace, stop_reason: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call(client, *, max_retries: int = 0, normalize=None, extra_feedback: str = ""):
    return await call_with_validation_retry(
        client=client,
        model="test-model",
        max_tokens=100,
        system="system",
        tool_definition=_TOOL_DEF,
        tool_name=_TOOL_NAME,
        initial_user_message="build a widget",
        schema_class=_Widget,
        envelope_key=None,
        max_retries=max_retries,
        normalize=normalize,
        extra_feedback=extra_feedback,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_one_call():
    client = _ScriptedClient([_resp(_tool_block(_GOOD_PAYLOAD))])
    widget, raw = await _call(client)
    assert widget.name == "foo"
    assert widget.value == 42
    assert raw == _GOOD_PAYLOAD
    assert len(client.messages.calls) == 1


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt():
    """Attempt 1 fails validation; attempt 2 returns a valid payload."""
    client = _ScriptedClient([
        _resp(_tool_block(_BAD_PAYLOAD)),
        _resp(_tool_block(_GOOD_PAYLOAD)),
    ])
    widget, _ = await _call(client, max_retries=1)
    assert widget.value == 42
    assert len(client.messages.calls) == 2


@pytest.mark.asyncio
async def test_retry_injects_tool_result_with_is_error():
    """Second call must include a tool_result is_error=True in messages."""
    client = _ScriptedClient([
        _resp(_tool_block(_BAD_PAYLOAD)),
        _resp(_tool_block(_GOOD_PAYLOAD)),
    ])
    await _call(client, max_retries=1)
    second_call_messages = client.messages.calls[1]["messages"]
    user_turn = second_call_messages[-1]
    assert user_turn["role"] == "user"
    assert isinstance(user_turn["content"], list)
    tool_result = user_turn["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["is_error"] is True
    assert tool_result["tool_use_id"] == "tu_1"


@pytest.mark.asyncio
async def test_extra_feedback_in_retry_tool_result():
    extra = "IMPORTANT: value must be >= 0"
    client = _ScriptedClient([
        _resp(_tool_block(_BAD_PAYLOAD)),
        _resp(_tool_block(_GOOD_PAYLOAD)),
    ])
    await _call(client, max_retries=1, extra_feedback=extra)
    second_call_messages = client.messages.calls[1]["messages"]
    tool_result_content = second_call_messages[-1]["content"][0]["content"]
    assert extra in tool_result_content


@pytest.mark.asyncio
async def test_no_tool_use_raises_signal_immediately():
    client = _ScriptedClient([_resp(_text_block("I cannot do that"), stop_reason="end_turn")])
    with pytest.raises(NoToolUseSignal) as exc_info:
        await _call(client, max_retries=2)
    assert exc_info.value.stop_reason == "end_turn"
    assert len(client.messages.calls) == 1  # not retried


@pytest.mark.asyncio
async def test_retry_exhausted_raises_error():
    client = _ScriptedClient([_resp(_tool_block(_BAD_PAYLOAD))] * 3)
    with pytest.raises(RetryExhaustedError) as exc_info:
        await _call(client, max_retries=2)
    err = exc_info.value
    assert err.attempts == 3
    assert err.pydantic_error.error_count() > 0
    assert err.raw_input == _BAD_PAYLOAD
    assert len(client.messages.calls) == 3


@pytest.mark.asyncio
async def test_normalize_applied_before_validate():
    """normalize callback must receive the unwrapped payload and its return value is validated."""
    called_with: list[Any] = []

    def _normalize(payload: Any) -> Any:
        called_with.append(payload)
        return {**payload, "value": 42}  # fix negative value

    client = _ScriptedClient([_resp(_tool_block(_BAD_PAYLOAD))])
    widget, _ = await _call(client, normalize=_normalize)
    assert widget.value == 42
    assert called_with == [_BAD_PAYLOAD]


@pytest.mark.asyncio
async def test_normalize_applied_on_all_attempts():
    """normalize is invoked on every attempt, including successful retries."""
    seen_values: list[Any] = []

    def _normalize(payload: Any) -> Any:
        seen_values.append(payload.get("value"))
        return payload  # pass through; attempt 1 bad, attempt 2 good

    client = _ScriptedClient([
        _resp(_tool_block(_BAD_PAYLOAD)),   # attempt 1: normalize called, still fails
        _resp(_tool_block(_GOOD_PAYLOAD)),  # attempt 2: normalize called, passes
    ])
    await _call(client, max_retries=1, normalize=_normalize)
    assert seen_values == [-1, 42]  # normalize called on both attempts


@pytest.mark.asyncio
async def test_envelope_unwrapping():
    """When envelope_key is set, the payload is unwrapped before validate."""
    client = _ScriptedClient([_resp(_tool_block({"widget": _GOOD_PAYLOAD}))])
    widget, raw = await call_with_validation_retry(
        client=client,
        model="test-model",
        max_tokens=100,
        system="system",
        tool_definition=_TOOL_DEF,
        tool_name=_TOOL_NAME,
        initial_user_message="build",
        schema_class=_Widget,
        envelope_key="widget",
        max_retries=0,
    )
    assert widget.name == "foo"
    assert raw == {"widget": _GOOD_PAYLOAD}


@pytest.mark.asyncio
async def test_invalid_max_retries_raises_value_error():
    client = _ScriptedClient([])
    with pytest.raises(ValueError, match="max_retries must be >= 0"):
        await _call(client, max_retries=-1)


def test_truncate_for_error_truncates():
    long_input = {"key": "x" * 1000}
    result = truncate_for_error(long_input, limit=50)
    assert len(result) <= 50


def test_truncate_for_error_non_serializable():
    result = truncate_for_error(object(), limit=100)
    assert len(result) <= 100


def test_format_validation_feedback_includes_loc_and_msg():
    from pydantic import ValidationError as PydanticValidationError

    try:
        _Widget.model_validate({"name": "x", "value": -5})
    except PydanticValidationError as exc:
        feedback = _format_validation_feedback(exc, extra_rules="Rule A")
    assert "value" in feedback
    assert "Rule A" in feedback
    assert "greater than or equal" in feedback.lower() or "ge" in feedback.lower()
