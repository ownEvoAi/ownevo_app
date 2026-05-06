"""Tests for `nl_gen.pipeline.generate_full_pipeline` (A4.4).

The pipeline is just sequencing — every cross-step contract is enforced
by the underlying generators. These tests pin:

  * Sequence: 4 calls, in order (workflow_spec → sim_plan → eval_case_set
    → metric_definition).
  * Each generator's output is fed verbatim into the next as the
    relevant typed argument (no paraphrasing in between).
  * model + max_tokens overrides apply uniformly to all four calls.
  * Underlying typed errors propagate without wrapping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.nl_gen import (
    NLGenPipelineResult,
    generate_full_pipeline,
)
from ownevo_kernel.nl_gen.eval_generator import TOOL_NAME as EVAL_TOOL_NAME
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.nl_gen.metric_generator import TOOL_NAME as METRIC_TOOL_NAME
from ownevo_kernel.nl_gen.sim_generator import TOOL_NAME as SIM_TOOL_NAME
from ownevo_kernel.nl_gen.workflow_spec_generator import TOOL_NAME as SPEC_TOOL_NAME


_TOOLS_IN_ORDER = [SPEC_TOOL_NAME, SIM_TOOL_NAME, EVAL_TOOL_NAME, METRIC_TOOL_NAME]
# Each generator's tool_input wrapper key (mirrors the keys the generators
# unwrap on tool_use → typed model). See spec_payload = raw_input["spec"]
# pattern in each generator module.
_WRAPPER_KEYS_IN_ORDER = [
    "spec",                # workflow_spec_generator
    "plan",                # sim_generator
    "eval_case_set",       # eval_generator
    "metric_definition",   # metric_generator
]


@dataclass
class _ScriptedMessages:
    payloads_in_order: list[dict]
    calls: list[dict] = field(default_factory=list)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        if idx >= len(self.payloads_in_order):
            raise AssertionError(
                f"pipeline made more calls ({len(self.calls)}) than scripted "
                f"({len(self.payloads_in_order)})"
            )
        payload = self.payloads_in_order[idx]
        wrapper = _WRAPPER_KEYS_IN_ORDER[idx]
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name=_TOOLS_IN_ORDER[idx],
                    input={wrapper: payload},
                    id=f"tu_{idx}",
                )
            ],
            stop_reason="tool_use",
        )


@dataclass
class _ScriptedClient:
    payloads_in_order: list[dict]
    messages: _ScriptedMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _ScriptedMessages(self.payloads_in_order)


def _payloads_for(workflow_id: str) -> list[dict]:
    """Serialize the four fixtures for `workflow_id` into JSON-roundtripped
    dicts (mimicking what Anthropic's tool_use input would carry)."""
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]
    return [
        json.loads(spec.model_dump_json()),
        json.loads(plan.model_dump_json()),
        json.loads(case_set.model_dump_json()),
        json.loads(metric.model_dump_json()),
    ]


# ---------------------------------------------------------------------------
# Happy path × 3 fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
async def test_pipeline_returns_typed_quartet(workflow_id):
    client = _ScriptedClient(_payloads_for(workflow_id))

    result = await generate_full_pipeline(client, "any description")

    assert isinstance(result, NLGenPipelineResult)
    assert result.workflow_spec == FIXTURES[workflow_id]
    assert result.simulation_plan == SIM_PLAN_FIXTURES[workflow_id]
    assert result.eval_case_set == EVAL_CASE_SET_FIXTURES[workflow_id]
    assert result.metric_definition == METRIC_FIXTURES[workflow_id]


async def test_pipeline_makes_exactly_four_calls():
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_payloads_for(workflow_id))
    await generate_full_pipeline(client, "any description")
    assert len(client.messages.calls) == 4


async def test_call_order_matches_documented_sequence():
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_payloads_for(workflow_id))
    await generate_full_pipeline(client, "any description")

    tool_names_in_order = [c["tools"][0]["name"] for c in client.messages.calls]
    assert tool_names_in_order == _TOOLS_IN_ORDER


