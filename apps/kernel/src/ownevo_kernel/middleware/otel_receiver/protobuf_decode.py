"""Decode OTLP-HTTP protobuf bodies into the JSON-path dict shape.

The receiver's mapper consumes the OTLP-JSON object model
(`resourceSpans` → `scopeSpans` → `spans`, IDs as hex strings,
attributes as `[{key, value: {stringValue: ...}}]`). LangSmith's
`langsmith-collector-proxy` emits exactly that. But OpenLLMetry /
traceloop and most stock OTel SDKs default to OTLP-HTTP **protobuf**
(`Content-Type: application/x-protobuf`), so a customer pointing such
an exporter straight at this receiver would otherwise get a 400.

Rather than write a second mapper, we decode the protobuf into the
same dict the JSON path produces and hand it to the existing mapper
unchanged. `google.protobuf.json_format.MessageToDict` gets us 95% of
the way — it already emits camelCase keys (`resourceSpans`,
`startTimeUnixNano`, `stringValue`, …) and string-encodes int64s the
way OTLP-JSON does. The one mismatch it leaves is the ID fields:
protobuf models `trace_id` / `span_id` / `parent_span_id` as raw
bytes, and `MessageToDict` base64-encodes bytes — but OTLP-JSON (and
therefore our mapper) expects **hex**. We rewrite those three fields
to hex after the conversion.
"""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING, Any

from .mapper import OtelDecodeError, OversizedPayloadError, decode_otlp_payload

if TYPE_CHECKING:
    from .mapper import DecodedBatch

# The three span fields protobuf models as bytes; MessageToDict
# base64-encodes them, but the mapper expects hex per the OTLP-JSON spec.
_ID_FIELDS = ("traceId", "spanId", "parentSpanId")


def _b64_to_hex(value: str) -> str:
    """Re-encode a base64 id (MessageToDict output) as lowercase hex.

    Returns the input unchanged if it isn't valid base64 — defensive
    against an exporter that already sent hex, though MessageToDict
    never does.
    """
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return value
    return raw.hex()


def _normalise_ids(payload: dict[str, Any]) -> None:
    """Rewrite base64 id fields to hex, in place, across every span."""
    for rs in payload.get("resourceSpans", []) or []:
        if not isinstance(rs, dict):
            continue
        for ss in rs.get("scopeSpans", []) or []:
            if not isinstance(ss, dict):
                continue
            for span in ss.get("spans", []) or []:
                if not isinstance(span, dict):
                    continue
                for field in _ID_FIELDS:
                    raw = span.get(field)
                    if isinstance(raw, str) and raw:
                        span[field] = _b64_to_hex(raw)


def decode_protobuf_to_otlp_dict(
    raw: bytes,
    *,
    max_body_bytes: int,
) -> dict[str, Any]:
    """Parse an OTLP-protobuf ExportTraceServiceRequest into the JSON dict.

    Raises `OversizedPayloadError` over the cap, `OtelDecodeError` on a
    body that isn't a parseable ExportTraceServiceRequest. The returned
    dict is shaped exactly like an OTLP-JSON payload so it can be passed
    straight to `decode_otlp_payload`.
    """
    if len(raw) > max_body_bytes:
        raise OversizedPayloadError(
            f"payload {len(raw)} bytes exceeds cap {max_body_bytes}",
        )

    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise OtelDecodeError(
            "protobuf OTLP ingest requires the `opentelemetry-proto` package",
        ) from exc

    message = ExportTraceServiceRequest()
    try:
        message.ParseFromString(raw)
    except Exception as exc:  # protobuf raises DecodeError (a ValueError subclass)
        raise OtelDecodeError(f"OTLP payload is not valid protobuf: {exc}") from exc

    payload = MessageToDict(message)
    _normalise_ids(payload)
    return payload


def decode_otlp_protobuf(
    raw: bytes,
    *,
    max_body_bytes: int,
) -> DecodedBatch:
    """Decode an OTLP-protobuf body into a `DecodedBatch`.

    Combines the protobuf → OTLP-JSON-dict conversion with the existing
    JSON-path mapper so the route can offload the whole CPU-bound step
    in one `asyncio.to_thread` call. The mapper sees a dict and skips
    its own size check, so the cap is enforced here on the raw bytes.
    """
    payload = decode_protobuf_to_otlp_dict(raw, max_body_bytes=max_body_bytes)
    return decode_otlp_payload(payload, max_body_bytes=max_body_bytes)
