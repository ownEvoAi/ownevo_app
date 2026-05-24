"""Helpers for hand-crafting OTLP-JSON fixtures.

The fixtures themselves live as `.json` files under `fixtures/` so a
reviewer can read them without running Python. These helpers exist
because OTLP-JSON's `AnyValue` envelope is verbose enough that
hand-typing every fixture would obscure the actual shape under test.

Each helper returns the OTLP-JSON dict for one piece (attribute,
span, resourceSpans), composable into a full payload.
"""

from __future__ import annotations

from typing import Any


def str_attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def int_attr(key: str, value: int) -> dict[str, Any]:
    # OTLP-JSON int64 → string per spec; the mapper accepts both.
    return {"key": key, "value": {"intValue": str(value)}}


def bool_attr(key: str, value: bool) -> dict[str, Any]:
    return {"key": key, "value": {"boolValue": value}}


def array_attr(key: str, values: list[Any]) -> dict[str, Any]:
    return {"key": key, "value": {"arrayValue": {"values": values}}}


def kvlist_value(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kvlistValue": {"values": pairs}}


def make_span(
    *,
    span_id: str,
    trace_id: str,
    parent_span_id: str = "",
    name: str = "gen_ai.chat",
    start_ns: int = 1_700_000_000_000_000_000,
    end_ns: int = 1_700_000_000_500_000_000,
    attributes: list[dict[str, Any]] | None = None,
    status_code: int = 1,  # STATUS_CODE_OK numeric
    status_message: str = "",
    kind: int = 3,  # SPAN_KIND_CLIENT
) -> dict[str, Any]:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attributes or [],
        "status": {"code": status_code, "message": status_message},
    }


def wrap_batch(spans: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "scope": {"name": "test-fixture"},
                        "spans": spans,
                    },
                ],
            },
        ],
    }


# Common, well-formed assistant-output messages structure used by the
# `gen_ai.output.messages` attribute. Each message is itself a kvlist
# (since OTLP attribute values cannot directly carry deep arrays of
# objects without wrapping in the kvlistValue envelope).
def assistant_text_messages(text: str) -> dict[str, Any]:
    return {
        "arrayValue": {
            "values": [
                kvlist_value(
                    [
                        {"key": "role", "value": {"stringValue": "assistant"}},
                        {
                            "key": "parts",
                            "value": {
                                "arrayValue": {
                                    "values": [
                                        kvlist_value(
                                            [
                                                {
                                                    "key": "type",
                                                    "value": {"stringValue": "text"},
                                                },
                                                {
                                                    "key": "content",
                                                    "value": {"stringValue": text},
                                                },
                                            ],
                                        ),
                                    ],
                                },
                            },
                        },
                    ],
                ),
            ],
        },
    }


def assistant_text_and_reasoning_messages(text: str, reasoning: str) -> dict[str, Any]:
    return {
        "arrayValue": {
            "values": [
                kvlist_value(
                    [
                        {"key": "role", "value": {"stringValue": "assistant"}},
                        {
                            "key": "parts",
                            "value": {
                                "arrayValue": {
                                    "values": [
                                        kvlist_value(
                                            [
                                                {
                                                    "key": "type",
                                                    "value": {"stringValue": "thinking"},
                                                },
                                                {
                                                    "key": "content",
                                                    "value": {"stringValue": reasoning},
                                                },
                                            ],
                                        ),
                                        kvlist_value(
                                            [
                                                {
                                                    "key": "type",
                                                    "value": {"stringValue": "text"},
                                                },
                                                {
                                                    "key": "content",
                                                    "value": {"stringValue": text},
                                                },
                                            ],
                                        ),
                                    ],
                                },
                            },
                        },
                    ],
                ),
            ],
        },
    }