async def test_first_call_carries_description_in_user_message():
    workflow_id = "demand-prediction"
    description = "specific test description that should be visible"
    client = _ScriptedClient(_payloads_for(workflow_id))
    await generate_full_pipeline(client, description)

    first_user_content = client.messages.calls[0]["messages"][0]["content"]
    assert description in first_user_content


async def test_second_call_passes_workflow_spec_into_sim_generator():
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_payloads_for(workflow_id))
    await generate_full_pipeline(client, "any description")

    sim_call_user_content = client.messages.calls[1]["messages"][0]["content"]
    spec = FIXTURES[workflow_id]
    assert spec.id in sim_call_user_content


async def test_third_call_passes_spec_and_plan_into_eval_generator():
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_payloads_for(workflow_id))
    await generate_full_pipeline(client, "any description")

    eval_call_user_content = client.messages.calls[2]["messages"][0]["content"]
    spec = FIXTURES[workflow_id]
    assert spec.id in eval_call_user_content
    assert "WorkflowSpec" in eval_call_user_content
    assert "SimulationPlan" in eval_call_user_content


async def test_fourth_call_passes_spec_into_metric_generator():
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_payloads_for(workflow_id))
    await generate_full_pipeline(client, "any description")

    metric_call_user_content = client.messages.calls[3]["messages"][0]["content"]
    spec = FIXTURES[workflow_id]
    assert spec.id in metric_call_user_content


# ---------------------------------------------------------------------------
# Override propagation
# ---------------------------------------------------------------------------


async def test_model_override_applied_to_every_call():
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_payloads_for(workflow_id))
    await generate_full_pipeline(
        client, "any description", model="claude-haiku-4-5-20251001"
    )
    for call in client.messages.calls:
        assert call["model"] == "claude-haiku-4-5-20251001"


async def test_max_tokens_override_applied_to_every_call():
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_payloads_for(workflow_id))
    await generate_full_pipeline(client, "any description", max_tokens=2048)
    for call in client.messages.calls:
        assert call["max_tokens"] == 2048


async def test_default_max_tokens_varies_by_step():
    """No override → each generator picks its own DEFAULT_MAX_TOKENS.

    Eval generator wants 12k (10-30 cases); metric generator wants 4k.
    Pinning these prevents a future generator-side default change from
    silently propagating through the pipeline."""
    workflow_id = "demand-prediction"
    client = _ScriptedClient(_payloads_for(workflow_id))
    await generate_full_pipeline(client, "any description")

    max_tokens_by_call = [c["max_tokens"] for c in client.messages.calls]
    # Spec + sim are 8k, eval is 12k, metric is 4k. The exact contract
    # is that calls 2 and 3 differ — eval has more headroom than metric.
    assert max_tokens_by_call[2] > max_tokens_by_call[3]


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


async def test_workflow_spec_validation_error_propagates(monkeypatch):
    """If step 1 fails validation, the pipeline raises and never calls step 2."""
    workflow_id = "demand-prediction"
    payloads = _payloads_for(workflow_id)
    payloads[0] = {"id": "x"}  # missing required fields
    client = _ScriptedClient(payloads)

    from ownevo_kernel.nl_gen import WorkflowSpecValidationError

    with pytest.raises(WorkflowSpecValidationError):
        await generate_full_pipeline(client, "any description")
    # Only one call attempted.
    assert len(client.messages.calls) == 1


async def test_metric_direction_mismatch_propagates_after_full_run():
    """If metric step emits a definition whose direction contradicts the
    spec's success_criterion, the pipeline raises after all 4 calls."""
    workflow_id = "demand-prediction"
    payloads = _payloads_for(workflow_id)
    # Flip metric direction.
    payloads[3]["direction"] = "minimize"
    client = _ScriptedClient(payloads)

    from ownevo_kernel.nl_gen import MetricDirectionMismatchError

    with pytest.raises(MetricDirectionMismatchError):
        await generate_full_pipeline(client, "any description")
    assert len(client.messages.calls) == 4
