"""Traceloop / OpenLLMetry → GenAI semconv translator for watsonx ADK traces.

One pure helper keyed to the OTel receiver's input shape (one
OTLP-JSON `ResourceSpans` envelope). See this module's `__init__`
docstring for the full divergence list this bridges.

The translator only fills in the standard `gen_ai.*` keys when they
are absent on the span. Any pre-existing standard key wins over the
Traceloop equivalent, so an OpenLLMetry release that ships native
semconv becomes a no-op rather than a double-write. The input dict is
not mutated; the returned dict is a deep copy with the rewrites
applied.

The receiver mapper ignores attribute keys it does not recognise, so
the original Traceloop keys are left in place after rewrite — keeping
the payload faithful to what watsonx emitted is cheaper than walking
the attribute list a second time to strip them.
"""

from __future__ import annotations

import copy
from typing import Any

# Traceloop / OpenLLMetry vendor attribute keys emitted on every tool
# or orchestration span.
_TL_SPAN_KIND_KEY = "traceloop.span.kind"
_TL_ENTITY_NAME_KEY = "traceloop.entity.name"
_TL_ENTITY_INPUT_KEY = "traceloop.entity.input"
_TL_ENTITY_OUTPUT_KEY = "traceloop.entity.output"

# Standard GenAI semconv attribute keys the OTel receiver mapper reads.
_SEMCONV_OP_NAME_KEY = "gen_ai.operation.name"
_SEMCONV_TOOL_NAME_KEY = "gen_ai.tool.name"
_SEMCONV_TOOL_CALL_ID_KEY = "gen_ai.tool.call.id"
_SEMCONV_TOOL_ARGS_KEY = "gen_ai.tool.call.arguments"
_SEMCONV_TOOL_RESULT_KEY = "gen_ai.tool.call.result"

# Map the Traceloop span-kind enum onto the receiver's operation-name
# vocabulary. workflow / agent / task are all silently consumed by the
# receiver as orchestration anchors; tool is the only kind that
# produces AgentEvents.
_TL_KIND_TO_OP_NAME = {
    "tool": "execute_tool",
    "workflow": "invoke_workflow",
    "agent": "invoke_agent",
    "task": "invoke_workflow",
}


def translate_otlp_json_for_watsonx(payload: dict[str, Any]) -> dict[str, Any]:
    """Rewrite Traceloop / OpenLLMetry vendor keys onto the GenAI semconv keys.

    Walks every span in the OTLP-JSON envelope. For each span carrying
    `traceloop.span.kind` (or `traceloop.entity.*` payload keys),
    appends the equivalent `gen_ai.*` attributes if and only if the
    standard ones are not already present. Both snake_case
    (`resource_spans`) and camelCase (`resourceSpans`) envelope shapes
    are accepted on input; the output uses whichever form the input
    used.

    Args:
        payload: parsed OTLP-JSON dict (one `ResourceSpans` batch).

    Returns:
        A new dict with the rewrites applied. The input is not mutated.
        Non-OpenLLMetry payloads pass through unchanged at the cost of
        one deep-copy + one attribute-list walk per span.
    """
    if not isinstance(payload, dict):
        return payload

    out = copy.deepcopy(payload)

    resource_spans = out.get("resourceSpans") or out.get("resource_spans")
    if not isinstance(resource_spans, list):
        return out

    for rs in resource_spans:
        if not isinstance(rs, dict):
            continue
        scope_spans = rs.get("scopeSpans") or rs.get("scope_spans") or []
        if not isinstance(scope_spans, list):
            continue
        for ss in scope_spans:
            if not isinstance(ss, dict):
                continue
            spans = ss.get("spans") or []
            if not isinstance(spans, list):
                continue
            for span in spans:
                if isinstance(span, dict):
                    _rewrite_span_attributes(span)

    return out


