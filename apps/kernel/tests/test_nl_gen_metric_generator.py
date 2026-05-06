"""Tests for `nl_gen.metric_generator.generate_metric_definition` (A4.2).

Mirrors `test_nl_gen_eval_generator.py` (A4.1):
  1. Fake AsyncAnthropic — pins tool definition shape, system-prompt
     load-bearing rules, the contract on tool_use → MetricDefinition,
     and the three error paths (no tool_use, malformed tool input,
     direction mismatch with the spec's success_criterion).
  2. Live API snapshot — gated by `OWNEVO_ANTHROPIC_LIVE=1`. Generates
     against each fixture and asserts structural shape + the cross-spec
     direction invariant.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.nl_gen import (
    MetricDefinition,
    MetricDefinitionValidationError,
    MetricDirectionMismatchError,
    NoMetricToolUseError,
    generate_metric_definition,
)
from ownevo_kernel.nl_gen.fixtures import FIXTURES, METRIC_FIXTURES
from ownevo_kernel.nl_gen.metric_generator import (
    SYSTEM_PROMPT,
    TOOL_NAME,
    _TOOL_DEFINITION,  # private but needed to pin the tool-definition shape
)
from ownevo_kernel.nl_gen import EVAL_TOOL_NAME as _EVAL_TOOL_NAME


# ---------------------------------------------------------------------------
# Fake AsyncAnthropic — same shape as A4.1's tests
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedResponse:
    content: list[Any]
    stop_reason: str = "tool_use"


class _FakeMessages:
    def __init__(self, response: _ScriptedResponse) -> None:
        self._response = response
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            content=self._response.content,
            stop_reason=self._response.stop_reason,
        )


@dataclass
class _FakeClient:
    response: _ScriptedResponse
    messages: _FakeMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _FakeMessages(self.response)


def _tool_use_block(name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload, id="tu_1")


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


# ---------------------------------------------------------------------------
# Tool-definition pinning
# ---------------------------------------------------------------------------


def test_tool_definition_shape():
    td = _TOOL_DEFINITION
    assert td["name"] == TOOL_NAME
    assert td["description"]
    schema = td["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["metric_definition"]
    md_schema = schema["properties"]["metric_definition"]
    assert md_schema["type"] == "object"
    required = set(md_schema["required"])
    for f in (
        "workflow_spec_id",
        "name",
        "family",
        "direction",
        "lower_bound",
        "upper_bound",
        "target_value",
        "description",
        "rationale",
        "provenance",
    ):
        assert f in required


def test_system_prompt_pins_load_bearing_rules():
    """A future edit can't silently drop the metric-design contract."""
    p = SYSTEM_PROMPT
    # Closed family enumeration
    for family in (
        "pass_rate", "precision", "recall", "f1",
        "balanced_accuracy", "specificity",
    ):
        assert family in p
    # Direction-must-match invariant — load-bearing for the gate
    assert "direction" in p
    assert "success_criterion" in p
    # Bounds contract
    assert "0.0" in p and "1.0" in p
    # Target-value framing
    assert "target_value" in p
    # Provenance shape
    assert "derived" in p and "inferred" in p


# ---------------------------------------------------------------------------
# Pass: tool_use → MetricDefinition
# ---------------------------------------------------------------------------


async def test_tool_use_returning_valid_metric_round_trips():
    spec = FIXTURES["demand-prediction"]
    md = METRIC_FIXTURES["demand-prediction"]
    payload = json.loads(md.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"metric_definition": payload})]
        )
    )
    result = await generate_metric_definition(client, spec)
    assert isinstance(result, MetricDefinition)
    assert result == md


async def test_generator_passes_spec_in_user_message():
    spec = FIXTURES["demand-prediction"]
    md = METRIC_FIXTURES["demand-prediction"]
    payload = json.loads(md.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"metric_definition": payload})]
        )
    )
    await generate_metric_definition(client, spec)
    kw = client.messages.last_kwargs
    assert len(kw["messages"]) == 1
    user_content = kw["messages"][0]["content"]
    assert spec.id in user_content
    assert "WorkflowSpec" in user_content
    assert "success_criterion" in user_content
    assert kw["system"] == SYSTEM_PROMPT
    assert kw["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert kw["tools"][0]["name"] == TOOL_NAME


async def test_flat_tool_input_without_wrapper_round_trips():
    """Some models emit the metric un-wrapped — accept either shape."""
    spec = FIXTURES["demand-prediction"]
    md = METRIC_FIXTURES["demand-prediction"]
    payload = json.loads(md.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, payload)])
    )
    result = await generate_metric_definition(client, spec)
    assert result == md


