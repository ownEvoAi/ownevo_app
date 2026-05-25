"""Tests for `translate_otlp_json_for_adk` — the ADK→semconv key shim.

These tests don't require `google-adk` to be installed — they exercise
the translator against hand-crafted OTLP-JSON payloads that mimic
exactly what ADK puts on the wire (the vendor-prefixed
`gcp.vertex.agent.tool_call_args` / `gcp.vertex.agent.tool_response`
attributes that the receiver mapper would otherwise miss).

What's pinned:

  1. Vendor key → standard key rewrite happens on `execute_tool` spans.
  2. The rewrite is non-destructive: input dict is not mutated; output
     is a deep copy.
  3. The rewrite is conditional: if the standard key is ALREADY
     present, we don't overwrite — a future ADK release that ships
     standard names directly should be a no-op.
  4. The chain `ADK output → translate → decode_otlp_payload` produces
     the same AgentEvents an OpenLLMetry agent would, demonstrating
     that ADK traces flow through the existing receiver pipeline once
     the shim is applied.
  5. Both camelCase (`resourceSpans`) and snake_case (`resource_spans`)
     envelope shapes are accepted.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from ownevo_kernel.middleware.google_adk import translate_otlp_json_for_adk
from ownevo_kernel.middleware.otel_receiver import decode_otlp_payload


def _adk_tool_span(
    *,
    args_json: str = '{"a":17,"b":25}',
    result_json: str = "42",
    standard_args_present: bool = False,
    standard_result_present: bool = False,
) -> dict[str, Any]:
    """Build an OTLP-JSON payload mimicking ADK's `execute_tool` shape."""
    attrs: list[dict[str, Any]] = [
        {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
        {"key": "gen_ai.tool.call.id", "value": {"stringValue": "call_abc123"}},
        {"key": "gen_ai.tool.name", "value": {"stringValue": "add"}},
        {"key": "gcp.vertex.agent.tool_call_args", "value": {"stringValue": args_json}},
        {"key": "gcp.vertex.agent.tool_response", "value": {"stringValue": result_json}},
    ]
    if standard_args_present:
        attrs.append(
            {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": '{"already":"set"}'}},
        )
    if standard_result_present:
        attrs.append(
            {"key": "gen_ai.tool.call.result", "value": {"stringValue": "already-set"}},
        )

    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "gcp.vertex.agent"},
                        "spans": [
                            {
                                "traceId": "1" * 32,
                                "spanId": "2" * 16,
                                "parentSpanId": "3" * 16,
                                "name": "execute_tool add",
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


def test_translator_rewrites_vendor_args_and_result_keys() -> None:
    payload = _adk_tool_span()
    rewritten = translate_otlp_json_for_adk(payload)

    attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    keys = {kv["key"] for kv in attrs}
    assert "gen_ai.tool.call.arguments" in keys
    assert "gen_ai.tool.call.result" in keys
    # The vendor keys should still be there too — leaving them in place
    # is harmless and keeps the payload faithful to ADK's emission.
    assert "gcp.vertex.agent.tool_call_args" in keys
    assert "gcp.vertex.agent.tool_response" in keys


def test_translator_does_not_mutate_input() -> None:
    payload = _adk_tool_span()
    snapshot = copy.deepcopy(payload)
    translate_otlp_json_for_adk(payload)
    assert payload == snapshot, "translator must not mutate its input"


def test_translator_preserves_standard_keys_when_present() -> None:
    """If a future ADK release ships standard semconv keys directly,
    the translator should not double-write — the existing value wins."""
    payload = _adk_tool_span(
        standard_args_present=True,
        standard_result_present=True,
    )
    rewritten = translate_otlp_json_for_adk(payload)
    attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]

    args_values = [
        kv["value"]["stringValue"] for kv in attrs
        if kv["key"] == "gen_ai.tool.call.arguments"
    ]
    result_values = [
        kv["value"]["stringValue"] for kv in attrs
        if kv["key"] == "gen_ai.tool.call.result"
    ]
    # Each appears exactly once — the original standard-key value, not
    # an additional copy from the vendor rewrite.
    assert args_values == ['{"already":"set"}']
    assert result_values == ["already-set"]


def test_snake_case_envelope_accepted() -> None:
    """Some OTLP emitters use snake_case (`resource_spans`) instead of
    camelCase. Translator must walk both."""
    payload = _adk_tool_span()
    # Rewrite the envelope key, leave the inner span structure alone.
    snake = {"resource_spans": payload["resourceSpans"]}
    for rs in snake["resource_spans"]:
        rs["scope_spans"] = rs.pop("scopeSpans")

    rewritten = translate_otlp_json_for_adk(snake)
    attrs = rewritten["resource_spans"][0]["scope_spans"][0]["spans"][0]["attributes"]
    keys = {kv["key"] for kv in attrs}
    assert "gen_ai.tool.call.arguments" in keys
    assert "gen_ai.tool.call.result" in keys


def test_non_dict_payload_returned_unchanged() -> None:
    """Defensive: passing a list or string shouldn't raise — the
    receiver entry point will reject it with its own clearer error."""
    assert translate_otlp_json_for_adk("not a dict") == "not a dict"  # type: ignore[arg-type]
    assert translate_otlp_json_for_adk([1, 2, 3]) == [1, 2, 3]  # type: ignore[arg-type]


def test_chain_adk_payload_to_receiver_produces_tool_events() -> None:
    """End-to-end pin: ADK-style payload → translate → decode produces
    the same ToolCallStart + ToolCallResult pair an OpenLLMetry trace
    would (per the existing OTel receiver fixtures)."""
    payload = _adk_tool_span(
        args_json=json.dumps({"a": 17, "b": 25}),
        result_json="42",
    )
    rewritten = translate_otlp_json_for_adk(payload)
    decoded = decode_otlp_payload(rewritten)

    # Exactly one ToolCallStart + one ToolCallResult; no warnings.
    starts = [e for e in decoded.events if e.type == "tool_call_start"]
    results = [e for e in decoded.events if e.type == "tool_call_result"]
    assert len(starts) == 1
    assert len(results) == 1
    assert decoded.warnings == []

    start = starts[0]
    result = results[0]
    assert start.name == "add"
    assert start.call_id == "call_abc123"
    assert start.args == {"a": 17, "b": 25}
    assert result.name == "add"
    assert result.call_id == "call_abc123"
    assert result.status == "ok"
    assert result.output == 42


def test_chain_without_translator_drops_tool_payload() -> None:
    """Negative control: same payload through the receiver WITHOUT the
    translator should still produce events (the receiver is permissive)
    but tool args/output should be empty/None because the receiver
    doesn't know about the vendor keys."""
    payload = _adk_tool_span()
    decoded = decode_otlp_payload(payload)

    starts = [e for e in decoded.events if e.type == "tool_call_start"]
    results = [e for e in decoded.events if e.type == "tool_call_result"]
    assert len(starts) == 1
    assert len(results) == 1
    # Without the translator, the standard keys are missing → args
    # defaults to {} and output to None. This is the gap the
    # translator closes.
    assert starts[0].args == {}
    assert results[0].output is None


@pytest.mark.parametrize(
    "present_vendor_key",
    ["args_only", "result_only"],
)
def test_translator_handles_partial_vendor_keys(present_vendor_key: str) -> None:
    """ADK could conceivably emit only one of the two vendor keys (a
    fire-and-forget tool with no return value, for example). The
    translator should still rewrite whichever is present.

    `present_vendor_key` names which ADK key remains after one is removed:
      - "args_only"   → tool_response stripped; only tool_call_args present
      - "result_only" → tool_call_args stripped; only tool_response present
    """
    payload = _adk_tool_span()
    attrs = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    if present_vendor_key == "args_only":
        attrs[:] = [kv for kv in attrs if kv["key"] != "gcp.vertex.agent.tool_response"]
    else:
        attrs[:] = [kv for kv in attrs if kv["key"] != "gcp.vertex.agent.tool_call_args"]

    rewritten = translate_otlp_json_for_adk(payload)
    out_attrs = rewritten["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    keys = {kv["key"] for kv in out_attrs}
    if present_vendor_key == "args_only":
        assert "gen_ai.tool.call.arguments" in keys
        assert "gen_ai.tool.call.result" not in keys
    else:
        assert "gen_ai.tool.call.arguments" not in keys
        assert "gen_ai.tool.call.result" in keys


def test_empty_resource_spans_returned_unchanged() -> None:
    """An explicit empty resourceSpans list is a no-op — the translator
    returns the payload unchanged without raising."""
    payload: dict[str, Any] = {"resourceSpans": []}
    result = translate_otlp_json_for_adk(payload)
    assert result == {"resourceSpans": []}
