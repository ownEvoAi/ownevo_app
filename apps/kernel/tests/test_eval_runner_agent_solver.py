"""Tests for `eval_runner.agent_solver` (A4.4).

Mirrors the fake-AsyncAnthropic pattern from A4.1's eval_generator
tests. Pins:

  * Tool definition shape + system prompt load-bearing rules.
  * Redaction correctness — the target event's label_field becomes
    REDACTED_TOKEN; past events keep their labels untouched.
  * Trajectory visibility — agent sees events 0..target_step_index.
  * Pass / no-tool-use / wrong-tool-name / malformed-input error paths.
  * Cross-check failures (workflow_spec_id mismatch).
  * solve_with_agent end-to-end with a scripted client that emits
    correct predictions → every case passes; flipped predictions →
    every case fails (proves wiring carries actual_value through).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.eval_runner.agent_solver import (
    REDACTED_TOKEN,
    SYSTEM_PROMPT,
    TOOL_INPUT_SCHEMA,
    TOOL_NAME,
    AgentPrediction,
    AgentSolverError,
    NoPredictToolUseError,
    PredictToolValidationError,
    _exec_sim_namespace,
    _format_user_message,
    _redact_target_event,
    predict_one,
    solve_with_agent,
)
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)


# ---------------------------------------------------------------------------
# Fake AsyncAnthropic
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedResponse:
    content: list[Any]
    stop_reason: str = "tool_use"


class _ScriptedMessages:
    """Cycles through a list of scripted responses (one per call)."""

    def __init__(self, responses: list[_ScriptedResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("client received more calls than scripted")
        response = self._responses.pop(0)
        return SimpleNamespace(
            content=response.content, stop_reason=response.stop_reason
        )


@dataclass
class _ScriptedClient:
    responses: list[_ScriptedResponse]
    messages: _ScriptedMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _ScriptedMessages(self.responses)


def _tool_use(name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload, id="tu_1")


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _predict_response(value: bool, rationale: str = "scripted") -> _ScriptedResponse:
    return _ScriptedResponse(
        content=[_tool_use(TOOL_NAME, {"value": value, "rationale": rationale})]
    )


# ---------------------------------------------------------------------------
# Tool definition + system prompt pinning
# ---------------------------------------------------------------------------


def test_tool_input_schema_shape():
    s = TOOL_INPUT_SCHEMA
    assert s["type"] == "object"
    assert s["additionalProperties"] is False
    assert set(s["required"]) == {"value", "rationale"}
    assert s["properties"]["value"]["type"] == "boolean"
    assert s["properties"]["rationale"]["type"] == "string"


def test_system_prompt_pins_load_bearing_rules():
    p = SYSTEM_PROMPT
    assert TOOL_NAME in p
    assert REDACTED_TOKEN in p
    assert "predict" in p.lower()
    # The two error-mode framings the prompt is supposed to teach
    assert "precision" in p.lower()
    assert "recall" in p.lower()
    # Metric-aware tie-breaker — load-bearing for sparse-True workflows
    # whose target metric is recall (haiku 4.5 default-False bias bug,
    # observed 2026-05-05).
    assert "gate-metric framing" in p.lower()


def test_metric_framing_block_for_recall_workflow():
    from ownevo_kernel.eval_runner.agent_solver import _metric_framing

    metric = METRIC_FIXTURES["demand-prediction"]
    assert metric.family == "recall"
    block = _metric_framing(metric)
    assert "recall" in block
    assert f"{metric.target_value:.2f}" in block
    # The recall-specific tie-breaker the prompt fix is supposed to inject.
    assert "False Negative" in block
    assert "True" in block  # the recommended tie-breaker direction


def test_metric_framing_block_for_precision_workflow():
    from ownevo_kernel.eval_runner.agent_solver import _metric_framing
    from ownevo_kernel.nl_gen import MetricDefinition

    precision_metric = MetricDefinition.model_validate(
        {
            "schema_version": "0.1",
            "workflow_spec_id": "any-workflow",
            "name": "demo-precision",
            "family": "precision",
            "direction": "maximize",
            "lower_bound": 0.0,
            "upper_bound": 1.0,
            "target_value": 0.85,
            "description": "Synthetic precision metric.",
            "rationale": "test fixture",
            "provenance": {"kind": "inferred", "source": "test"},
        }
    )
    block = _metric_framing(precision_metric)
    assert "precision" in block
    assert "False Positive" in block


def test_metric_framing_covers_every_supported_family():
    from ownevo_kernel.eval_runner.agent_solver import (
        _METRIC_FAMILY_GUIDANCE,
    )
    from typing import get_args
    from ownevo_kernel.nl_gen import MetricFamily

    assert set(_METRIC_FAMILY_GUIDANCE.keys()) == set(get_args(MetricFamily))


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_target_event_replaces_only_last_event_label():
    events = [
        {"step": 0, "demand": 100, "alert_correct_label": False},
        {"step": 1, "demand": 90, "alert_correct_label": False},
        {"step": 2, "demand": 50, "alert_correct_label": True},
    ]
    redacted = _redact_target_event(events, "alert_correct_label")
    assert redacted[0] == events[0]
    assert redacted[1] == events[1]
    assert redacted[2]["alert_correct_label"] == REDACTED_TOKEN
    # Other fields on the target event preserved
    assert redacted[2]["step"] == 2
    assert redacted[2]["demand"] == 50


def test_redact_does_not_mutate_input():
    events = [{"step": 0, "label": True}]
    _ = _redact_target_event(events, "label")
    assert events[0]["label"] is True


def test_redact_empty_list_returns_empty():
    assert _redact_target_event([], "anything") == []


# ---------------------------------------------------------------------------
# User message assembly
# ---------------------------------------------------------------------------


def test_format_user_message_includes_workflow_id_and_redacts_target():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]
    case = case_set.cases[0]

    ns = _exec_sim_namespace(plan, spec)
    trajectory = ns["run_simulation"](case.sim_seed, case.n_steps)["trajectory"]
    events = trajectory[: case.target_step_index + 1]
    msg = _format_user_message(spec, case, events, metric)

    assert spec.id in msg
    assert spec.domain in msg
    assert REDACTED_TOKEN in msg
    assert case.target_label_field in msg
    assert str(case.target_step_index) in msg
    # Gate-metric framing surfaces
    assert metric.family in msg
    assert "Gate metric" in msg
    # Tool vocabulary surfaces
    for tool in spec.tools:
        assert tool.name in msg


def test_format_user_message_keeps_past_event_labels_visible():
    """Earlier-trajectory labels are training signal — they MUST appear
    in the prompt unredacted."""
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]
    # Pick the case with the largest target_step_index → most past events.
    case = max(case_set.cases, key=lambda c: c.target_step_index)
    assert case.target_step_index >= 1, "fixture should include a past event"

    ns = _exec_sim_namespace(plan, spec)
    trajectory = ns["run_simulation"](case.sim_seed, case.n_steps)["trajectory"]
    events = trajectory[: case.target_step_index + 1]
    msg = _format_user_message(spec, case, events, metric)

    # The target field is redacted on the last event; check that ANY
    # past event has a True or False bool label rendered (i.e. the
    # whole field wasn't accidentally globally-redacted).
    past_payload = json.dumps(events[:-1], default=str)
    assert ("true" in past_payload.lower()) or ("false" in past_payload.lower())


# ---------------------------------------------------------------------------
# predict_one
# ---------------------------------------------------------------------------


async def test_predict_one_returns_agent_prediction():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]

    ns = _exec_sim_namespace(plan, spec)
    client = _ScriptedClient([_predict_response(True, "looks like a markdown week")])

    result = await predict_one(client, case, spec=spec, metric=METRIC_FIXTURES[workflow_id], namespace=ns)

    assert isinstance(result, AgentPrediction)
    assert result.case_id == case.case_id
    assert result.value is True
    assert result.rationale == "looks like a markdown week"


async def test_predict_one_passes_tool_definition_and_system_prompt():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]

    ns = _exec_sim_namespace(plan, spec)
    client = _ScriptedClient([_predict_response(False)])
    await predict_one(client, case, spec=spec, metric=METRIC_FIXTURES[workflow_id], namespace=ns)

    kw = client.messages.calls[0]
    assert kw["system"] == SYSTEM_PROMPT
    assert kw["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert kw["tools"][0]["name"] == TOOL_NAME
    assert kw["tools"][0]["input_schema"] == TOOL_INPUT_SCHEMA


async def test_predict_one_no_tool_use_raises():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]

    ns = _exec_sim_namespace(plan, spec)
    client = _ScriptedClient(
        [_ScriptedResponse(content=[_text("I think it's True...")], stop_reason="end_turn")]
    )
    with pytest.raises(NoPredictToolUseError) as exc_info:
        await predict_one(client, case, spec=spec, metric=METRIC_FIXTURES[workflow_id], namespace=ns)
    assert exc_info.value.stop_reason == "end_turn"
    assert "True" in exc_info.value.content_preview


async def test_predict_one_wrong_tool_name_raises():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]

    ns = _exec_sim_namespace(plan, spec)
    client = _ScriptedClient(
        [_ScriptedResponse(content=[_tool_use("other_tool", {"value": True})])]
    )
    with pytest.raises(NoPredictToolUseError):
        await predict_one(client, case, spec=spec, metric=METRIC_FIXTURES[workflow_id], namespace=ns)


async def test_predict_one_non_bool_value_raises():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]

    ns = _exec_sim_namespace(plan, spec)
    client = _ScriptedClient(
        [_ScriptedResponse(content=[_tool_use(TOOL_NAME, {"value": 1, "rationale": "x"})])]
    )
    with pytest.raises(PredictToolValidationError, match="bool"):
        await predict_one(client, case, spec=spec, metric=METRIC_FIXTURES[workflow_id], namespace=ns)


async def test_predict_one_missing_rationale_raises():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]

    ns = _exec_sim_namespace(plan, spec)
    client = _ScriptedClient(
        [_ScriptedResponse(content=[_tool_use(TOOL_NAME, {"value": True})])]
    )
    with pytest.raises(PredictToolValidationError, match="rationale"):
        await predict_one(client, case, spec=spec, metric=METRIC_FIXTURES[workflow_id], namespace=ns)


async def test_predict_one_empty_rationale_rejected():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]

    ns = _exec_sim_namespace(plan, spec)
    client = _ScriptedClient(
        [_ScriptedResponse(content=[_tool_use(TOOL_NAME, {"value": True, "rationale": "  "})])]
    )
    with pytest.raises(PredictToolValidationError):
        await predict_one(client, case, spec=spec, metric=METRIC_FIXTURES[workflow_id], namespace=ns)


async def test_predict_one_non_dict_input_raises():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case = EVAL_CASE_SET_FIXTURES[workflow_id].cases[0]

    ns = _exec_sim_namespace(plan, spec)
    client = _ScriptedClient(
        [_ScriptedResponse(content=[_tool_use(TOOL_NAME, "string-not-dict")])]
    )
    with pytest.raises(PredictToolValidationError):
        await predict_one(client, case, spec=spec, metric=METRIC_FIXTURES[workflow_id], namespace=ns)


# ---------------------------------------------------------------------------
# solve_with_agent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", list(FIXTURES.keys()))
async def test_solve_with_agent_returns_one_result_per_case(workflow_id):
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]

    # Script: agent always predicts True.
    client = _ScriptedClient([_predict_response(True) for _ in case_set.cases])
    results = await solve_with_agent(client, case_set, plan, spec, METRIC_FIXTURES[workflow_id])

    assert len(results) == len(case_set.cases)
    assert [r.case_id for r in results] == [c.case_id for c in case_set.cases]


async def test_perfect_predictions_yield_all_pass():
    """Script the agent to predict each case's expected_value exactly."""
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]

    client = _ScriptedClient(
        [_predict_response(c.expected_value) for c in case_set.cases]
    )
    results = await solve_with_agent(client, case_set, plan, spec, METRIC_FIXTURES[workflow_id])
    assert all(r.passed for r in results)


