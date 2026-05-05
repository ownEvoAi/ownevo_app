"""Tests for `nl_gen.sim_generator.generate_simulation_plan`.

Mirrors `test_nl_gen_generator.py` (A3.1):
  1. Fake AsyncAnthropic — deterministic, no network. Pins the tool
     definition shape, the system prompt's load-bearing rules, and the
     contract on tool_use → SimulationPlan + the two error paths.
  2. Live API snapshot — gated by `OWNEVO_ANTHROPIC_LIVE=1`. Generates
     against the demand-prediction WorkflowSpec, asserts structural shape.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.nl_gen import (
    NoSimToolUseError,
    SimulationPlan,
    SimulationPlanValidationError,
    generate_simulation_plan,
)
from ownevo_kernel.nl_gen.fixtures import (
    FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.nl_gen.sim_generator import (
    SYSTEM_PROMPT,
    TOOL_NAME,
    _TOOL_DEFINITION,
)


# ---------------------------------------------------------------------------
# Fake AsyncAnthropic — same shape as test_nl_gen_generator.py
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
    assert schema["required"] == ["plan"]
    plan_schema = schema["properties"]["plan"]
    assert plan_schema["type"] == "object"
    required = set(plan_schema["required"])
    for f in (
        "workflow_spec_id",
        "description",
        "init_state_code",
        "step_code",
        "event_fields",
    ):
        assert f in required


def test_system_prompt_pins_load_bearing_rules():
    """A future edit can't silently drop the determinism + import rules."""
    assert "DETERMINISTIC" in SYSTEM_PROMPT
    assert "rng" in SYSTEM_PROMPT
    assert "init_state_code" in SYSTEM_PROMPT
    assert "step_code" in SYSTEM_PROMPT
    assert "event_fields" in SYSTEM_PROMPT
    assert "schema_version" in SYSTEM_PROMPT
    # The prompt must mention the safety surface so the model knows what
    # the renderer rejects.
    assert "eval" in SYSTEM_PROMPT
    assert "exec" in SYSTEM_PROMPT
    assert "__import__" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Pass: tool_use → SimulationPlan
# ---------------------------------------------------------------------------


async def test_tool_use_returning_valid_plan_round_trips():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    payload = json.loads(plan.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, {"plan": payload})])
    )
    result = await generate_simulation_plan(client, spec)
    assert isinstance(result, SimulationPlan)
    assert result == plan


async def test_generator_passes_workflow_spec_as_user_message():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    payload = json.loads(plan.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, {"plan": payload})])
    )
    await generate_simulation_plan(client, spec)
    kw = client.messages.last_kwargs
    assert len(kw["messages"]) == 1
    assert kw["messages"][0]["role"] == "user"
    # The user message includes the workflow spec id — this is the
    # observable contract: future templating changes can be checked
    # against the persisted spec id.
    assert spec.id in kw["messages"][0]["content"]
    assert kw["system"] == SYSTEM_PROMPT
    assert kw["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert len(kw["tools"]) == 1
    assert kw["tools"][0]["name"] == TOOL_NAME


async def test_flat_tool_input_without_plan_wrapper_round_trips():
    """Some models emit the plan un-wrapped — accept either shape."""
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    payload = json.loads(plan.model_dump_json())  # no {"plan": ...} wrapper
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, payload)])
    )
    result = await generate_simulation_plan(client, spec)
    assert result == plan


# ---------------------------------------------------------------------------
# Fail: no tool_use
# ---------------------------------------------------------------------------


async def test_text_only_response_raises_no_tool_use():
    spec = FIXTURES["demand-prediction"]
    client = _FakeClient(
        _ScriptedResponse(
            content=[_text_block("I'd build the sim like this...")],
            stop_reason="end_turn",
        )
    )
    with pytest.raises(NoSimToolUseError) as exc_info:
        await generate_simulation_plan(client, spec)
    assert exc_info.value.stop_reason == "end_turn"
    assert "I'd build the sim like this..." in exc_info.value.content_preview


async def test_wrong_tool_name_raises_no_tool_use():
    spec = FIXTURES["demand-prediction"]
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block("emit_workflow_spec", {"x": 1})],
            stop_reason="tool_use",
        )
    )
    with pytest.raises(NoSimToolUseError):
        await generate_simulation_plan(client, spec)


# ---------------------------------------------------------------------------
# Fail: tool_use with malformed input
# ---------------------------------------------------------------------------


async def test_invalid_tool_input_raises_validation_error():
    spec = FIXTURES["demand-prediction"]
    bad = {"workflow_spec_id": "x"}  # missing every other required field
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, bad)])
    )
    with pytest.raises(SimulationPlanValidationError) as exc_info:
        await generate_simulation_plan(client, spec)
    assert exc_info.value.raw_input == bad
    assert exc_info.value.pydantic_error.error_count() > 0


async def test_extra_field_in_tool_input_raises_validation_error():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    payload = json.loads(plan.model_dump_json())
    payload["bonus_field"] = "claude invented this"
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, {"plan": payload})])
    )
    with pytest.raises(SimulationPlanValidationError):
        await generate_simulation_plan(client, spec)


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
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_id", list(SIM_PLAN_FIXTURES.keys()))
async def test_live_generates_renderable_plan(fixture_id):
    """Plan generated against the real API renders, parses as a skill, and
    runs end-to-end with replay equivalence."""
    from anthropic import AsyncAnthropic

    from ownevo_kernel.nl_gen import render_simulation_module
    from ownevo_kernel.skills.format import parse_skill

    spec = FIXTURES[fixture_id]
    client = AsyncAnthropic()
    plan = await generate_simulation_plan(client, spec, model=_LIVE_MODEL)

    assert isinstance(plan, SimulationPlan)
    assert plan.workflow_spec_id == spec.id
    assert len(plan.event_fields) >= 2

    # Renders cleanly under the safety pass.
    content = render_simulation_module(plan, spec)
    record = parse_skill(content)
    assert record.frontmatter.kind == "python"

    # Runs end-to-end and is replay-equivalent. We exec the body in an
    # isolated namespace; this matches the in-process path the local-
    # docker sandbox would also support.
    ns: dict[str, Any] = {"__name__": "_sim_under_test"}
    exec(compile(record.body, f"<sim:{spec.id}>", "exec"), ns)
    a = ns["run_simulation"](seed=plan.seed_default, n_steps=10)
    b = ns["run_simulation"](seed=plan.seed_default, n_steps=10)
    assert a == b
    assert len(a["trajectory"]) == 10
