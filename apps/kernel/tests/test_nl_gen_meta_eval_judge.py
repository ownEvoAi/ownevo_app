"""Tests for `nl_gen.meta_eval.judge.judge_artifacts` (A4.6).

Mirrors `test_nl_gen_metric_generator.py`:
  1. Fake AsyncAnthropic — pins tool definition shape, system-prompt
     load-bearing rules, the contract on tool_use → MetaEvalJudgment,
     and the four error paths (no tool_use, malformed input, extra
     field, spec-id mismatch).
  2. User-message payload — verifies the description, all four artifact
     JSONs, and the sim-body truncation marker land in the prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from ownevo_kernel.nl_gen.fixtures import (
    DESCRIPTIONS,
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.nl_gen.meta_eval.judge import (
    SIM_BODY_PREVIEW_CHARS,
    SYSTEM_PROMPT,
    TOOL_NAME,
    MetaEvalJudgmentValidationError,
    MetaEvalSpecIdMismatchError,
    NoMetaEvalToolUseError,
    _TOOL_DEFINITION,
    judge_artifacts,
)
from ownevo_kernel.nl_gen.meta_eval.judgment import MetaEvalJudgment


# ---------------------------------------------------------------------------
# Fake AsyncAnthropic
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


def _good_judgment_payload(spec_id: str = "demand-prediction") -> dict:
    return {
        "schema_version": "0.1",
        "workflow_spec_id": spec_id,
        "sim_coverage": {
            "verdict": "pass",
            "rationale": "All entities present.",
        },
        "eval_case_coverage": {
            "verdict": "pass",
            "rationale": "Past-misses covered.",
        },
        "metric_alignment": {
            "verdict": "pass",
            "rationale": "Recall family fits the past-miss.",
        },
        "overall_verdict": "good",
        "overall_rationale": "Bundle is aligned end-to-end with the description.",
    }


def _fixture_bundle(workflow_id: str = "demand-prediction"):
    return (
        DESCRIPTIONS[workflow_id],
        FIXTURES[workflow_id],
        SIM_PLAN_FIXTURES[workflow_id],
        EVAL_CASE_SET_FIXTURES[workflow_id],
        METRIC_FIXTURES[workflow_id],
    )


# ---------------------------------------------------------------------------
# Tool-definition pinning
# ---------------------------------------------------------------------------


def test_tool_definition_shape():
    td = _TOOL_DEFINITION
    assert td["name"] == TOOL_NAME
    assert td["description"]
    schema = td["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["judgment"]
    j_schema = schema["properties"]["judgment"]
    assert j_schema["type"] == "object"
    required = set(j_schema["required"])
    for f in (
        "workflow_spec_id",
        "sim_coverage",
        "eval_case_coverage",
        "metric_alignment",
        "overall_verdict",
        "overall_rationale",
    ):
        assert f in required


def test_system_prompt_pins_load_bearing_rules():
    """A future edit can't silently drop the dimension contract."""
    p = SYSTEM_PROMPT
    # Three dimensions named
    assert "sim_coverage" in p
    assert "eval_case_coverage" in p
    assert "metric_alignment" in p
    # Three-level discrete scale
    assert "pass" in p
    assert "partial" in p
    assert "fail" in p
    # Binary overall
    assert "good" in p
    assert "bad" in p
    # Past-miss / direction framing — load-bearing for the judge's calibration
    assert "past-miss" in p or "past_miss" in p
    # Calibration warning
    assert "balanced" in p or "calibrat" in p


# ---------------------------------------------------------------------------
# Pass: tool_use → MetaEvalJudgment
# ---------------------------------------------------------------------------


async def test_tool_use_returning_valid_judgment_round_trips():
    description, spec, plan, case_set, metric = _fixture_bundle()
    payload = _good_judgment_payload(spec.id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    result = await judge_artifacts(client, description, spec, plan, case_set, metric)
    assert isinstance(result, MetaEvalJudgment)
    assert result.workflow_spec_id == spec.id
    assert result.overall_verdict == "good"


async def test_flat_tool_input_without_wrapper_round_trips():
    """Some models emit the judgment un-wrapped — accept either shape."""
    description, spec, plan, case_set, metric = _fixture_bundle()
    payload = _good_judgment_payload(spec.id)
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, payload)])
    )
    result = await judge_artifacts(client, description, spec, plan, case_set, metric)
    assert result.workflow_spec_id == spec.id


