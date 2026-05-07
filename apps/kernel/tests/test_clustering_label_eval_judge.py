"""Tests for `clustering.label_eval.judge.judge_label_match` (B3.5).

Mirrors `test_nl_gen_meta_eval_judge.py`:
  1. Fake AsyncAnthropic — pins tool definition shape, system-prompt
     load-bearing rules, the contract on tool_use → ClusterLabelJudgment,
     and the four error paths (no tool_use, malformed input, extra
     field, cluster-id mismatch).
  2. User-message payload — verifies the cluster_id, members, ground-truth,
     candidate label, and domain context all land in the prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from ownevo_kernel.clustering.label_eval.fixtures import (
    LABELED_CLUSTER_CASES,
)
from ownevo_kernel.clustering.label_eval.judge import (
    _TOOL_DEFINITION,
    SYSTEM_PROMPT,
    TOOL_NAME,
    ClusterLabelIdMismatchError,
    ClusterLabelJudgmentValidationError,
    NoClusterLabelToolUseError,
    judge_label_match,
)
from ownevo_kernel.clustering.label_eval.judgment import ClusterLabelJudgment

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


def _tool_use_block(name: str, payload: Any) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload, id="tu_1")


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _good_payload(cluster_id: str = "ca-snack-weekend-under") -> dict:
    return {
        "schema_version": "0.1",
        "cluster_id": cluster_id,
        "verdict": "agree",
        "rationale": "Candidate names same direction + weekend pattern.",
    }


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
    for f in ("cluster_id", "verdict", "rationale"):
        assert f in required


def test_system_prompt_pins_load_bearing_rules():
    p = SYSTEM_PROMPT
    # Verdict criteria
    assert "agree" in p
    assert "disagree" in p
    # Reading hints
    assert "under-forecast" in p
    assert "over-forecast" in p
    assert "zero-inflated" in p
    assert "flat-prediction" in p
    assert "high-variance" in p
    # Echo cluster_id rule
    assert "cluster_id" in p
    # Calibration rule
    assert "calibrated" in p.lower() or "calibration" in p.lower()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_label_match_returns_judgment():
    case = LABELED_CLUSTER_CASES[0]
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, _good_payload(case.cluster_id))],
        )
    )
    judgment = await judge_label_match(client, case, "weekend snack under-forecasts")
    assert isinstance(judgment, ClusterLabelJudgment)
    assert judgment.cluster_id == case.cluster_id
    assert judgment.verdict == "agree"


@pytest.mark.asyncio
async def test_judge_label_match_passes_model_and_max_tokens():
    case = LABELED_CLUSTER_CASES[1]
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, _good_payload(case.cluster_id))],
        )
    )
    await judge_label_match(
        client, case, "TX over-forecast",
        model="claude-opus-4-7",
        max_tokens=2048,
    )
    kwargs = client.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["max_tokens"] == 2048
    assert kwargs["tool_choice"]["name"] == TOOL_NAME


@pytest.mark.asyncio
async def test_judge_label_match_user_message_includes_all_inputs():
    """The user message must carry cluster_id, domain_context, every
    member signature, the ground-truth label, and the candidate label.
    Without these the judge cannot reach a grounded verdict."""
    case = LABELED_CLUSTER_CASES[2]
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, _good_payload(case.cluster_id))],
        )
    )
    candidate = "rare-event hobby zero-inflated cluster"
    await judge_label_match(client, case, candidate)
    user_msg = client.messages.last_kwargs["messages"][0]["content"]
    assert case.cluster_id in user_msg
    assert case.domain_context in user_msg
    for sig in case.member_signatures:
        assert sig in user_msg
    assert repr(case.ground_truth_label) in user_msg or case.ground_truth_label in user_msg
    assert repr(candidate) in user_msg or candidate in user_msg


# ---------------------------------------------------------------------------
# Wrapped-payload paths (matches A3.x / A4.x convention)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrapped_judgment_key_unwrapped():
    case = LABELED_CLUSTER_CASES[0]
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[
                _tool_use_block(TOOL_NAME, {"judgment": _good_payload(case.cluster_id)}),
            ],
        )
    )
    judgment = await judge_label_match(client, case, "weekend under-forecasts")
    assert judgment.cluster_id == case.cluster_id


@pytest.mark.asyncio
async def test_string_wrapped_payload_recovered():
    """Defensive parsing: occasionally the model returns the wrapped
    value as a JSON-encoded string. The judge must json.loads + validate."""
    case = LABELED_CLUSTER_CASES[0]
    payload_str = json.dumps(_good_payload(case.cluster_id))
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[
                _tool_use_block(TOOL_NAME, {"judgment": payload_str}),
            ],
        )
    )
    judgment = await judge_label_match(client, case, "weekend under-forecasts")
    assert judgment.cluster_id == case.cluster_id


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_tool_use_raises():
    case = LABELED_CLUSTER_CASES[0]
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[_text_block("I do not feel like calling the tool today.")],
            stop_reason="end_turn",
        )
    )
    with pytest.raises(NoClusterLabelToolUseError) as exc:
        await judge_label_match(client, case, "anything")
    assert exc.value.stop_reason == "end_turn"
    assert "do not feel like" in exc.value.content_preview


@pytest.mark.asyncio
async def test_validation_error_on_missing_field():
    case = LABELED_CLUSTER_CASES[0]
    bad = _good_payload(case.cluster_id)
    bad.pop("verdict")
    client = _FakeClient(
        response=_ScriptedResponse(content=[_tool_use_block(TOOL_NAME, bad)])
    )
    with pytest.raises(ClusterLabelJudgmentValidationError) as exc:
        await judge_label_match(client, case, "x")
    assert exc.value.raw_input is bad


@pytest.mark.asyncio
async def test_validation_error_on_extra_field():
    case = LABELED_CLUSTER_CASES[0]
    bad = _good_payload(case.cluster_id)
    bad["mystery_field"] = "noise"
    client = _FakeClient(
        response=_ScriptedResponse(content=[_tool_use_block(TOOL_NAME, bad)])
    )
    with pytest.raises(ClusterLabelJudgmentValidationError):
        await judge_label_match(client, case, "x")


@pytest.mark.asyncio
async def test_cluster_id_mismatch_raises_typed_error():
    case = LABELED_CLUSTER_CASES[0]
    other_id_payload = _good_payload(cluster_id="tx-cleaning-over")  # different fixture
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[_tool_use_block(TOOL_NAME, other_id_payload)]
        )
    )
    with pytest.raises(ClusterLabelIdMismatchError) as exc:
        await judge_label_match(client, case, "x")
    assert exc.value.expected_id == case.cluster_id
    assert exc.value.judgment.cluster_id == "tx-cleaning-over"