async def test_inverted_predictions_yield_all_fail():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]

    client = _ScriptedClient(
        [_predict_response(not c.expected_value) for c in case_set.cases]
    )
    results = await solve_with_agent(client, case_set, plan, spec, METRIC_FIXTURES[workflow_id])
    assert not any(r.passed for r in results)


async def test_actual_value_is_agent_prediction_not_sim_label():
    """Wiring check: actual_value carried into ReplayResult comes from
    the agent, not from re-reading the sim's hidden label."""
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]

    # Force every prediction to True regardless of expected_value.
    client = _ScriptedClient([_predict_response(True) for _ in case_set.cases])
    results = await solve_with_agent(client, case_set, plan, spec, METRIC_FIXTURES[workflow_id])
    assert all(r.actual_value is True for r in results)
    assert all(r.expected_value == c.expected_value
               for r, c in zip(results, case_set.cases))


# ---------------------------------------------------------------------------
# Cross-check failures
# ---------------------------------------------------------------------------


async def test_case_set_workflow_id_mismatch_raises():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["credit-risk"]
    metric = METRIC_FIXTURES["demand-prediction"]
    client = _ScriptedClient([])
    with pytest.raises(ValueError, match="workflow_spec_id"):
        await solve_with_agent(client, case_set, plan, spec, metric)
    assert client.messages.calls == []  # no API hit