async def test_user_message_carries_description_and_artifacts():
    description, spec, plan, case_set, metric = _fixture_bundle()
    payload = _good_judgment_payload(spec.id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    await judge_artifacts(client, description, spec, plan, case_set, metric)
    kw = client.messages.last_kwargs
    assert kw["system"] == SYSTEM_PROMPT
    assert kw["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert kw["tools"][0]["name"] == TOOL_NAME
    user_content = kw["messages"][0]["content"]
    # All four artifact section headers + the description
    assert "Original description" in user_content
    assert description in user_content
    assert "WorkflowSpec" in user_content
    assert "SimulationPlan" in user_content
    assert "EvalCaseSet" in user_content
    assert "MetricDefinition" in user_content
    # spec.id appears (in the WorkflowSpec JSON)
    assert spec.id in user_content


async def test_long_step_code_is_truncated_in_prompt():
    """step_code longer than SIM_BODY_PREVIEW_CHARS gets clipped + marker."""
    description, spec, plan, case_set, metric = _fixture_bundle()
    long_step = "x = 1\n" * (SIM_BODY_PREVIEW_CHARS // 2)  # comfortably over the cap
    plan_long = plan.model_copy(update={"step_code": long_step})
    payload = _good_judgment_payload(spec.id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    await judge_artifacts(client, description, spec, plan_long, case_set, metric)
    user_content = client.messages.last_kwargs["messages"][0]["content"]
    assert "truncated for judge prompt" in user_content


async def test_short_step_code_is_not_truncated():
    description, spec, plan, case_set, metric = _fixture_bundle()
    payload = _good_judgment_payload(spec.id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    await judge_artifacts(client, description, spec, plan, case_set, metric)
    user_content = client.messages.last_kwargs["messages"][0]["content"]
    # Real fixture step_code is well under 4kB — no truncation marker
    assert "truncated for judge prompt" not in user_content


async def test_judge_accepts_model_and_max_tokens_overrides():
    description, spec, plan, case_set, metric = _fixture_bundle()
    payload = _good_judgment_payload(spec.id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    await judge_artifacts(
        client,
        description,
        spec,
        plan,
        case_set,
        metric,
        model="claude-haiku-4-5",
        max_tokens=2_000,
    )
    kw = client.messages.last_kwargs
    assert kw["model"] == "claude-haiku-4-5"
    assert kw["max_tokens"] == 2_000


# ---------------------------------------------------------------------------
# Fail: no tool_use
# ---------------------------------------------------------------------------


async def test_text_only_response_raises_no_tool_use():
    description, spec, plan, case_set, metric = _fixture_bundle()
    client = _FakeClient(
        _ScriptedResponse(
            content=[_text_block("Here's what I'd say about this bundle...")],
            stop_reason="end_turn",
        )
    )
    with pytest.raises(NoMetaEvalToolUseError) as exc_info:
        await judge_artifacts(client, description, spec, plan, case_set, metric)
    assert exc_info.value.stop_reason == "end_turn"
    assert "what I'd say" in exc_info.value.content_preview


async def test_wrong_tool_name_raises_no_tool_use():
    description, spec, plan, case_set, metric = _fixture_bundle()
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block("not_the_judge_tool", {"x": 1})],
            stop_reason="tool_use",
        )
    )
    with pytest.raises(NoMetaEvalToolUseError):
        await judge_artifacts(client, description, spec, plan, case_set, metric)


# ---------------------------------------------------------------------------
# Fail: tool_use with malformed input
# ---------------------------------------------------------------------------


async def test_invalid_tool_input_raises_validation_error():
    description, spec, plan, case_set, metric = _fixture_bundle()
    bad = {"workflow_spec_id": spec.id}  # missing dimensions + verdict + rationale
    client = _FakeClient(
        _ScriptedResponse(content=[_tool_use_block(TOOL_NAME, {"judgment": bad})])
    )
    with pytest.raises(MetaEvalJudgmentValidationError) as exc_info:
        await judge_artifacts(client, description, spec, plan, case_set, metric)
    assert exc_info.value.raw_input == {"judgment": bad}
    assert exc_info.value.pydantic_error.error_count() > 0


async def test_extra_field_in_tool_input_raises_validation_error():
    description, spec, plan, case_set, metric = _fixture_bundle()
    payload = _good_judgment_payload(spec.id)
    payload["bonus_dimension"] = {"verdict": "pass", "rationale": "x"}
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    with pytest.raises(MetaEvalJudgmentValidationError):
        await judge_artifacts(client, description, spec, plan, case_set, metric)


# ---------------------------------------------------------------------------
# Fail: spec-id mismatch (cross-spec invariant)
# ---------------------------------------------------------------------------


async def test_spec_id_mismatch_raises_dedicated_error():
    """Judge copies the wrong workflow_spec_id — surface loudly because
    downstream audit-log joins on that id would silently break."""
    description, spec, plan, case_set, metric = _fixture_bundle()
    payload = _good_judgment_payload(spec_id="some-other-workflow")
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    with pytest.raises(MetaEvalSpecIdMismatchError) as exc_info:
        await judge_artifacts(client, description, spec, plan, case_set, metric)
    assert exc_info.value.expected_id == spec.id
    assert exc_info.value.judgment.workflow_spec_id == "some-other-workflow"


# ---------------------------------------------------------------------------
# Fixture parametrization — happy path on every existing workflow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", sorted(FIXTURES.keys()))
async def test_judge_happy_path_per_existing_fixture(workflow_id: str):
    description, spec, plan, case_set, metric = _fixture_bundle(workflow_id)
    payload = _good_judgment_payload(spec.id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    result = await judge_artifacts(client, description, spec, plan, case_set, metric)
    assert result.workflow_spec_id == spec.id


# ---------------------------------------------------------------------------
# Smoke: the prompt is sent as JSON-encoded dict (not raw repr)
# ---------------------------------------------------------------------------


async def test_user_message_artifacts_are_json_decodable():
    """Section content should parse as JSON so the judge sees structured data."""
    description, spec, plan, case_set, metric = _fixture_bundle()
    payload = _good_judgment_payload(spec.id)
    client = _FakeClient(
        _ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, {"judgment": payload})]
        )
    )
    await judge_artifacts(client, description, spec, plan, case_set, metric)
    user_content = client.messages.last_kwargs["messages"][0]["content"]
    # Pull each fenced ```json block and verify it parses
    blocks = []
    in_block = False
    cur: list[str] = []
    for line in user_content.splitlines():
        if line.strip() == "```json":
            in_block = True
            cur = []
            continue
        if line.strip() == "```" and in_block:
            blocks.append("\n".join(cur))
            in_block = False
            continue
        if in_block:
            cur.append(line)
    # 4 artifacts → 4 ```json blocks
    assert len(blocks) == 4
    for b in blocks:
        json.loads(b)
