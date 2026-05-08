"""Tests for `nl_gen.instruction_proposer.propose_instruction_edit` (W6).

Mirrors A4.6 / W5.2 patterns:
  * Fake AsyncAnthropic pins tool definition shape, system-prompt
    rules, the tool_use → InstructionEdit contract, and every typed
    error path.
  * Schema round-trips + budget validators pinned independently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from ownevo_kernel.nl_gen.fixtures import FIXTURES, METRIC_FIXTURES
from ownevo_kernel.nl_gen.instruction_proposer import (
    DEFAULT_MODEL,
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    TOOL_NAME,
    FailureExample,
    InstructionEdit,
    InstructionEditValidationError,
    NoInstructionEditToolUseError,
    _build_tool_definition,
    _format_failure_examples,
    _format_user_message,
    propose_instruction_edit,
)


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

    async def create(self, **kwargs: Any) -> SimpleNamespace:
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


def _tool_use_block(payload: Any, name: str = TOOL_NAME) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=payload, id="tu_1")


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _good_edit_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "cluster_label": "winter-boot-spike-week-47",
        "rationale": (
            "False-negatives concentrated on holiday weeks; the agent "
            "is missing the seasonal-promo signal."
        ),
        "appended_text": (
            "When the trajectory shows weeks 47-52 and any "
            "`seasonal_promo` flag is set on prior events, lean True "
            "even if the visible state at the target step looks ambiguous."
        ),
        "schema_version": SCHEMA_VERSION,
    }
    base.update(overrides)
    return base


def _failure_examples() -> list[FailureExample]:
    return [
        FailureExample(
            case_id=f"c{i}",
            direction="false-negative",
            provenance_kind="derived",
            is_test_fold=False,
            text_signature=f"winter spike, week {47 + i}",
        )
        for i in range(3)
    ]


# ---------------------------------------------------------------------------
# Schema — frozen + extra-forbid + length budgets
# ---------------------------------------------------------------------------


def test_schema_round_trip_happy_path():
    edit = InstructionEdit.model_validate(_good_edit_payload())
    assert edit.cluster_label == "winter-boot-spike-week-47"
    assert edit.schema_version == "0.1"


def test_schema_extra_field_rejected():
    payload = _good_edit_payload()
    payload["surprise"] = "hi"
    with pytest.raises(Exception, match="forbid|surprise"):
        InstructionEdit.model_validate(payload)


def test_schema_frozen():
    edit = InstructionEdit.model_validate(_good_edit_payload())
    with pytest.raises(Exception):
        edit.cluster_label = "different"  # type: ignore[misc]


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("cluster_label", ""),
        ("cluster_label", "x" * 121),
        ("rationale", ""),
        ("rationale", "x" * 401),
        ("appended_text", ""),
        ("appended_text", "x" * 1_001),
    ],
)
def test_schema_length_budgets(field: str, bad_value: str):
    payload = _good_edit_payload(**{field: bad_value})
    with pytest.raises(Exception):
        InstructionEdit.model_validate(payload)


def test_schema_version_pattern_rejects_non_semver():
    payload = _good_edit_payload(schema_version="not-semver")
    with pytest.raises(Exception):
        InstructionEdit.model_validate(payload)


# ---------------------------------------------------------------------------
# Tool definition shape
# ---------------------------------------------------------------------------


def test_tool_definition_shape():
    td = _build_tool_definition()
    assert td["name"] == TOOL_NAME
    assert td["description"]
    schema = td["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["edit"]
    edit_schema = schema["properties"]["edit"]
    assert edit_schema["type"] == "object"
    required = set(edit_schema["required"])
    for f in ("cluster_label", "rationale", "appended_text"):
        assert f in required


# ---------------------------------------------------------------------------
# System prompt — load-bearing rules pinned
# ---------------------------------------------------------------------------


def test_system_prompt_pins_load_bearing_rules():
    """A regression in any of these rules would silently change the
    proposer's behavior — pin them at the prompt level so a refactor
    surfaces in tests."""
    sp = SYSTEM_PROMPT
    # Names the failure-mode framing
    assert "dominant cluster" in sp.lower() or "dominant failure cluster" in sp.lower()
    # Names second-person directive style
    assert "second person" in sp.lower()
    # Names metric-asymmetry awareness
    assert "recall" in sp.lower()
    assert "precision" in sp.lower()
    # Names build-on-prior-cycle constraint
    assert "build on" in sp.lower() or "layer" in sp.lower()
    # Names budget constraint
    assert "1,000" in sp or "1000" in sp


# ---------------------------------------------------------------------------
# User message assembly
# ---------------------------------------------------------------------------


def test_format_failure_examples_renders_each_case():
    examples = _failure_examples()
    rendered = _format_failure_examples(examples)
    for ex in examples:
        assert ex.case_id in rendered
        assert ex.direction in rendered
        assert ex.text_signature in rendered


def test_format_failure_examples_handles_empty_list():
    """Defense in depth: an empty cluster shouldn't crash the proposer
    (the orchestrator's contract is to skip cycles with no failures, but
    the function itself should be safe)."""
    rendered = _format_failure_examples([])
    assert "no representative failures" in rendered


def test_format_user_message_includes_workflow_metric_and_cluster():
    spec = FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]
    msg = _format_user_message(
        spec=spec,
        metric=metric,
        cluster_label="my-cluster",
        cluster_size=7,
        failure_examples=_failure_examples(),
        current_instruction=None,
    )
    assert spec.id in msg
    assert spec.domain in msg
    assert metric.family in msg
    assert "my-cluster" in msg
    assert "7 failure" in msg
    # Cycle 1 marker
    assert "cycle 1" in msg.lower() or "none" in msg.lower()


def test_format_user_message_includes_current_instruction_when_set():
    spec = FIXTURES["credit-risk"]
    metric = METRIC_FIXTURES["credit-risk"]
    current = "When `application_type=high-risk`, lean False."
    msg = _format_user_message(
        spec=spec,
        metric=metric,
        cluster_label="x",
        cluster_size=1,
        failure_examples=[],
        current_instruction=current,
    )
    assert current in msg


def test_format_user_message_strips_whitespace_only_current():
    spec = FIXTURES["credit-risk"]
    metric = METRIC_FIXTURES["credit-risk"]
    msg = _format_user_message(
        spec=spec,
        metric=metric,
        cluster_label="x",
        cluster_size=1,
        failure_examples=[],
        current_instruction="   \n  ",
    )
    # Same output as None — whitespace-only is treated as cycle-1
    msg_none = _format_user_message(
        spec=spec,
        metric=metric,
        cluster_label="x",
        cluster_size=1,
        failure_examples=[],
        current_instruction=None,
    )
    assert msg == msg_none


# ---------------------------------------------------------------------------
# propose_instruction_edit — happy + error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_returns_validated_edit():
    spec = FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[_tool_use_block({"edit": _good_edit_payload()})],
        )
    )

    edit = await propose_instruction_edit(
        client,  # type: ignore[arg-type]
        spec=spec,
        metric=metric,
        cluster_label="winter-boot-spike-week-47",
        cluster_size=4,
        failure_examples=_failure_examples(),
        current_instruction=None,
    )

    assert isinstance(edit, InstructionEdit)
    assert edit.cluster_label == "winter-boot-spike-week-47"
    # Verifies the tool was forced + system prompt + tool list passed
    kwargs = client.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert kwargs["system"] == SYSTEM_PROMPT
    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0]["name"] == TOOL_NAME


@pytest.mark.asyncio
async def test_propose_unwraps_unwrapped_payload():
    """Defensive parsing: when the model returns the InstructionEdit
    fields at the top level (no `edit` wrapper), the proposer should
    still validate it. Mirrors A4.6's payload-unwrap fallback."""
    spec = FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]
    payload = _good_edit_payload()
    client = _FakeClient(
        response=_ScriptedResponse(content=[_tool_use_block(payload)]),
    )

    edit = await propose_instruction_edit(
        client,  # type: ignore[arg-type]
        spec=spec,
        metric=metric,
        cluster_label="x",
        cluster_size=1,
        failure_examples=[],
    )
    assert edit.cluster_label == payload["cluster_label"]


