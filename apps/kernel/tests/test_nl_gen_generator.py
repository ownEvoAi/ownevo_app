"""Tests for `workflow_spec_generator.generate_workflow_spec`.

Two layers:
  1. Fake AsyncAnthropic client — deterministic, no network. Covers the
     contract: tool_use → WorkflowSpec, no tool_use → NoToolUseError,
     malformed input → WorkflowSpecValidationError. Also pins the tool
     definition shape so a future schema change doesn't silently break
     Claude's input_schema.
  2. Live API snapshot — gated behind `OWNEVO_ANTHROPIC_LIVE=1`. Generates
     against the 3 fixture descriptions and asserts the *structural shape*
     of the response matches what the mock UI renders. We do not assert
     verbatim text — Claude paraphrases, that's fine; what matters is that
     downstream stages (sim_generator, eval_generator, metric_generator)
     see the same shape they see from the hand-authored fixtures.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.nl_gen import (
    NoToolUseError,
    WorkflowSpec,
    WorkflowSpecValidationError,
    generate_workflow_spec,
)
from ownevo_kernel.nl_gen.fixtures import DESCRIPTIONS, FIXTURES
from ownevo_kernel.nl_gen.workflow_spec_generator import (
    SYSTEM_PROMPT,
    TOOL_NAME,
    _TOOL_DEFINITION,
)


# ---------------------------------------------------------------------------
# Fake AsyncAnthropic client — minimal slice the generator uses
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedResponse:
    """One canned `messages.create` response."""

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
    # The tool takes a single `spec` parameter wrapping the WorkflowSpec —
    # matches small-model nesting behavior, larger models accept it fine.
    assert schema["required"] == ["spec"]
    spec_schema = schema["properties"]["spec"]
    assert spec_schema["type"] == "object"
    # Required keys inside the wrapped WorkflowSpec. `description` is
    # intentionally absent — the user's input lives on workflows.description,
    # not duplicated in the spec.
    required = set(spec_schema["required"])
    for f in (
        "id", "domain", "environment", "tools",
        "reviewer", "success_criterion", "ui",
    ):
        assert f in required
    assert "description" not in required


def test_system_prompt_mentions_provenance_and_kebab_case():
    """The prompt is the contract that anchors the generator's output —
    pin the most load-bearing rules so a future edit can't silently drop them."""
    assert "Provenance" in SYSTEM_PROMPT
    assert "derived" in SYSTEM_PROMPT
    assert "inferred" in SYSTEM_PROMPT
    assert "known_past_misses" in SYSTEM_PROMPT
    assert "kebab-case" in SYSTEM_PROMPT
    assert "schema_version" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Pass: tool_use returns a valid spec
# ---------------------------------------------------------------------------


async def test_tool_use_returning_valid_spec_round_trips():
    fixture = FIXTURES["demand-prediction"]
    payload = json.loads(fixture.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, {"spec": payload})])
    )
    result = await generate_workflow_spec(client, "describe me")
    assert isinstance(result, WorkflowSpec)
    assert result == fixture


async def test_generator_passes_description_as_user_message():
    fixture = FIXTURES["demand-prediction"]
    payload = json.loads(fixture.model_dump_json())
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, {"spec": payload})])
    )
    await generate_workflow_spec(client, "the user description")
    kw = client.messages.last_kwargs
    assert kw["messages"] == [{"role": "user", "content": "the user description"}]
    assert kw["system"] == SYSTEM_PROMPT
    assert kw["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert len(kw["tools"]) == 1
    assert kw["tools"][0]["name"] == TOOL_NAME


# ---------------------------------------------------------------------------
# Fail: no tool_use
# ---------------------------------------------------------------------------


async def test_text_only_response_raises_no_tool_use():
    client = _FakeClient(
        _ScriptedResponse(
            content=[_text_block("I think the workflow is...")],
            stop_reason="end_turn",
        )
    )
    with pytest.raises(NoToolUseError) as exc_info:
        await generate_workflow_spec(client, "describe me")
    assert exc_info.value.stop_reason == "end_turn"
    assert "I think the workflow is..." in exc_info.value.content_preview


async def test_wrong_tool_name_raises_no_tool_use():
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block("some_other_tool", {"x": 1})],
            stop_reason="tool_use",
        )
    )
    with pytest.raises(NoToolUseError):
        await generate_workflow_spec(client, "describe me")


