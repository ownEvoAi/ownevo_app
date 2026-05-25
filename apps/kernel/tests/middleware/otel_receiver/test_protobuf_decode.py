"""Protobuf OTLP ingest — parity with the JSON path.

Strategy: take the hand-crafted OTLP-JSON fixtures, convert each into
the equivalent protobuf `ExportTraceServiceRequest` bytes, decode those
bytes through `decode_otlp_protobuf`, and assert the resulting
AgentEvents are identical to decoding the original JSON through
`decode_otlp_payload`. If the two paths agree on every fixture, the
protobuf converter is faithful.

The fixture→protobuf step converts the hex trace/span ids to base64
(protobuf's JSON mapping for bytes fields), which is the inverse of the
base64→hex rewrite the production decoder applies — so the test also
proves that symmetry.
"""

from __future__ import annotations

import base64
import binascii
import copy
from typing import Any

import pytest
from ownevo_kernel.middleware.otel_receiver import (
    decode_otlp_payload,
    decode_otlp_protobuf,
)
from ownevo_kernel.middleware.otel_receiver.mapper import OtelDecodeError

from ._fixture_cases import CASES

_ID_FIELDS = ("traceId", "spanId", "parentSpanId")


def _hex_to_b64(value: str) -> str:
    return base64.b64encode(binascii.unhexlify(value)).decode("ascii")


def _json_payload_to_protobuf_bytes(payload: dict[str, Any]) -> bytes:
    """Convert an OTLP-JSON payload dict to protobuf wire bytes.

    Mirrors what a real OTLP-HTTP protobuf exporter would put on the
    wire for the same spans.
    """
    from google.protobuf.json_format import ParseDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    converted = copy.deepcopy(payload)
    for rs in converted.get("resourceSpans", []) or []:
        for ss in rs.get("scopeSpans", []) or []:
            for span in ss.get("spans", []) or []:
                for field in _ID_FIELDS:
                    raw = span.get(field)
                    if isinstance(raw, str) and raw:
                        span[field] = _hex_to_b64(raw)

    message = ParseDict(
        converted, ExportTraceServiceRequest(), ignore_unknown_fields=True
    )
    return message.SerializeToString()


def _events_as_dicts(batch) -> list[dict]:  # noqa: ANN001
    return [e.model_dump(mode="json") for e in batch.events]


def _decodes_cleanly(payload: Any) -> bool:
    """True if the JSON path decodes the payload without a batch-level error.

    Negative fixtures (malformed resourceSpans, etc.) are JSON-path
    error cases and don't apply to the protobuf wire format — they can't
    even be expressed as a valid ExportTraceServiceRequest.
    """
    if not (isinstance(payload, dict) and isinstance(payload.get("resourceSpans"), list)):
        return False
    try:
        decode_otlp_payload(payload)
    except OtelDecodeError:
        return False
    return True


# Fixtures whose payloads round-trip cleanly through protobuf.
_PARITY_CASE_NAMES = [c.name for c in CASES if _decodes_cleanly(c.payload)]


@pytest.mark.parametrize("case_name", _PARITY_CASE_NAMES)
def test_protobuf_matches_json_path(case_name: str) -> None:
    case = next(c for c in CASES if c.name == case_name)
    json_batch = decode_otlp_payload(case.payload)

    pb_bytes = _json_payload_to_protobuf_bytes(case.payload)
    pb_batch = decode_otlp_protobuf(pb_bytes, max_body_bytes=8 * 1024 * 1024)

    assert _events_as_dicts(pb_batch) == _events_as_dicts(json_batch), (
        f"protobuf/JSON divergence on fixture {case_name!r}"
    )


def test_malformed_protobuf_raises_decode_error() -> None:
    # Random bytes that aren't a valid ExportTraceServiceRequest.
    with pytest.raises(OtelDecodeError):
        decode_otlp_protobuf(b"\xde\xad\xbe\xef not protobuf", max_body_bytes=1024)


def test_oversized_protobuf_rejected() -> None:
    from ownevo_kernel.middleware.otel_receiver import OversizedPayloadError

    with pytest.raises(OversizedPayloadError):
        decode_otlp_protobuf(b"x" * 100, max_body_bytes=10)


def test_empty_protobuf_rejected_like_json() -> None:
    # An empty ExportTraceServiceRequest decodes to `{}` (no
    # resourceSpans). The JSON path rejects a payload with no
    # resourceSpans, so the protobuf path must behave identically.
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    empty = ExportTraceServiceRequest().SerializeToString()
    with pytest.raises(OtelDecodeError):
        decode_otlp_protobuf(empty, max_body_bytes=1024)
