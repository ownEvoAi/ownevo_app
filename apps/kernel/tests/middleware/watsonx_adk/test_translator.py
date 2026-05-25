"""Tests for `translate_otlp_json_for_watsonx` — Traceloop → semconv shim.

watsonx Orchestrate ADK emits OpenTelemetry traces through the
Traceloop / OpenLLMetry SDK. These tests exercise the translator
against hand-crafted OTLP-JSON payloads shaped to match what
Traceloop's auto-instrumentation produces on tool, workflow, and
agent spans. No live watsonx environment is required.

What's pinned:

  1. `traceloop.span.kind = "tool"` synthesises
     `gen_ai.operation.name = "execute_tool"`.
  2. `traceloop.entity.name` / `.input` / `.output` rewrite onto the
     standard `gen_ai.tool.name` / `.call.arguments` / `.call.result`.
  3. A synthetic `gen_ai.tool.call.id` is derived from the span's own
     `spanId` so the receiver's ToolCallStart / ToolCallResult pair
     shares a deterministic correlation id.
  4. `traceloop.span.kind = "workflow" | "agent" | "task"` synthesises
     the appropriate `invoke_*` operation name; those spans are then
     silently consumed by the receiver mapper.
  5. The rewrite is conditional — pre-existing standard semconv keys
     are not overwritten.
  6. Non-Traceloop spans (pure `gen_ai.*` LLM spans) pass through
     untouched.
  7. The chain `watsonx payload → translate → decode_otlp_payload`
     produces the same AgentEvent stream a Claude-SDK-emitted trace
     would.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from ownevo_kernel.middleware.otel_receiver import decode_otlp_payload
from ownevo_kernel.middleware.watsonx_adk import translate_otlp_json_for_watsonx


def _watsonx_tool_span(
    *,
    span_id: str = "f" * 16,
    args_json: str = '{"a":17,"b":25}',
    result_json: str = "42",
    include_op_name: bool = False,
    include_tool_name: bool = False,
    include_call_id: bool = False,
) -> dict[str, Any]:
    """Build an OTLP-JSON payload mimicking a Traceloop tool span.

    Defaults model the common case: only Traceloop vendor keys
    present, no standard `gen_ai.*` tool attributes. Flags let
    individual tests inject the standard keys to verify the
    conditional-rewrite behaviour.
    """
    attrs: list[dict[str, Any]] = [
        {"key": "traceloop.span.kind", "value": {"stringValue": "tool"}},
        {"key": "traceloop.entity.name", "value": {"stringValue": "add"}},
        {"key": "traceloop.entity.input", "value": {"stringValue": args_json}},
        {"key": "traceloop.entity.output", "value": {"stringValue": result_json}},
    ]
    if include_op_name:
        attrs.append(
            {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
        )
    if include_tool_name:
        attrs.append(
            {"key": "gen_ai.tool.name", "value": {"stringValue": "preexisting_name"}},
        )
    if include_call_id:
        attrs.append(
            {"key": "gen_ai.tool.call.id", "value": {"stringValue": "preexisting_id"}},
        )

    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "traceloop.workflow"},
                        "spans": [
                            {
                                "traceId": "1" * 32,
                                "spanId": span_id,
                                "parentSpanId": "3" * 16,
                                "name": "add.tool",
                                "kind": 1,
                                "startTimeUnixNano": "1700000001000000000",
                                "endTimeUnixNano": "1700000001420000000",
                                "attributes": attrs,
                                "status": {"code": 1, "message": ""},
                            },
                        ],
                    },
                ],
            },
        ],
    }


def _watsonx_workflow_span(kind: str = "workflow") -> dict[str, Any]:
    """A Traceloop orchestration span (workflow / agent / task)."""
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "traceloop.workflow"},
                        "spans": [
                            {
                                "traceId": "1" * 32,
                                "spanId": "a" * 16,
                                "parentSpanId": "",
                                "name": f"{kind}_root",
                                "kind": 1,
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000002000000000",
                                "attributes": [
                                    {
                                        "key": "traceloop.span.kind",
                                        "value": {"stringValue": kind},
                                    },
                                    {
                                        "key": "traceloop.workflow.name",
                                        "value": {"stringValue": "add_workflow"},
                                    },
                                    {
                                        "key": "traceloop.entity.name",
                                        "value": {"stringValue": "add_workflow"},
                                    },
                                ],
                                "status": {"code": 1, "message": ""},
                            },
                        ],
                    },
                ],
            },
        ],
    }


def test_translator_rewrites_tool_kind_into_execute_tool_op_name() -> None:
    payload = _watsonx_tool_span()
    rewritten = translate_otlp_json_for_watsonx(payload)

    attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    op_values = [
        kv["value"]["stringValue"]
        for kv in attrs
        if kv["key"] == "gen_ai.operation.name"
    ]
    assert op_values == ["execute_tool"]


def test_translator_rewrites_entity_name_input_output() -> None:
    payload = _watsonx_tool_span()
    rewritten = translate_otlp_json_for_watsonx(payload)

    attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    keys = {kv["key"] for kv in attrs}
    assert "gen_ai.tool.name" in keys
    assert "gen_ai.tool.call.arguments" in keys
    assert "gen_ai.tool.call.result" in keys
    # Traceloop keys remain — translator is additive, not destructive.
    assert "traceloop.entity.name" in keys
    assert "traceloop.entity.input" in keys
    assert "traceloop.entity.output" in keys


def test_translator_synthesises_call_id_from_span_id() -> None:
    payload = _watsonx_tool_span(span_id="abcdef0123456789")
    rewritten = translate_otlp_json_for_watsonx(payload)

    attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    call_id_values = [
        kv["value"]["stringValue"]
        for kv in attrs
        if kv["key"] == "gen_ai.tool.call.id"
    ]
    assert call_id_values == ["watsonx_abcdef0123456789"]


def test_translator_preserves_standard_keys_when_present() -> None:
    """A future OpenLLMetry release that ships native semconv keys
    should not get double-written by the translator."""
    payload = _watsonx_tool_span(
        include_op_name=True,
        include_tool_name=True,
        include_call_id=True,
    )
    rewritten = translate_otlp_json_for_watsonx(payload)

    attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    tool_name_values = [
        kv["value"]["stringValue"]
        for kv in attrs
        if kv["key"] == "gen_ai.tool.name"
    ]
    call_id_values = [
        kv["value"]["stringValue"]
        for kv in attrs
        if kv["key"] == "gen_ai.tool.call.id"
    ]
    op_values = [
        kv["value"]["stringValue"]
        for kv in attrs
        if kv["key"] == "gen_ai.operation.name"
    ]
    assert tool_name_values == ["preexisting_name"]
    assert call_id_values == ["preexisting_id"]
    assert op_values == ["execute_tool"]


def test_translator_does_not_mutate_input() -> None:
    payload = _watsonx_tool_span()
    snapshot = copy.deepcopy(payload)
    translate_otlp_json_for_watsonx(payload)
    assert payload == snapshot, "translator must not mutate its input"


@pytest.mark.parametrize(
    "kind,expected_op",
    [
        ("workflow", "invoke_workflow"),
        ("agent", "invoke_agent"),
        ("task", "invoke_workflow"),
    ],
)
def test_translator_maps_orchestration_kinds_to_invoke_ops(
    kind: str, expected_op: str,
) -> None:
    payload = _watsonx_workflow_span(kind=kind)
    rewritten = translate_otlp_json_for_watsonx(payload)

    attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    op_values = [
        kv["value"]["stringValue"]
        for kv in attrs
        if kv["key"] == "gen_ai.operation.name"
    ]
    assert op_values == [expected_op]


def test_translator_passes_through_pure_gen_ai_spans_untouched() -> None:
    """A pure `gen_ai.*` LLM-call span carries no Traceloop attributes
    and must round-trip unchanged."""
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "openllmetry.openai"},
                        "spans": [
                            {
                                "traceId": "1" * 32,
                                "spanId": "b" * 16,
                                "parentSpanId": "a" * 16,
                                "name": "openai.chat",
                                "kind": 3,
                                "startTimeUnixNano": "1700000001500000000",
                                "endTimeUnixNano": "1700000002000000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.operation.name",
                                        "value": {"stringValue": "chat"},
                                    },
                                    {
                                        "key": "gen_ai.response.model",
                                        "value": {"stringValue": "gpt-4o"},
                                    },
                                ],
                                "status": {"code": 1, "message": ""},
                            },
                        ],
                    },
                ],
            },
        ],
    }
    snapshot = copy.deepcopy(payload)
    rewritten = translate_otlp_json_for_watsonx(payload)
    assert rewritten == snapshot, "non-Traceloop spans should pass through unchanged"


def test_snake_case_envelope_accepted() -> None:
    payload = _watsonx_tool_span()
    snake = {"resource_spans": payload["resourceSpans"]}
    for rs in snake["resource_spans"]:
        rs["scope_spans"] = rs.pop("scopeSpans")

    rewritten = translate_otlp_json_for_watsonx(snake)
    attrs = rewritten["resource_spans"][0]["scope_spans"][0]["spans"][0]["attributes"]
    keys = {kv["key"] for kv in attrs}
    assert "gen_ai.operation.name" in keys
    assert "gen_ai.tool.call.arguments" in keys


def test_non_dict_payload_returned_unchanged() -> None:
    assert translate_otlp_json_for_watsonx("not a dict") == "not a dict"  # type: ignore[arg-type]
    assert translate_otlp_json_for_watsonx([1, 2, 3]) == [1, 2, 3]  # type: ignore[arg-type]


def test_empty_resource_spans_returned_unchanged() -> None:
    payload: dict[str, Any] = {"resourceSpans": []}
    result = translate_otlp_json_for_watsonx(payload)
    assert result == {"resourceSpans": []}


def test_chain_watsonx_payload_to_receiver_produces_tool_events() -> None:
    """End-to-end pin: a Traceloop tool span → translate → decode
    produces the same ToolCallStart + ToolCallResult pair an
    OpenLLMetry-instrumented agent trace would."""
    payload = _watsonx_tool_span(
        span_id="cafebabecafebabe",
        args_json=json.dumps({"a": 17, "b": 25}),
        result_json="42",
    )
    rewritten = translate_otlp_json_for_watsonx(payload)
    decoded = decode_otlp_payload(rewritten)

    starts = [e for e in decoded.events if e.type == "tool_call_start"]
    results = [e for e in decoded.events if e.type == "tool_call_result"]
    assert len(starts) == 1
    assert len(results) == 1
    assert decoded.warnings == []

    start = starts[0]
    result = results[0]
    assert start.name == "add"
    assert start.call_id == "watsonx_cafebabecafebabe"
    assert start.args == {"a": 17, "b": 25}
    assert result.name == "add"
    assert result.call_id == "watsonx_cafebabecafebabe"
    assert result.status == "ok"
    assert result.output == 42


def test_chain_without_translator_drops_tool_payload() -> None:
    """Negative control: same payload through the receiver WITHOUT the
    translator. With no standard gen_ai.* keys, the receiver does not
    recognise the span as an execute_tool and emits no tool events."""
    payload = _watsonx_tool_span()
    decoded = decode_otlp_payload(payload)

    starts = [e for e in decoded.events if e.type == "tool_call_start"]
    results = [e for e in decoded.events if e.type == "tool_call_result"]
    assert starts == []
    assert results == []


def test_chain_workflow_span_produces_no_events() -> None:
    """A Traceloop workflow span anchors the trace but emits no
    AgentEvents — same behaviour as the receiver's silent-drop branch
    for `invoke_workflow` / `invoke_agent`."""
    payload = _watsonx_workflow_span(kind="workflow")
    rewritten = translate_otlp_json_for_watsonx(payload)
    decoded = decode_otlp_payload(rewritten)

    assert decoded.events == []
    assert decoded.warnings == []


def test_translator_unknown_traceloop_kind_passes_through_unchanged() -> None:
    """An unrecognised `traceloop.span.kind` (e.g. a future "retrieval"
    or "embedding" kind not yet in _TL_KIND_TO_OP_NAME) must pass through
    without crashing and without synthesising any `gen_ai.*` attribute.
    This pins the silent-pass-through contract so that adding a new
    Traceloop kind to the map is a deliberate opt-in, not an accident."""
    payload: dict[str, Any] = {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "traceloop.workflow"},
                        "spans": [
                            {
                                "traceId": "1" * 32,
                                "spanId": "c" * 16,
                                "parentSpanId": "a" * 16,
                                "name": "retrieval.span",
                                "kind": 1,
                                "startTimeUnixNano": "1700000001000000000",
                                "endTimeUnixNano": "1700000001500000000",
                                "attributes": [
                                    {
                                        "key": "traceloop.span.kind",
                                        "value": {"stringValue": "retrieval"},
                                    },
                                    {
                                        "key": "traceloop.entity.name",
                                        "value": {"stringValue": "vector_search"},
                                    },
                                ],
                                "status": {"code": 1, "message": ""},
                            },
                        ],
                    },
                ],
            },
        ],
    }
    rewritten = translate_otlp_json_for_watsonx(payload)
    attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    keys = {kv["key"] for kv in attrs}
    # No standard gen_ai.* keys must be synthesised for an unknown kind.
    assert "gen_ai.operation.name" not in keys
    assert "gen_ai.tool.name" not in keys
    assert "gen_ai.tool.call.id" not in keys


def test_translator_absent_span_id_produces_no_call_id() -> None:
    """When a tool span has no `spanId` / `span_id` field, the translator
    must NOT append `gen_ai.tool.call.id` — returning None from
    `_synthesise_call_id` is the expected path and the absence of the
    call-id attribute is the contract this test pins."""
    payload: dict[str, Any] = {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "traceloop.workflow"},
                        "spans": [
                            {
                                "traceId": "1" * 32,
                                # No spanId / span_id field — simulates a
                                # malformed or partially-decoded span.
                                "parentSpanId": "3" * 16,
                                "name": "add.tool",
                                "kind": 1,
                                "startTimeUnixNano": "1700000001000000000",
                                "endTimeUnixNano": "1700000001420000000",
                                "attributes": [
                                    {
                                        "key": "traceloop.span.kind",
                                        "value": {"stringValue": "tool"},
                                    },
                                    {
                                        "key": "traceloop.entity.name",
                                        "value": {"stringValue": "add"},
                                    },
                                    {
                                        "key": "traceloop.entity.input",
                                        "value": {"stringValue": '{"a":1}'},
                                    },
                                    {
                                        "key": "traceloop.entity.output",
                                        "value": {"stringValue": "2"},
                                    },
                                ],
                                "status": {"code": 1, "message": ""},
                            },
                        ],
                    },
                ],
            },
        ],
    }
    rewritten = translate_otlp_json_for_watsonx(payload)
    attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    keys = {kv["key"] for kv in attrs}
    # All other rewrites fire (entity keys are present).
    assert "gen_ai.operation.name" in keys
    assert "gen_ai.tool.name" in keys
    assert "gen_ai.tool.call.arguments" in keys
    # But call_id must be absent — _synthesise_call_id returns None with no spanId.
    assert "gen_ai.tool.call.id" not in keys
