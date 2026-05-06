"""Tests for `nl_gen.eval_generator.generate_eval_case_set` (A4.1).

Mirrors `test_nl_gen_sim_generator.py` (A3.2):
  1. Fake AsyncAnthropic — pins tool definition shape, system-prompt
     load-bearing rules, the contract on tool_use → EvalCaseSet, and the
     two error paths (no tool_use, malformed tool input).
  2. Live API snapshot — gated by `OWNEVO_ANTHROPIC_LIVE=1`. Generates
     against each fixture and asserts structural shape + replay-equivalence
     against the matched SimulationPlan.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.nl_gen import (
    EvalCaseSet,
    EvalCaseSetValidationError,
    NoEvalToolUseError,
    generate_eval_case_set,
)
from ownevo_kernel.nl_gen.eval_generator import (
    SYSTEM_PROMPT,
    TOOL_NAME,
    _TOOL_DEFINITION,
)
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    SIM_PLAN_FIXTURES,
)


# ---------------------------------------------------------------------------
# Fake AsyncAnthropic — same shape as A3.2's tests
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
    assert schema["required"] == ["eval_case_set"]
    set_schema = schema["properties"]["eval_case_set"]
    assert set_schema["type"] == "object"
    required = set(set_schema["required"])
    for f in (
        "workflow_spec_id",
        "simulation_plan_workflow_id",
        "cases",
    ):
        assert f in required


def test_system_prompt_pins_load_bearing_rules():
    """A future edit can't silently drop the case-design contract."""
    p = SYSTEM_PROMPT
    # Determinism + targeting
    assert "target_label_field" in p
    assert "target_step_index" in p
    assert "sim_seed" in p
    # The bool-field contract
    assert "bool" in p
    # Past-miss coverage rule
    assert "known_past_misses" in p
    # Both classes covered
    assert "expected_value" in p
    # Held-out discipline
    assert "is_test_fold" in p
    # Size bound
    assert "10" in p and "30" in p


# ---------------------------------------------------------------------------
# Pass: tool_use → EvalCaseSet
# ---------------------------------------------------------------------------


async def test_tool_use_returning_valid_case_set_round_trips():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    payload = json.loads(case_set.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"eval_case_set": payload})]
        )
    )
    result = await generate_eval_case_set(client, spec, plan)
    assert isinstance(result, EvalCaseSet)
    assert result == case_set


async def test_generator_passes_spec_and_plan_in_user_message():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    payload = json.loads(case_set.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"eval_case_set": payload})]
        )
    )
    await generate_eval_case_set(client, spec, plan)
    kw = client.messages.last_kwargs
    assert len(kw["messages"]) == 1
    user_content = kw["messages"][0]["content"]
    # Both artifacts visible to the model — observable contract.
    assert spec.id in user_content
    assert plan.workflow_spec_id in user_content
    assert "WorkflowSpec" in user_content
    assert "SimulationPlan" in user_content
    assert kw["system"] == SYSTEM_PROMPT
    assert kw["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert kw["tools"][0]["name"] == TOOL_NAME


async def test_flat_tool_input_without_wrapper_round_trips():
    """Some models emit the case set un-wrapped — accept either shape."""
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    payload = json.loads(case_set.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, payload)])
    )
    result = await generate_eval_case_set(client, spec, plan)
    assert result == case_set


# ---------------------------------------------------------------------------
# Pre-flight back-pointer check
# ---------------------------------------------------------------------------


async def test_mismatched_spec_and_plan_raises_value_error():
    """Caller-side contract: the plan must be for the same workflow as the spec."""
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["credit-risk"]
    client = _FakeClient(_ScriptedResponse(content=[]))
    with pytest.raises(ValueError, match="workflow_spec_id"):
        await generate_eval_case_set(client, spec, plan)
    # Generator should not even hit the model when the back-pointer is bad.
    assert client.messages.last_kwargs is None


# ---------------------------------------------------------------------------
# Fail: no tool_use
# ---------------------------------------------------------------------------


async def test_text_only_response_raises_no_tool_use():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    client = _FakeClient(
        _ScriptedResponse(
            content=[_text_block("Here are the cases I would write...")],
            stop_reason="end_turn",
        )
    )
    with pytest.raises(NoEvalToolUseError) as exc_info:
        await generate_eval_case_set(client, spec, plan)
    assert exc_info.value.stop_reason == "end_turn"
    assert "I would write" in exc_info.value.content_preview


async def test_wrong_tool_name_raises_no_tool_use():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block("emit_simulation_plan", {"x": 1})],
            stop_reason="tool_use",
        )
    )
    with pytest.raises(NoEvalToolUseError):
        await generate_eval_case_set(client, spec, plan)


# ---------------------------------------------------------------------------
# Fail: tool_use with malformed input
# ---------------------------------------------------------------------------


async def test_invalid_tool_input_raises_validation_error():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    bad = {"workflow_spec_id": "x"}  # missing every other required field
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, bad)])
    )
    with pytest.raises(EvalCaseSetValidationError) as exc_info:
        await generate_eval_case_set(client, spec, plan)
    assert exc_info.value.raw_input == bad
    assert exc_info.value.pydantic_error.error_count() > 0


async def test_extra_field_in_tool_input_raises_validation_error():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    payload = json.loads(case_set.model_dump_json())
    payload["bonus_field"] = "claude invented this"
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"eval_case_set": payload})]
        )
    )
    with pytest.raises(EvalCaseSetValidationError):
        await generate_eval_case_set(client, spec, plan)


async def test_one_class_suite_rejected_by_validator():
    """Server validates the balanced-classes rule — a one-class payload fails."""
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    payload = json.loads(case_set.model_dump_json())
    # Force every case to expected_value=True
    for c in payload["cases"]:
        c["expected_value"] = True
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"eval_case_set": payload})]
        )
    )
    with pytest.raises(EvalCaseSetValidationError):
        await generate_eval_case_set(client, spec, plan)


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
@pytest.mark.parametrize("fixture_id", list(EVAL_CASE_SET_FIXTURES.keys()))
async def test_live_generates_replayable_case_set(fixture_id):
    """Generated set replays cleanly against the matched sim plan."""
    from anthropic import AsyncAnthropic

    from ownevo_kernel.nl_gen import replay_set

    spec = FIXTURES[fixture_id]
    plan = SIM_PLAN_FIXTURES[fixture_id]
    client = AsyncAnthropic()
    case_set = await generate_eval_case_set(client, spec, plan, model=_LIVE_MODEL)

    assert isinstance(case_set, EvalCaseSet)
    assert case_set.workflow_spec_id == spec.id
    assert 10 <= len(case_set.cases) <= 30

    # Replay-equivalence: every case the live model emitted produces a
    # ReplayResult — the LLM might disagree with the sim's actual labels
    # (i.e. case.passed=False), but no case may raise a structural error.
    results = replay_set(case_set, plan, spec)
    assert len(results) == len(case_set.cases)
