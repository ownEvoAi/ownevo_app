"""ADK → GenAI semconv translator + ReadableSpan → OTLP-JSON encoder.

Two pure helpers, both keyed to the OTel receiver's input shape (one
OTLP-JSON `ResourceSpans` envelope).

`translate_otlp_json_for_adk` rewrites the two ADK vendor-prefixed
attribute keys onto their standard GenAI Semantic Conventions
equivalents. The rewrite is conditional: it only fills in the standard
key when the standard key is absent, so a future ADK release that
starts emitting the standard names directly is a no-op rather than a
double-write.

`readable_spans_to_otlp_json` is a minimal in-process encoder that
mirrors what the OTLP-HTTP/JSON exporter puts on the wire. The
integration test uses this to stitch
`InMemorySpanExporter` → translator → `otel_receiver.decode_otlp_payload`
without going through an HTTP transport, which avoids the
proto-vs-JSON protocol toggle, the BatchSpanProcessor flush race,
and the need to run a live FastAPI server inside the test.

The encoder is intentionally minimal — it serialises exactly the
fields the receiver mapper reads (`traceId`, `spanId`,
`parentSpanId`, `name`, `kind`, `startTimeUnixNano`,
`endTimeUnixNano`, `attributes`, `status`). It is NOT a full OTLP-JSON
emitter; do not point production tooling at it.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from opentelemetry.sdk.trace import ReadableSpan


# ADK vendor attribute keys (set by `google.adk.telemetry.tracing` on
# every `execute_tool` span). The values are JSON strings.
_ADK_TOOL_ARGS_KEY = "gcp.vertex.agent.tool_call_args"
_ADK_TOOL_RESULT_KEY = "gcp.vertex.agent.tool_response"

# Standard GenAI semconv attribute keys the OTel receiver mapper reads.
_SEMCONV_TOOL_ARGS_KEY = "gen_ai.tool.call.arguments"
_SEMCONV_TOOL_RESULT_KEY = "gen_ai.tool.call.result"


def translate_otlp_json_for_adk(payload: dict[str, Any]) -> dict[str, Any]:
    """Rewrite ADK vendor attribute keys onto the standard semconv keys.

    Walks every span in the OTLP-JSON envelope. For each span that
    carries `gcp.vertex.agent.tool_call_args` or
    `gcp.vertex.agent.tool_response`, appends an equivalent
    `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result` attribute
    if and only if the standard one is not already present. Both
    snake_case (`resource_spans`) and camelCase (`resourceSpans`)
    envelopes are accepted on input; the output uses whichever form
    the input used.

    Args:
        payload: parsed OTLP-JSON dict (one `ResourceSpans` batch).

    Returns:
        A new dict with the rewrites applied. The input is not
        mutated — callers that want to drop the vendor keys to save
        wire bytes can post-process the result; the receiver mapper
        ignores unknown attribute keys so leaving the vendor keys in
        place is harmless.
    """
    if not isinstance(payload, dict):
        return payload

    out = copy.deepcopy(payload)
    _walk_and_rewrite_inplace(out)
    return out


def _walk_and_rewrite_inplace(out: dict[str, Any]) -> None:
    """Walk the OTLP span tree and apply ADK rewrites in-place.

    Internal helper shared with the ingest route, which performs a
    single deep-copy across multiple translation passes instead of one
    per translator. The public ``translate_otlp_json_for_adk`` wrapper
    is the preferred interface for standalone callers.
    """
    resource_spans = out.get("resourceSpans") or out.get("resource_spans")
    if not isinstance(resource_spans, list):
        return

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


def _rewrite_span_attributes(span: dict[str, Any]) -> None:
    """In-place rewrite on a single span dict (already deep-copied)."""
    attrs = span.get("attributes")
    if not isinstance(attrs, list):
        return

    present_keys: set[str] = set()
    adk_values: dict[str, Any] = {}
    for kv in attrs:
        if not isinstance(kv, dict):
            continue
        key = kv.get("key")
        if not isinstance(key, str):
            continue
        present_keys.add(key)
        if key in (_ADK_TOOL_ARGS_KEY, _ADK_TOOL_RESULT_KEY):
            adk_values[key] = kv.get("value")

    rewrites: list[tuple[str, str]] = [
        (_ADK_TOOL_ARGS_KEY, _SEMCONV_TOOL_ARGS_KEY),
        (_ADK_TOOL_RESULT_KEY, _SEMCONV_TOOL_RESULT_KEY),
    ]
    for src_key, dst_key in rewrites:
        if src_key in adk_values and dst_key not in present_keys:
            attrs.append({"key": dst_key, "value": adk_values[src_key]})


def readable_spans_to_otlp_json(spans: Iterable[ReadableSpan]) -> dict[str, Any]:
    """Encode `ReadableSpan` objects into an OTLP-JSON `resourceSpans` dict.

    Spans coming out of `InMemorySpanExporter` carry the full
    OpenTelemetry SDK shape. This helper extracts just the fields the
    OTel receiver mapper consumes and packages them into the camelCase
    OTLP-JSON envelope the mapper expects.

    Status code mapping mirrors the OTLP spec:

      * StatusCode.UNSET → numeric 0
      * StatusCode.OK    → numeric 1
      * StatusCode.ERROR → numeric 2

    Span kind mapping follows the OTLP enum exactly (INTERNAL=1,
    SERVER=2, CLIENT=3, PRODUCER=4, CONSUMER=5).

    Args:
        spans: any iterable of `ReadableSpan` (as returned by
            `InMemorySpanExporter.get_finished_spans()`). Spans are
            grouped under a single resource + scope; the test path
            does not exercise multi-resource batches.

    Returns:
        A dict shaped like one OTLP-JSON `ExportTraceServiceRequest`
        payload, ready to pass through `translate_otlp_json_for_adk`
        and then into `otel_receiver.decode_otlp_payload`.
    """
    from opentelemetry.trace import SpanKind, StatusCode

    # Both maps are constant across all spans; built once here, not per-span.
    status_code_map = {
        StatusCode.UNSET: 0,
        StatusCode.OK: 1,
        StatusCode.ERROR: 2,
    }
    kind_map = {
        SpanKind.INTERNAL: 1,
        SpanKind.SERVER: 2,
        SpanKind.CLIENT: 3,
        SpanKind.PRODUCER: 4,
        SpanKind.CONSUMER: 5,
    }

    span_list: list[dict[str, Any]] = []
    for span in spans:
        ctx = span.get_span_context()
        # OpenTelemetry exposes ids as ints; OTLP-JSON wants lowercase
        # hex strings (16 hex for span_id, 32 hex for trace_id).
        trace_id_hex = f"{ctx.trace_id:032x}"
        span_id_hex = f"{ctx.span_id:016x}"
        parent_hex = ""
        if span.parent is not None:
            parent_hex = f"{span.parent.span_id:016x}"

        span_dict: dict[str, Any] = {
            "traceId": trace_id_hex,
            "spanId": span_id_hex,
            "parentSpanId": parent_hex,
            "name": span.name,
            "kind": kind_map.get(span.kind, 1),
            "startTimeUnixNano": str(span.start_time) if span.start_time else "0",
            "endTimeUnixNano": str(span.end_time) if span.end_time else "0",
            "attributes": _attributes_to_kv_list(dict(span.attributes or {})),
            "status": {
                "code": status_code_map.get(span.status.status_code, 0),
                "message": span.status.description or "",
            },
        }
        span_list.append(span_dict)

    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "ownevo.middleware.google_adk"},
                        "spans": span_list,
                    },
                ],
            },
        ],
    }


def _attributes_to_kv_list(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """Encode a flat attribute dict into the OTLP-JSON `KeyValue[]` shape.

    Only the AnyValue variants the receiver mapper consumes are
    emitted: string, int, double, bool, plus arrayValue for lists of
    primitives, and kvlistValue for nested dicts. The receiver's
    `_unwrap_anyvalue` recurses into kvlistValue entries and
    reconstructs the original Python dict — necessary for attributes
    like `gen_ai.output.messages` that the mapper walks structurally.
    """
    out: list[dict[str, Any]] = []
    for key, value in attrs.items():
        out.append({"key": key, "value": _anyvalue(value)})
    return out


def _anyvalue(value: Any) -> dict[str, Any]:
    """Wrap a Python value in the OTLP-JSON `AnyValue` envelope."""
    if value is None:
        # OTLP has no explicit null AnyValue variant; emit an empty
        # stringValue to keep the key present for downstream warning
        # surfaces without poisoning the schema.
        return {"stringValue": ""}
    if isinstance(value, bool):
        # bool must come before int (bool is an int subclass in Python).
        return {"boolValue": value}
    if isinstance(value, int):
        # OTLP-JSON encodes int64 as a string per spec.
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_anyvalue(v) for v in value]}}
    if isinstance(value, dict):
        # Encode nested dicts as OTLP kvlistValue so the receiver's
        # _unwrap_anyvalue recurses into them and reconstructs the
        # original Python dict — necessary for attributes like
        # `gen_ai.output.messages` that the receiver walks structurally.
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(k), "value": _anyvalue(v)} for k, v in value.items()
                ],
            },
        }
    # Anything else — dataclasses, model instances, ... — JSON-encode
    # to a string. The mapper reads tool args / results back via
    # `json.loads` so this keeps the round-trip intact.
    return {"stringValue": json.dumps(value, default=str)}