# ---------------------------------------------------------------------------
# Fail: tool_use with malformed input
# ---------------------------------------------------------------------------


async def test_invalid_tool_input_raises_validation_error():
    bad = {"id": "x", "description": "y"}  # missing every other required field
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, bad)])
    )
    with pytest.raises(WorkflowSpecValidationError) as exc_info:
        await generate_workflow_spec(client, "describe me")
    assert exc_info.value.raw_input == bad
    assert exc_info.value.pydantic_error.error_count() > 0


async def test_extra_field_in_tool_input_raises_validation_error():
    fixture = FIXTURES["demand-prediction"]
    payload = json.loads(fixture.model_dump_json())
    payload["bonus_field"] = "claude invented this"
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, {"spec": payload})])
    )
    with pytest.raises(WorkflowSpecValidationError):
        await generate_workflow_spec(client, "describe me")


async def test_flat_tool_input_without_spec_wrapper_round_trips():
    """Model emits spec un-wrapped at top level (no 'spec' key) — fallback path."""
    fixture = FIXTURES["demand-prediction"]
    payload = json.loads(fixture.model_dump_json())  # no {"spec": ...} wrapper
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, payload)])
    )
    result = await generate_workflow_spec(client, "describe me")
    assert isinstance(result, WorkflowSpec)
    assert result == fixture


# ---------------------------------------------------------------------------
# Live API snapshot — gated by OWNEVO_ANTHROPIC_LIVE
#
# We don't assert verbatim text — Claude paraphrases. We assert the
# *structural shape* matches what the mock UI renders.
# ---------------------------------------------------------------------------


_LIVE = pytest.mark.skipif(
    os.environ.get("OWNEVO_ANTHROPIC_LIVE") != "1",
    reason="set OWNEVO_ANTHROPIC_LIVE=1 to exercise the real Anthropic API",
)


_LIVE_MODEL = os.environ.get(
    "OWNEVO_NL_GEN_LIVE_MODEL", "claude-haiku-4-5-20251001"
)
"""Model for live snapshot tests. Haiku 4.5 by default — capable enough for
structured tool-use against a typed JSON schema, available on the broadest
account tiers (Sonnet/Opus access varies by workspace). Override via
OWNEVO_NL_GEN_LIVE_MODEL to verify against Sonnet or Opus where available."""


_PLAUSIBLE_DOMAINS = {
    # Some descriptions overlap categories. Assert the model picked a
    # domain in a small, reasonable set rather than locking the fixture's
    # one-true-answer (which is fine for hand-authored fixtures but
    # over-constrained for an LLM that's reading the same words).
    "demand-prediction": {"supply-chain"},
    "credit-risk": {"credit-risk"},
    "contract-review": {"legal", "labour"},  # union-contract review reads as either
}


@_LIVE
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_id", list(FIXTURES.keys()))
async def test_live_generates_structurally_valid_spec(fixture_id):
    from anthropic import AsyncAnthropic

    description = DESCRIPTIONS[fixture_id]
    fixture = FIXTURES[fixture_id]
    client = AsyncAnthropic()
    result = await generate_workflow_spec(
        client, description, model=_LIVE_MODEL
    )

    # Same minimum shape every fixture satisfies.
    assert len(result.tools) >= 3
    assert len(result.environment.personas) >= 1
    assert len(result.environment.env_generators) >= 1
    assert result.success_criterion.target_metric_name
    assert result.ui.tabs and result.ui.tabs[0].primitives
    # Every tool must carry provenance — the demo's load-bearing claim.
    for tool in result.tools:
        assert tool.provenance is not None
    # Domain steering — must land in the plausible set for this description.
    assert result.domain in _PLAUSIBLE_DOMAINS[fixture_id], (
        f"domain={result.domain!r} not in {_PLAUSIBLE_DOMAINS[fixture_id]}"
    )
    # Past-miss extraction worked.
    assert len(result.known_past_misses) >= 1
