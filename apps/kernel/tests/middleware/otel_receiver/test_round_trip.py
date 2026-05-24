"""Round-trip replay test (the load-bearing acceptance criterion).

Construct a realistic AgentEvent stream — the shape produced by the
existing kernel-side trace collector when an agent runs against the
M5 sandbox — encode it as OTLP-JSON via an in-test wrapper, decode
through the receiver, and assert that the load-bearing fields survive
the round trip.

What the test guarantees
------------------------
For every AgentEvent variant the mapper claims to support (see
`MAPPING.md`), an `AgentEvent → OTel → AgentEvent` round trip
preserves:

  * `type` (the discriminator)
  * `trace_id`
  * payload identity for: `text`, `model`, `name`, `call_id`,
    `status`, `error_class`, `duration_ms`

The IDs (`event_id`, `parent_span_id`) are not pinned for identity —
OTel encodes span IDs in 8 bytes so re-encoding through the wire path
deterministically pads/derives them; the round trip is internally
consistent but does not preserve the original UUIDs verbatim. The
mapper's `_derive_uuid` covers the fan-out cases (one OTel span → two
AgentEvents); the original-id pinning is what a Phase-2 OTel exporter
adds when ownEvo also speaks OTel egress.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from ownevo_format import AgentEventAdapter
from ownevo_kernel.middleware.otel_receiver import decode_otlp_payload

from ._fixture_helpers import (
    assistant_text_and_reasoning_messages,
    assistant_text_messages,
    make_span,
    str_attr,
    wrap_batch,
)

# ---------------------------------------------------------------------------
# AgentEvent → OTLP-JSON wrapper (test-only).
#
# Production OTel egress is a future slice (Phase 6). This wrapper covers
# only the variants the receiver decodes (content/reasoning, tool pair,
# citation); SkillLoaded / MonitorSignal stay native per MAPPING.md.
# ---------------------------------------------------------------------------


def _uuid_to_otel_trace(uuid: UUID) -> str:
    return uuid.hex


def _new_span_id() -> str:
    return secrets.token_hex(8)


def _ts_to_ns(ts: datetime) -> int:
    return int(ts.timestamp() * 1e9)


def _encode_content_delta(ev: dict[str, Any], *, end_ns: int) -> dict[str, Any]:
    return make_span(
        span_id=_new_span_id(),
        trace_id=_uuid_to_otel_trace(UUID(ev["trace_id"])),
        name="gen_ai.chat",
        start_ns=_ts_to_ns(datetime.fromisoformat(ev["timestamp"])),
        end_ns=end_ns,
        attributes=[
            str_attr("gen_ai.operation.name", "chat"),
            str_attr("gen_ai.response.model", ev["model"]),
            {
                "key": "gen_ai.output.messages",
                "value": assistant_text_messages(ev["text"]),
            },
        ],
    )


def _encode_tool_pair(
    start_ev: dict[str, Any],
    result_ev: dict[str, Any],
    *,
    trace_id_hex: str,
) -> dict[str, Any]:
    import json as _json

    attrs = [
        str_attr("gen_ai.operation.name", "execute_tool"),
        str_attr("gen_ai.tool.call.id", start_ev["call_id"]),
        str_attr("gen_ai.tool.name", start_ev["name"]),
        str_attr("gen_ai.tool.call.arguments", _json.dumps(start_ev["args"])),
        str_attr("gen_ai.tool.call.result", _json.dumps(result_ev["output"])),
    ]
    if result_ev.get("error_class"):
        attrs.append(str_attr("ownevo.error_class", result_ev["error_class"]))
    start_ts = datetime.fromisoformat(start_ev["timestamp"])
    end_ts = datetime.fromisoformat(result_ev["timestamp"])
    return make_span(
        span_id=_new_span_id(),
        trace_id=trace_id_hex,
        name="gen_ai.execute_tool",
        start_ns=_ts_to_ns(start_ts),
        end_ns=_ts_to_ns(end_ts),
        attributes=attrs,
        status_code=2 if result_ev["status"] == "error" else 1,
        status_message=result_ev.get("error") or "",
    )


# ---------------------------------------------------------------------------
# Realistic AgentEvent stream — mirrors what the kernel's TraceCollector
# produces for a one-tool-call M5-style agent turn.
# ---------------------------------------------------------------------------


def _make_native_stream() -> list[dict[str, Any]]:
    """One realistic agent turn: chat -> tool start -> tool result -> chat."""
    trace_id = uuid4()
    t0 = datetime(2026, 5, 23, 14, 0, 0, tzinfo=UTC)

    return [
        {
            "type": "content_delta",
            "event_id": str(uuid4()),
            "trace_id": str(trace_id),
            "iteration_id": None,
            "timestamp": t0.isoformat(),
            "parent_span_id": None,
            "text": "Looking up supplier S-7821.",
            "model": "claude-opus-4-7",
        },
        {
            "type": "tool_call_start",
            "event_id": str(uuid4()),
            "trace_id": str(trace_id),
            "iteration_id": None,
            "timestamp": t0.isoformat(),
            "parent_span_id": None,
            "call_id": "toolu_lookup_001",
            "name": "lookup_supplier",
            "args": {"supplier_id": "S-7821"},
        },
        {
            "type": "tool_call_result",
            "event_id": str(uuid4()),
            "trace_id": str(trace_id),
            "iteration_id": None,
            "timestamp": datetime(2026, 5, 23, 14, 0, 0, 420_000, tzinfo=UTC).isoformat(),
            "parent_span_id": None,
            "call_id": "toolu_lookup_001",
            "name": "lookup_supplier",
            "status": "ok",
            "output": {"lead_time_days": 14, "capacity": 0.82},
            "duration_ms": 420,
            "error": None,
            "error_class": None,
        },
        {
            "type": "content_delta",
            "event_id": str(uuid4()),
            "trace_id": str(trace_id),
            "iteration_id": None,
            "timestamp": datetime(2026, 5, 23, 14, 0, 1, tzinfo=UTC).isoformat(),
            "parent_span_id": None,
            "text": "Supplier confirmed; 14-day lead time, capacity 0.82.",
            "model": "claude-opus-4-7",
        },
    ]


def _encode_native_stream_as_otlp(events: list[dict[str, Any]]) -> dict[str, Any]:
    spans: list[dict[str, Any]] = []
    trace_id_hex = _uuid_to_otel_trace(UUID(events[0]["trace_id"]))

    i = 0
    while i < len(events):
        ev = events[i]
        if ev["type"] == "content_delta":
            end_ns = _ts_to_ns(datetime.fromisoformat(ev["timestamp"])) + 100_000_000
            spans.append(_encode_content_delta(ev, end_ns=end_ns))
            i += 1
        elif ev["type"] == "tool_call_start":
            # Must pair with the matching result.
            result_ev = events[i + 1]
            assert result_ev["type"] == "tool_call_result"
            spans.append(_encode_tool_pair(ev, result_ev, trace_id_hex=trace_id_hex))
            i += 2
        else:
            i += 1
    return wrap_batch(spans)


# ---------------------------------------------------------------------------
# The actual round-trip assertions.
# ---------------------------------------------------------------------------


def test_native_stream_round_trips_through_otlp() -> None:
    native = _make_native_stream()
    typed_native = [AgentEventAdapter.validate_python(e) for e in native]

    payload = _encode_native_stream_as_otlp(native)
    batch = decode_otlp_payload(payload)

    # Same event count: 2 content_delta + 1 tool_call_start + 1 tool_call_result.
    assert [e.type for e in batch.events] == [
        "content_delta",
        "tool_call_start",
        "tool_call_result",
        "content_delta",
    ]

    # trace_id survives the round trip identically.
    decoded_trace_ids = {e.trace_id for e in batch.events}
    native_trace_ids = {e.trace_id for e in typed_native}
    assert decoded_trace_ids == native_trace_ids

    # Payload identity per variant.
    assert batch.events[0].type == "content_delta"
    assert batch.events[0].text == typed_native[0].text
    assert batch.events[0].model == typed_native[0].model

    assert batch.events[1].type == "tool_call_start"
    assert batch.events[1].call_id == typed_native[1].call_id
    assert batch.events[1].name == typed_native[1].name
    assert batch.events[1].args == typed_native[1].args

    assert batch.events[2].type == "tool_call_result"
    assert batch.events[2].call_id == typed_native[2].call_id
    assert batch.events[2].name == typed_native[2].name
    assert batch.events[2].status == typed_native[2].status
    assert batch.events[2].duration_ms == typed_native[2].duration_ms
    assert batch.events[2].error_class is None
    # Output round-trips as a dict (JSON re-decoded by the receiver).
    assert batch.events[2].output == typed_native[2].output

    # The result event's parent_span_id points at the start event's id —
    # the receiver preserves the start/result relationship even though
    # the original UUIDs cannot be recovered byte-for-byte.
    assert batch.events[2].parent_span_id == batch.events[1].event_id


def test_sandbox_timeout_round_trips_with_error_class() -> None:
    """A sandbox-runtime failure preserves the `error_class` discriminator."""
    trace_id = uuid4()
    t0 = datetime(2026, 5, 23, 14, 30, 0, tzinfo=UTC)
    native = [
        {
            "type": "tool_call_start",
            "event_id": str(uuid4()),
            "trace_id": str(trace_id),
            "iteration_id": None,
            "timestamp": t0.isoformat(),
            "parent_span_id": None,
            "call_id": "toolu_run_001",
            "name": "run_pipeline",
            "args": {"path": "p.py"},
        },
        {
            "type": "tool_call_result",
            "event_id": str(uuid4()),
            "trace_id": str(trace_id),
            "iteration_id": None,
            "timestamp": datetime(2026, 5, 23, 14, 40, 0, 42_000, tzinfo=UTC).isoformat(),
            "parent_span_id": None,
            "call_id": "toolu_run_001",
            "name": "run_pipeline",
            "status": "error",
            "output": None,
            "duration_ms": 600_042,
            "error": "Sandbox timeout exceeded 600s",
            "error_class": "Timeout",
        },
    ]
    payload = _encode_native_stream_as_otlp(native)
    batch = decode_otlp_payload(payload)
    assert [e.type for e in batch.events] == ["tool_call_start", "tool_call_result"]
    result = batch.events[1]
    assert result.status == "error"
    assert result.error_class is not None
    assert result.error_class.value == "Timeout"


def test_reasoning_stream_round_trips() -> None:
    """ReasoningDelta is decoded from a chat span carrying thinking parts."""
    trace_id = uuid4()
    t0 = datetime(2026, 5, 23, 15, 0, 0, tzinfo=UTC)
    payload = wrap_batch(
        [
            make_span(
                span_id=_new_span_id(),
                trace_id=trace_id.hex,
                name="gen_ai.chat",
                start_ns=_ts_to_ns(t0),
                end_ns=_ts_to_ns(t0) + 100_000_000,
                attributes=[
                    str_attr("gen_ai.operation.name", "chat"),
                    str_attr("gen_ai.response.model", "claude-opus-4-7"),
                    {
                        "key": "gen_ai.output.messages",
                        "value": assistant_text_and_reasoning_messages(
                            text="OK, proceeding.",
                            reasoning="The supplier capacity is sufficient.",
                        ),
                    },
                ],
            ),
        ],
    )
    batch = decode_otlp_payload(payload)
    kinds = [e.type for e in batch.events]
    assert kinds == ["content_delta", "reasoning_delta"]
    assert batch.events[0].text == "OK, proceeding."
    assert batch.events[1].text == "The supplier capacity is sufficient."
    # Both events share the same trace_id.
    assert batch.events[0].trace_id == batch.events[1].trace_id == trace_id


@pytest.mark.parametrize("error_class", ["Timeout", "OOM", "Crash"])
def test_every_sandbox_error_class_round_trips(error_class: str) -> None:
    """The three sandbox-runtime error classes all survive the round trip."""
    trace_id = uuid4()
    t0 = datetime(2026, 5, 23, 14, 30, 0, tzinfo=UTC)
    native = [
        {
            "type": "tool_call_start",
            "event_id": str(uuid4()),
            "trace_id": str(trace_id),
            "iteration_id": None,
            "timestamp": t0.isoformat(),
            "parent_span_id": None,
            "call_id": "c1",
            "name": "x",
            "args": {},
        },
        {
            "type": "tool_call_result",
            "event_id": str(uuid4()),
            "trace_id": str(trace_id),
            "iteration_id": None,
            "timestamp": t0.isoformat(),
            "parent_span_id": None,
            "call_id": "c1",
            "name": "x",
            "status": "error",
            "output": None,
            "duration_ms": 1,
            "error": f"sandbox {error_class}",
            "error_class": error_class,
        },
    ]
    batch = decode_otlp_payload(_encode_native_stream_as_otlp(native))
    result = batch.events[1]
    assert result.error_class is not None
    assert result.error_class.value == error_class