def _rewrite_span_attributes(span: dict[str, Any]) -> None:
    """In-place rewrite on a single span dict (already deep-copied)."""
    attrs = span.get("attributes")
    if not isinstance(attrs, list):
        return

    present_keys: set[str] = set()
    tl_values: dict[str, Any] = {}
    tl_kind: str | None = None
    for kv in attrs:
        if not isinstance(kv, dict):
            continue
        key = kv.get("key")
        if not isinstance(key, str):
            continue
        present_keys.add(key)
        if key == _TL_SPAN_KIND_KEY:
            raw_value = kv.get("value")
            if isinstance(raw_value, dict):
                tl_kind = raw_value.get("stringValue")
        elif key in (
            _TL_ENTITY_NAME_KEY,
            _TL_ENTITY_INPUT_KEY,
            _TL_ENTITY_OUTPUT_KEY,
        ):
            tl_values[key] = kv.get("value")

    # Without a Traceloop span-kind we have nothing to bridge; pure
    # gen_ai.* spans (LLM calls, citation spans) take this path
    # untouched.
    if tl_kind is None:
        return

    # Synthesise gen_ai.operation.name from the Traceloop kind when the
    # standard key is absent. The receiver silently consumes
    # invoke_agent / invoke_workflow spans, so the workflow / agent /
    # task kinds produce no AgentEvents — they just anchor the trace.
    op_name = _TL_KIND_TO_OP_NAME.get(tl_kind)
    if op_name is not None and _SEMCONV_OP_NAME_KEY not in present_keys:
        attrs.append(
            {"key": _SEMCONV_OP_NAME_KEY, "value": {"stringValue": op_name}},
        )
        present_keys.add(_SEMCONV_OP_NAME_KEY)

    # Tool-only rewrites: name, args, result, and a synthetic call_id.
    # Workflow / agent / task spans don't need the entity payloads
    # since the receiver drops them on the operation-name match.
    if tl_kind != "tool":
        return

    if (
        _TL_ENTITY_NAME_KEY in tl_values
        and _SEMCONV_TOOL_NAME_KEY not in present_keys
    ):
        attrs.append(
            {"key": _SEMCONV_TOOL_NAME_KEY, "value": tl_values[_TL_ENTITY_NAME_KEY]},
        )
        present_keys.add(_SEMCONV_TOOL_NAME_KEY)

    if (
        _TL_ENTITY_INPUT_KEY in tl_values
        and _SEMCONV_TOOL_ARGS_KEY not in present_keys
    ):
        attrs.append(
            {"key": _SEMCONV_TOOL_ARGS_KEY, "value": tl_values[_TL_ENTITY_INPUT_KEY]},
        )
        present_keys.add(_SEMCONV_TOOL_ARGS_KEY)

    if (
        _TL_ENTITY_OUTPUT_KEY in tl_values
        and _SEMCONV_TOOL_RESULT_KEY not in present_keys
    ):
        attrs.append(
            {"key": _SEMCONV_TOOL_RESULT_KEY, "value": tl_values[_TL_ENTITY_OUTPUT_KEY]},
        )
        present_keys.add(_SEMCONV_TOOL_RESULT_KEY)

    if _SEMCONV_TOOL_CALL_ID_KEY not in present_keys:
        synthetic_call_id = _synthesise_call_id(span)
        if synthetic_call_id is not None:
            attrs.append(
                {
                    "key": _SEMCONV_TOOL_CALL_ID_KEY,
                    "value": {"stringValue": synthetic_call_id},
                },
            )


def _synthesise_call_id(span: dict[str, Any]) -> str | None:
    """Derive a stable synthetic tool-call id from the span's own spanId.

    OpenLLMetry does not emit `gen_ai.tool.call.id` because the
    Traceloop SDK identifies tool invocations by span position rather
    than an explicit correlation id. The receiver's
    ToolCallStart / ToolCallResult pair shares `call_id` to match the
    two events; using the span's own 16-hex `spanId` gives us a stable
    deterministic id that round-trips across replays of the same trace
    without colliding with other tool calls in the same trace.
    """
    span_id = span.get("spanId") or span.get("span_id")
    if isinstance(span_id, str) and span_id:
        return f"watsonx_{span_id}"
    return None