@pytest.mark.asyncio
async def test_propose_handles_json_string_wrapped_payload():
    """Opus 4.7 quirk seen in A4.6: occasionally returns the wrapped
    value as a JSON-encoded string. The proposer mirrors A4.6's
    json.loads fallback so a re-run isn't needed."""
    spec = FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]
    payload_str = json.dumps(_good_edit_payload())
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[_tool_use_block({"edit": payload_str})],
        )
    )

    edit = await propose_instruction_edit(
        client,  # type: ignore[arg-type]
        spec=spec,
        metric=metric,
        cluster_label="x",
        cluster_size=1,
        failure_examples=[],
    )
    assert edit.cluster_label == "winter-boot-spike-week-47"


@pytest.mark.asyncio
async def test_propose_no_tool_use_raises():
    spec = FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[_text_block("I am refusing to call the tool today.")],
            stop_reason="end_turn",
        ),
    )

    with pytest.raises(NoInstructionEditToolUseError) as ei:
        await propose_instruction_edit(
            client,  # type: ignore[arg-type]
            spec=spec,
            metric=metric,
            cluster_label="x",
            cluster_size=1,
            failure_examples=[],
        )
    assert ei.value.stop_reason == "end_turn"
    assert "refusing" in ei.value.content_preview


@pytest.mark.asyncio
async def test_propose_validation_error_carries_raw_input():
    spec = FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]
    bad = _good_edit_payload(appended_text="x" * 5_000)  # over the budget
    client = _FakeClient(
        response=_ScriptedResponse(content=[_tool_use_block({"edit": bad})]),
    )

    with pytest.raises(InstructionEditValidationError) as ei:
        await propose_instruction_edit(
            client,  # type: ignore[arg-type]
            spec=spec,
            metric=metric,
            cluster_label="x",
            cluster_size=1,
            failure_examples=[],
        )
    assert ei.value.raw_input == {"edit": bad}


@pytest.mark.asyncio
async def test_propose_passes_failure_examples_into_user_message():
    """The orchestrator hands ≤5 failure examples; they should land in
    the user message verbatim so the LLM has concrete vocabulary."""
    spec = FIXTURES["demand-prediction"]
    metric = METRIC_FIXTURES["demand-prediction"]
    examples = _failure_examples()
    client = _FakeClient(
        response=_ScriptedResponse(
            content=[_tool_use_block({"edit": _good_edit_payload()})],
        ),
    )
    await propose_instruction_edit(
        client,  # type: ignore[arg-type]
        spec=spec,
        metric=metric,
        cluster_label="x",
        cluster_size=len(examples),
        failure_examples=examples,
    )

    user_msg = client.messages.last_kwargs["messages"][0]["content"]
    for ex in examples:
        assert ex.case_id in user_msg
        assert ex.text_signature in user_msg