async def test_plan_workflow_id_mismatch_raises():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["credit-risk"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]
    client = _ScriptedClient([])
    with pytest.raises(ValueError, match="workflow_spec_id"):
        await solve_with_agent(client, case_set, plan, spec, metric)
    assert client.messages.calls == []


async def test_metric_workflow_id_mismatch_raises():
    spec = FIXTURES["demand-prediction"]
    plan = SIM_PLAN_FIXTURES["demand-prediction"]
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["credit-risk"]
    client = _ScriptedClient([])
    with pytest.raises(ValueError, match="workflow_spec_id"):
        await solve_with_agent(client, case_set, plan, spec, metric)
    assert client.messages.calls == []


async def test_target_step_past_trajectory_raises_agent_solver_error():
    workflow_id = "demand-prediction"
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    # Construct a synthetic case targeting beyond trajectory length.
    base_case = case_set.cases[0]
    huge = base_case.model_copy(
        update={"n_steps": 5, "target_step_index": 4}
    )
    # Override sim's run to return a 1-event trajectory.
    ns = _exec_sim_namespace(plan, spec)
    original = ns["run_simulation"]

    def _short(seed, n):  # noqa: ARG001
        return {"trajectory": [{"step": 0, base_case.target_label_field: True}]}

    ns["run_simulation"] = _short
    client = _ScriptedClient([])  # no calls expected
    with pytest.raises(AgentSolverError, match="past the trajectory"):
        await predict_one(client, huge, spec=spec, metric=METRIC_FIXTURES[workflow_id], namespace=ns)
    # Restore (defensive — namespace is local to this test)
    ns["run_simulation"] = original