# ---------------------------------------------------------------------------
# Fail: no tool_use
# ---------------------------------------------------------------------------


async def test_text_only_response_raises_no_tool_use():
    spec = FIXTURES["demand-prediction"]
    client = _FakeClient(
        _ScriptedResponse(
            content=[_text_block("Here's the metric I would write...")],
            stop_reason="end_turn",
        )
    )
    with pytest.raises(NoMetricToolUseError) as exc_info:
        await generate_metric_definition(client, spec)
    assert exc_info.value.stop_reason == "end_turn"
    assert "I would write" in exc_info.value.content_preview


async def test_wrong_tool_name_raises_no_tool_use():
    spec = FIXTURES["demand-prediction"]
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(_EVAL_TOOL_NAME, {"x": 1})],
            stop_reason="tool_use",
        )
    )
    with pytest.raises(NoMetricToolUseError):
        await generate_metric_definition(client, spec)


# ---------------------------------------------------------------------------
# Fail: tool_use with malformed input
# ---------------------------------------------------------------------------


async def test_invalid_tool_input_raises_validation_error():
    spec = FIXTURES["demand-prediction"]
    bad = {"workflow_spec_id": "x"}  # missing every other required field
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, bad)])
    )
    with pytest.raises(MetricDefinitionValidationError) as exc_info:
        await generate_metric_definition(client, spec)
    assert exc_info.value.raw_input == bad
    assert exc_info.value.pydantic_error.error_count() > 0


async def test_extra_field_in_tool_input_raises_validation_error():
    spec = FIXTURES["demand-prediction"]
    md = METRIC_FIXTURES["demand-prediction"]
    payload = json.loads(md.model_dump_json())
    payload["bonus_field"] = "claude invented this"
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"metric_definition": payload})]
        )
    )
    with pytest.raises(MetricDefinitionValidationError):
        await generate_metric_definition(client, spec)


# ---------------------------------------------------------------------------
# Fail: cross-spec direction / id mismatch
# ---------------------------------------------------------------------------


async def test_direction_mismatch_raises_dedicated_error():
    """The model emits a structurally valid metric whose direction
    contradicts the spec's success_criterion. Surface loudly because
    the gate would silently treat regressions as wins."""
    spec = FIXTURES["demand-prediction"]
    md = METRIC_FIXTURES["demand-prediction"].model_copy(
        update={"direction": "minimize"}
    )
    payload = json.loads(md.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"metric_definition": payload})]
        )
    )
    with pytest.raises(MetricDirectionMismatchError) as exc_info:
        await generate_metric_definition(client, spec)
    assert exc_info.value.spec is spec
    assert exc_info.value.definition.direction == "minimize"


async def test_workflow_id_mismatch_raises_direction_mismatch_error():
    """Cross-spec ID mismatch surfaces through the same error class — the
    failure mode is the same (definition can't be safely fed to the gate
    for this workflow)."""
    spec = FIXTURES["demand-prediction"]
    # Use credit-risk's metric fixture against demand-prediction's spec
    md = METRIC_FIXTURES["credit-risk"]
    payload = json.loads(md.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"metric_definition": payload})]
        )
    )
    with pytest.raises(MetricDirectionMismatchError, match="workflow_spec_id"):
        await generate_metric_definition(client, spec)


# ---------------------------------------------------------------------------
# Live API snapshot — gated by OWNEVO_ANTHROPIC_LIVE
# ---------------------------------------------------------------------------


_LIVE = pytest.mark.skipif(
    os.environ.get("OWNEVO_ANTHROPIC_LIVE") != "1",
    reason="set OWNEVO_ANTHROPIC_LIVE=1 to exercise the real Anthropic API",
)


_LIVE_MODEL = os.environ.get(
    "OWNEVO_NL_GEN_LIVE_MODEL", "claude-haiku-4-5-20251001"
)


@_LIVE
@pytest.mark.parametrize("fixture_id", list(METRIC_FIXTURES.keys()))
async def test_live_generates_consistent_metric_definition(fixture_id):
    """Live model returns a structurally valid metric whose direction +
    id match the source spec. The exact `family` and `target_value`
    will vary across runs — we only pin invariants."""
    from anthropic import AsyncAnthropic

    spec = FIXTURES[fixture_id]
    client = AsyncAnthropic()
    md = await generate_metric_definition(client, spec, model=_LIVE_MODEL)

    assert isinstance(md, MetricDefinition)
    assert md.workflow_spec_id == spec.id
    assert md.direction == spec.success_criterion.direction
    assert md.lower_bound == 0.0
    assert md.upper_bound == 1.0
    assert 0.0 < md.target_value < 1.0
