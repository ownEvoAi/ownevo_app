"""Unit tests for the trace-native WorkflowSpec generator.

Anthropic is mocked: the client returns a forced `emit_workflow_spec`
tool call wrapping a known-valid spec fixture. Asserts the generator
unwraps + validates it, and that the user message is grounded in the
trace summary + agent definition + design brief.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from ownevo_kernel.design_agent.trace_summary import summarize_events
from ownevo_kernel.nl_gen import WorkflowSpec
from ownevo_kernel.nl_gen.fixtures import DEMAND_PREDICTION_SPEC
from ownevo_kernel.nl_gen.workflow_spec_from_traces import (
    NoToolUseError,
    generate_workflow_spec_from_traces,
)


@dataclass
class _Block:
    type: str
    name: str
    input: dict
    id: str = "toolu_test"


@dataclass
class _Msg:
    content: list[Any]
    stop_reason: str = "tool_use"


class _SpecClient:
    """Returns a single emit_workflow_spec tool call with a valid spec."""

    def __init__(self, spec_payload: dict):
        self._payload = spec_payload
        self.last_call_kwargs: dict[str, Any] = {}

        outer = self

        class _Messages:
            async def create(self, **kwargs):
                outer.last_call_kwargs = kwargs
                return _Msg(
                    content=[
                        _Block(
                            type="tool_use",
                            name="emit_workflow_spec",
                            input={"spec": outer._payload},
                        )
                    ]
                )

        self.messages = _Messages()


class _NoToolClient:
    class messages:
        @staticmethod
        async def create(**_):
            return _Msg(content=[], stop_reason="end_turn")


def _summary():
    events = [
        {"type": "tool_call_start", "name": "forecast_demand", "args": {"sku": "A1"}},
        {"type": "tool_call_result", "name": "forecast_demand", "status": "ok",
         "output": {"units": 120}},
    ]
    return summarize_events([(uuid4(), events)])


async def test_generates_valid_spec_from_traces():
    payload = json.loads(DEMAND_PREDICTION_SPEC.model_dump_json())
    client = _SpecClient(payload)
    spec = await generate_workflow_spec_from_traces(
        client, _summary(), agent_definition="Flag risky SKUs."
    )
    assert isinstance(spec, WorkflowSpec)
    assert spec.id == DEMAND_PREDICTION_SPEC.id


async def test_user_message_grounds_in_summary_definition_and_brief():
    payload = json.loads(DEMAND_PREDICTION_SPEC.model_dump_json())
    client = _SpecClient(payload)
    brief = "## Design-agent answers from the operator\n- **Success metric**\n  A: Recall"
    await generate_workflow_spec_from_traces(
        client,
        _summary(),
        agent_definition="Flag risky SKUs in the catalog.",
        design_brief_block=brief,
    )
    msg = client.last_call_kwargs["messages"][0]["content"]
    assert "forecast_demand" in msg
    assert "Flag risky SKUs in the catalog." in msg
    assert "Success metric" in msg


async def test_raises_when_model_skips_tool():
    with pytest.raises(NoToolUseError):
        await generate_workflow_spec_from_traces(_NoToolClient(), _summary())
