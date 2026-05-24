"""OTLP-JSON → AgentEvent mapper.

Implements the mapping pinned in `MAPPING.md`. The mapper is a pure
function over the parsed payload — no IO, no global state — so the
fixture tests can exercise it directly without the HTTP layer.

Shape of the input
------------------
OTLP-JSON for traces looks roughly like::

    {
      "resourceSpans": [
        {
          "resource": { "attributes": [{"key": "...", "value": {"stringValue": "..."}}, ...] },
          "scopeSpans": [
            {
              "scope": { "name": "..." },
              "spans": [
                {
                  "traceId": "<16-byte hex>",
                  "spanId":  "<8-byte hex>",
                  "parentSpanId": "<8-byte hex>" | "",
                  "name": "...",
                  "kind": 1,
                  "startTimeUnixNano": "...",
                  "endTimeUnixNano":   "...",
                  "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                    ...
                  ],
                  "status": { "code": 1, "message": "..." },
                  "events": [...]
                }
              ]
            }
          ]
        }
      ]
    }

OTLP-JSON encodes attribute values as `AnyValue` objects keyed by type
(`stringValue`, `intValue`, `boolValue`, `kvlistValue`, `arrayValue`).
`_unwrap_anyvalue` collapses that shape into a plain Python value.

Shape of the output
-------------------
Per `MAPPING.md`, each GenAI span becomes one or two AgentEvents:

  * `gen_ai.operation.name = chat` (and the text/reasoning variants)
    → one `ContentDelta`, optionally one `ReasoningDelta`.
  * `gen_ai.operation.name = execute_tool` → one `ToolCallStart` +
    one `ToolCallResult`.
  * `gen_ai.operation.name = invoke_agent` / `invoke_workflow` /
    `create_agent` → no AgentEvent (root agent span is consumed silently).
  * everything else → skipped with a warning.

The mapper is intentionally permissive: spans the receiver doesn't
recognise are skipped via `DecodeWarning`, not raised. Hard errors
(malformed JSON, missing required ID fields) raise `OtelDecodeError`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ownevo_format import (
    AgentEvent,
    AgentEventAdapter,
    SandboxErrorClass,
)
from pydantic import ValidationError as PydanticValidationError

# ---------------------------------------------------------------------------
# Defaults / configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX_BODY_BYTES = 8 * 1024 * 1024
"""8 MiB cap on a single OTLP batch. Matches the OpenTelemetry collector
default for `otlphttpreceiver`; configurable on the receiver call site."""

# Recognised gen_ai.operation.name values. Anything outside this set is
# skipped with a warning rather than raising.
_OP_LLM_CHAT = "chat"
_OP_LLM_TEXT = "text_completion"
_OP_LLM_GENERATE_CONTENT = "generate_content"
_OP_TOOL = "execute_tool"
_OP_AGENT_INVOKE = "invoke_agent"
_OP_AGENT_CREATE = "create_agent"
_OP_AGENT_WORKFLOW = "invoke_workflow"
_OP_RETRIEVAL = "retrieval"
_OP_EMBEDDINGS = "embeddings"

_LLM_OPS = frozenset({_OP_LLM_CHAT, _OP_LLM_TEXT, _OP_LLM_GENERATE_CONTENT})
_AGENT_ROOT_OPS = frozenset({_OP_AGENT_INVOKE, _OP_AGENT_CREATE, _OP_AGENT_WORKFLOW})
_RETRIEVAL_OPS = frozenset({_OP_RETRIEVAL, _OP_EMBEDDINGS})


# OTel span status codes. Numeric per the protobuf encoding; OTLP-JSON
# also accepts the string form ("STATUS_CODE_OK").
#
# UNSET (numeric 0) is deliberately separated from OK (numeric 1).
# Many OTLP exporters (including some LangSmith configurations) emit
# status_code=0 for all spans, even failed ones, because they do not
# explicitly call Span.set_status(). Mapping UNSET to 'ok' silently
# causes the failure clustering to miss those spans. When a tool span
# carries UNSET, the mapper emits a warning so the operator can see the
# ambiguity in the ingest log; the status still defaults to 'ok' so the
# event validates through the AgentEvent schema.
_STATUS_UNSET_VALUES = frozenset({0, "STATUS_CODE_UNSET", "UNSET"})
_STATUS_OK_VALUES = frozenset({1, "STATUS_CODE_OK", "OK"})
_STATUS_ERR_VALUES = frozenset({2, "STATUS_CODE_ERROR", "ERROR"})

# Pre-computed frozenset of valid SandboxErrorClass values. Used in
# _emit_tool_events to avoid a generator scan on every error span.
_SANDBOX_ERROR_CLASS_VALUES: frozenset[str] = frozenset(
    e.value for e in SandboxErrorClass
)

# Maximum characters kept from a retrieval document quote. Matches the
# downstream citation field width in the trace schema.
_MAX_CITATION_QUOTE_CHARS = 2048

# Maximum number of retrieval documents processed per span. An 8 MiB
# payload can carry ~160k minimal doc entries; each decoded Citation
# event object takes ~1 KB in memory — unbounded processing would
# amplify a maximally-compact input by ~20x. The cap keeps peak memory
# within 2x of the raw payload.
_MAX_RETRIEVAL_DOCS_PER_SPAN = 1_000


# ---------------------------------------------------------------------------
# Errors / result types
# ---------------------------------------------------------------------------


class OtelDecodeError(ValueError):
    """Raised when the OTLP payload cannot be decoded at all.

    Examples: malformed JSON, missing `resourceSpans`, span without a
    `traceId` / `spanId`. Use `OversizedPayloadError` for the
    body-size case so the HTTP layer can return 413 instead of 400.
    """


class OversizedPayloadError(OtelDecodeError):
    """Raised when the raw payload exceeds the configured size cap."""


@dataclass(frozen=True)
class DecodeWarning:
    """One non-fatal issue noted while decoding the payload.

    Warnings cover skipped spans (unknown operation, missing required
    GenAI attributes) and partially-decoded spans (e.g. tool span with
    no arguments). The HTTP layer logs them and responds 200; only
    `OtelDecodeError` translates to a 4xx response.
    """

    span_id: str | None
    reason: str


@dataclass
class DecodedBatch:
    """Result envelope returned by `decode_otlp_payload`."""

    events: list[AgentEvent] = field(default_factory=list)
    warnings: list[DecodeWarning] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def decode_otlp_payload(
    payload: bytes | str | dict[str, Any],
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> DecodedBatch:
    """Decode one OTLP-JSON `ResourceSpans` batch into AgentEvents.

    Args:
        payload: raw bytes, str, or a pre-parsed dict. Bytes/str are
            JSON-decoded; dict is consumed directly (the test path uses
            dicts so fixtures can be human-readable JSON files).
        max_body_bytes: cap on the raw bytes/str length. Applies only
            when `payload` is bytes/str (a dict has no fixed wire size).

    Returns:
        DecodedBatch with the extracted AgentEvents and any
        non-fatal warnings.

    Raises:
        OversizedPayloadError: bytes/str payload exceeds `max_body_bytes`.
        OtelDecodeError: payload is malformed or missing required fields.
    """
    parsed = _parse_payload(payload, max_body_bytes=max_body_bytes)

    if not isinstance(parsed, dict):
        raise OtelDecodeError(f"OTLP payload must be a JSON object, got {type(parsed).__name__}")

    # OTLP-JSON uses camelCase keys by spec; some emitters use snake_case.
    resource_spans = parsed.get("resourceSpans")
    if resource_spans is None:
        resource_spans = parsed.get("resource_spans")
    if resource_spans is None:
        raise OtelDecodeError("OTLP payload missing 'resourceSpans'")
    if not isinstance(resource_spans, list):
        raise OtelDecodeError("'resourceSpans' must be a list")

    result = DecodedBatch()
    for rs in resource_spans:
        _decode_resource_spans(rs, result)
    return result


# ---------------------------------------------------------------------------
# Top-level traversal
# ---------------------------------------------------------------------------


def _parse_payload(
    payload: bytes | str | dict[str, Any],
    *,
    max_body_bytes: int,
) -> Any:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        if len(payload) > max_body_bytes:
            raise OversizedPayloadError(
                f"payload {len(payload)} bytes exceeds cap {max_body_bytes}",
            )
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise OtelDecodeError(f"OTLP payload is not valid JSON: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise OtelDecodeError(f"OTLP payload is not valid UTF-8: {exc}") from exc
    if isinstance(payload, str):
        # Encode once to get the exact byte count and reuse the bytes for
        # parsing — avoids a redundant encode-to-measure-then-encode-again.
        encoded = payload.encode("utf-8")
        if len(encoded) > max_body_bytes:
            raise OversizedPayloadError(
                f"payload {len(encoded)} bytes exceeds cap {max_body_bytes}",
            )
        try:
            return json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise OtelDecodeError(f"OTLP payload is not valid JSON: {exc}") from exc
    raise OtelDecodeError(
        f"OTLP payload must be bytes / str / dict, got {type(payload).__name__}",
    )


def _decode_resource_spans(rs: Any, out: DecodedBatch) -> None:
    if not isinstance(rs, dict):
        out.warnings.append(DecodeWarning(None, "resourceSpans entry is not an object"))
        return
    scope_spans = rs.get("scopeSpans") or rs.get("scope_spans") or []
    if not isinstance(scope_spans, list):
        out.warnings.append(DecodeWarning(None, "scopeSpans is not a list"))
        return
    for ss in scope_spans:
        if not isinstance(ss, dict):
            out.warnings.append(DecodeWarning(None, "scopeSpans entry is not an object"))
            continue
        spans = ss.get("spans") or []
        if not isinstance(spans, list):
            out.warnings.append(DecodeWarning(None, "spans is not a list"))
            continue
        for span in spans:
            try:
                _decode_span(span, out)
            except PydanticValidationError as exc:
                # Pydantic validation failure inside _build_event — the span
                # data passed mapper logic but was rejected by the AgentEvent
                # schema. Degrade to a warning rather than letting the
                # ValidationError bubble up as a 500.
                out.warnings.append(
                    DecodeWarning(_safe_span_id(span), f"schema validation error: {exc}"),
                )
            except OtelDecodeError as exc:
                # Per-span hard errors degrade to warnings — one bad span
                # must not poison the whole batch. The HTTP layer only
                # returns 4xx for batch-level errors.
                out.warnings.append(
                    DecodeWarning(_safe_span_id(span), f"hard error: {exc}"),
                )


# ---------------------------------------------------------------------------
# Per-span decoding
# ---------------------------------------------------------------------------


def _decode_span(span: Any, out: DecodedBatch) -> None:
    if not isinstance(span, dict):
        out.warnings.append(DecodeWarning(None, "span is not an object"))
        return

    span_id_hex = span.get("spanId") or span.get("span_id")
    trace_id_hex = span.get("traceId") or span.get("trace_id")
    if not span_id_hex or not trace_id_hex:
        out.warnings.append(
            DecodeWarning(span_id_hex, "span missing traceId or spanId"),
        )
        return

    try:
        event_uuid = _hex_to_uuid(span_id_hex, expected_bytes=8)
        trace_uuid = _hex_to_uuid(trace_id_hex, expected_bytes=16)
    except ValueError as exc:
        out.warnings.append(DecodeWarning(span_id_hex, f"id decode failed: {exc}"))
        return

    parent_hex = span.get("parentSpanId") or span.get("parent_span_id") or ""
    parent_uuid: UUID | None
    if parent_hex:
        try:
            parent_uuid = _hex_to_uuid(parent_hex, expected_bytes=8)
        except ValueError:
            parent_uuid = None
    else:
        parent_uuid = None

    start_ns = _parse_unix_nano(span.get("startTimeUnixNano") or span.get("start_time_unix_nano"))
    end_ns = _parse_unix_nano(span.get("endTimeUnixNano") or span.get("end_time_unix_nano"))
    if start_ns is None:
        out.warnings.append(DecodeWarning(span_id_hex, "span missing startTimeUnixNano"))
        return

    attrs = _flatten_attributes(span.get("attributes") or [])
    op_name = attrs.get("gen_ai.operation.name")

    common = _CommonFields(
        event_id=event_uuid,
        trace_id=trace_uuid,
        parent_span_id=parent_uuid,
        timestamp=_ns_to_datetime(start_ns),
    )

    if op_name in _LLM_OPS:
        _emit_llm_events(span, attrs, common, out)
    elif op_name == _OP_TOOL:
        _emit_tool_events(span, attrs, common, start_ns, end_ns, out)
    elif op_name in _AGENT_ROOT_OPS:
        # Root agent spans anchor the trace but do not produce an AgentEvent.
        return
    elif op_name in _RETRIEVAL_OPS:
        _emit_retrieval_events(attrs, common, out)
    elif op_name is None:
        out.warnings.append(
            DecodeWarning(span_id_hex, "span has no gen_ai.operation.name — skipped"),
        )
    else:
        out.warnings.append(
            DecodeWarning(
                span_id_hex,
                f"unrecognised gen_ai.operation.name={str(op_name)[:128]!r}",
            ),
        )


# ---------------------------------------------------------------------------
# Variant emitters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CommonFields:
    event_id: UUID
    trace_id: UUID
    parent_span_id: UUID | None
    timestamp: datetime


def _emit_llm_events(
    span: dict[str, Any],
    attrs: dict[str, Any],
    common: _CommonFields,
    out: DecodedBatch,
) -> None:
    model = attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")
    if not model:
        out.warnings.append(
            DecodeWarning(span.get("spanId"), "LLM span missing gen_ai.*.model"),
        )
        return

    text, reasoning = _extract_llm_text(attrs)

    if text:
        out.events.append(
            _build_event(
                {
                    "type": "content_delta",
                    "event_id": str(common.event_id),
                    "trace_id": str(common.trace_id),
                    "parent_span_id": (
                        str(common.parent_span_id) if common.parent_span_id else None
                    ),
                    "timestamp": common.timestamp.isoformat(),
                    "text": text,
                    "model": model,
                    "cumulative_text": text,
                },
            ),
        )
    if reasoning:
        # Reasoning gets a deterministic synthetic event_id derived from
        # the span's event_id so a round-trip on the same span produces
        # the same UUIDs. Flip the leading nibble to disambiguate.
        reasoning_uuid = _derive_uuid(common.event_id, salt=b"reasoning")
        out.events.append(
            _build_event(
                {
                    "type": "reasoning_delta",
                    "event_id": str(reasoning_uuid),
                    "trace_id": str(common.trace_id),
                    "parent_span_id": (
                        str(common.parent_span_id) if common.parent_span_id else None
                    ),
                    "timestamp": common.timestamp.isoformat(),
                    "text": reasoning,
                    "model": model,
                },
            ),
        )

    if not text and not reasoning:
        out.warnings.append(
            DecodeWarning(
                span.get("spanId"),
                "LLM span carried neither text nor reasoning content — skipped",
            ),
        )


def _emit_tool_events(
    span: dict[str, Any],
    attrs: dict[str, Any],
    common: _CommonFields,
    start_ns: int,
    end_ns: int | None,
    out: DecodedBatch,
) -> None:
    call_id = attrs.get("gen_ai.tool.call.id")
    name = attrs.get("gen_ai.tool.name")
    if not call_id or not name:
        out.warnings.append(
            DecodeWarning(
                span.get("spanId"),
                "tool span missing gen_ai.tool.call.id or gen_ai.tool.name",
            ),
        )
        return

    raw_args = attrs.get("gen_ai.tool.call.arguments")
    args = _coerce_dict(raw_args, default={})
    raw_result = attrs.get("gen_ai.tool.call.result")
    output = _maybe_decode_json(raw_result)

    # Tool span emits both start and result. The start carries args,
    # the result carries output + duration + status. Both share the
    # trace_id and the result's parent_span_id points back at the start
    # (per SPEC.md § ToolCallResult).
    start_uuid = common.event_id
    result_uuid = _derive_uuid(common.event_id, salt=b"tool_result")

    out.events.append(
        _build_event(
            {
                "type": "tool_call_start",
                "event_id": str(start_uuid),
                "trace_id": str(common.trace_id),
                "parent_span_id": (
                    str(common.parent_span_id) if common.parent_span_id else None
                ),
                "timestamp": common.timestamp.isoformat(),
                "call_id": str(call_id),
                "name": str(name),
                "args": args,
            },
        ),
    )

    status_code = (span.get("status") or {}).get("code")
    status_message = (span.get("status") or {}).get("message")
    if status_code in _STATUS_ERR_VALUES:
        status = "error"
    elif status_code in _STATUS_UNSET_VALUES:
        # UNSET means the exporter did not call Span.set_status() — it is
        # not the same as an explicit OK. Warn so operators know their
        # exporter should be configured to set explicit status codes;
        # default to 'ok' so the event validates.
        out.warnings.append(
            DecodeWarning(
                span.get("spanId"),
                "tool span status UNSET (code 0) — defaulting to ok; "
                "configure your OTLP exporter to set explicit status codes",
            ),
        )
        status = "ok"
    else:
        status = "ok"
    duration_ms = _duration_ms(start_ns, end_ns)
    # ownevo.error_class is a vendor extension that the ownEvo sandbox
    # sets when an agent tool run fails at the infrastructure level
    # (Timeout, OOM, Crash). When it arrives over the external OTLP
    # ingest path it cannot be attested — an untrusted caller could set
    # it on any span to reclassify a real agent failure as an
    # infrastructure timeout, gaming the failure clustering signal.
    # Emit a warning and accept the value for now; when multi-tenant
    # auth lands, the acceptance rule narrows to workspace-attested spans.
    error_class_raw = attrs.get("ownevo.error_class")
    if error_class_raw is not None:
        out.warnings.append(
            DecodeWarning(
                span.get("spanId"),
                "ownevo.error_class received from external OTLP ingest — "
                "value is unattested and will be accepted as-is until auth lands",
            ),
        )

    result_payload: dict[str, Any] = {
        "type": "tool_call_result",
        "event_id": str(result_uuid),
        "trace_id": str(common.trace_id),
        "parent_span_id": str(start_uuid),
        "timestamp": (
            _ns_to_datetime(end_ns).isoformat() if end_ns else common.timestamp.isoformat()
        ),
        "call_id": str(call_id),
        "name": str(name),
        "status": status,
        "output": output,
        "duration_ms": duration_ms,
    }
    if status == "error":
        result_payload["error"] = status_message or "tool error"
        if error_class_raw in _SANDBOX_ERROR_CLASS_VALUES:
            result_payload["error_class"] = error_class_raw
    # status=="ok" → leave error / error_class as their default None
    out.events.append(_build_event(result_payload))


def _emit_retrieval_events(
    attrs: dict[str, Any],
    common: _CommonFields,
    out: DecodedBatch,
) -> None:
    docs = attrs.get("gen_ai.retrieval.documents")
    if not isinstance(docs, list) or not docs:
        # Per MAPPING.md: skipped when the doc list is missing. No
        # warning — many emitters omit it by design.
        return
    if len(docs) > _MAX_RETRIEVAL_DOCS_PER_SPAN:
        out.warnings.append(
            DecodeWarning(
                str(common.event_id),
                f"retrieval span has {len(docs)} documents; "
                f"capping at {_MAX_RETRIEVAL_DOCS_PER_SPAN} to bound memory use",
            ),
        )
        docs = docs[:_MAX_RETRIEVAL_DOCS_PER_SPAN]
    for i, doc in enumerate(docs, start=1):
        if not isinstance(doc, dict):
            continue
        source = doc.get("id") or doc.get("source")
        quote = doc.get("content") or doc.get("quote") or ""
        if not source:
            continue
        citation_uuid = _derive_uuid(common.event_id, salt=f"cite-{i}".encode())
        out.events.append(
            _build_event(
                {
                    "type": "citation",
                    "event_id": str(citation_uuid),
                    "trace_id": str(common.trace_id),
                    "parent_span_id": (
                        str(common.parent_span_id) if common.parent_span_id else None
                    ),
                    "timestamp": common.timestamp.isoformat(),
                    "ref": i,
                    "source": str(source),
                    "quote": str(quote)[:_MAX_CITATION_QUOTE_CHARS],
                },
            ),
        )


# ---------------------------------------------------------------------------
# OTLP-JSON value helpers
# ---------------------------------------------------------------------------


def _flatten_attributes(attrs: list[Any]) -> dict[str, Any]:
    """Collapse OTLP `KeyValue[]` into a plain dict keyed by name."""
    out: dict[str, Any] = {}
    for kv in attrs:
        if not isinstance(kv, dict):
            continue
        key = kv.get("key")
        if not key:
            continue
        out[str(key)] = _unwrap_anyvalue(kv.get("value"))
    return out


def _unwrap_anyvalue(value: Any) -> Any:
    """Collapse an OTLP `AnyValue` object into a plain Python value.

    OTLP-JSON wraps every value in a single-key envelope identifying
    the type (`stringValue`, `intValue`, `arrayValue`, `kvlistValue`,
    ...). Some emitters skip the envelope and pass the raw value
    through; this helper handles both shapes.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        # OTLP-JSON encodes int64 as a string ("12345") per spec.
        v = value["intValue"]
        try:
            return int(v)
        except (TypeError, ValueError):
            return v
    if "doubleValue" in value:
        return value["doubleValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "bytesValue" in value:
        return value["bytesValue"]
    if "arrayValue" in value:
        arr = value["arrayValue"].get("values") or []
        return [_unwrap_anyvalue(v) for v in arr]
    if "kvlistValue" in value:
        kvs = value["kvlistValue"].get("values") or []
        return _flatten_attributes(kvs)
    return value  # unknown envelope — pass through


def _maybe_decode_json(raw: Any) -> Any:
    """Decode a JSON string back to a Python value; pass non-strings through.

    OTLP attribute values are scalars, so structured tool args / results
    are typically serialised as JSON-encoded strings. Round-tripping
    them back to dict/list lets the resulting AgentEvent.output stay
    structurally identical to the native event.
    """
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _coerce_dict(raw: Any, *, default: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return default
        return parsed if isinstance(parsed, dict) else default
    return default


def _extract_llm_text(attrs: dict[str, Any]) -> tuple[str, str]:
    """Pull (assistant text, reasoning text) out of `gen_ai.output.messages`.

    Returns ("", "") when neither is present. The shape upstream is
    `messages[*].parts[*].{type, content}` with role on the message;
    only assistant messages are walked here.
    """
    messages = attrs.get("gen_ai.output.messages")
    if not isinstance(messages, list):
        return ("", "")

    text_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") not in (None, "assistant"):
            continue
        parts = msg.get("parts") or []
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type", "text")
            content = part.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content)
            if ptype == "thinking":
                reasoning_chunks.append(content)
            elif ptype in ("text", None):
                text_chunks.append(content)
    return ("".join(text_chunks), "".join(reasoning_chunks))


# ---------------------------------------------------------------------------
# ID / timestamp helpers
# ---------------------------------------------------------------------------


def _hex_to_uuid(hex_id: str, *, expected_bytes: int | None = None) -> UUID:
    """Pad or validate a hex OTel ID and parse it as a UUID.

    OTel trace_ids are 16 bytes (32 hex chars); span_ids are 8 bytes
    (16 hex chars). Padding 8-byte span_ids with a zero prefix gives a
    stable UUID — the round-trip test pins it.

    `expected_bytes` sets a strict width check:
      - 16 → span_id (8 bytes). Padded to 32 hex chars.
      - 32 → trace_id (16 bytes). No padding.
      - None → accepts both widths (legacy, no field context).

    A non-compliant width raises ValueError so the caller can emit a
    DecodeWarning rather than silently aliasing IDs from different
    address spaces.
    """
    raw = hex_id.strip().lower()
    if not raw:
        raise ValueError("empty hex id")
    if expected_bytes is not None:
        expected_hex_len = expected_bytes * 2
        if len(raw) != expected_hex_len:
            raise ValueError(
                f"expected {expected_hex_len}-char hex id ({expected_bytes} bytes), "
                f"got {len(raw)} chars: {hex_id!r}",
            )
    if len(raw) == 16:  # 8-byte span_id
        raw = ("0" * 16) + raw
    if len(raw) != 32:
        raise ValueError(f"unexpected hex id length {len(raw)}: {hex_id!r}")
    try:
        return UUID(hex=raw)
    except ValueError as exc:
        raise ValueError(f"invalid hex id {hex_id!r}: {exc}") from exc


def _derive_uuid(seed: UUID, *, salt: bytes) -> UUID:
    """Deterministically derive a sibling UUID from a seed.

    Used so a single OTel span that fans out into multiple AgentEvents
    (tool start + result, retrieval → multiple citations) gets stable,
    reproducible event_ids — the round-trip replay test relies on it.
    """
    seed_bytes = seed.bytes
    digest = bytearray(16)
    for i in range(16):
        digest[i] = seed_bytes[i] ^ salt[i % len(salt)] if salt else seed_bytes[i]
    # Set RFC-4122 version 4 + variant bits so the result is a well-formed UUID.
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(digest))


def _parse_unix_nano(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ns_to_datetime(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, tz=UTC)


def _duration_ms(start_ns: int, end_ns: int | None) -> int:
    if end_ns is None or end_ns < start_ns:
        return 0
    return max(0, (end_ns - start_ns) // 1_000_000)


def _safe_span_id(span: Any) -> str | None:
    if isinstance(span, dict):
        return span.get("spanId") or span.get("span_id")
    return None


def _build_event(payload: dict[str, Any]) -> AgentEvent:
    """Validate a dict-shaped AgentEvent through the discriminated union."""
    return AgentEventAdapter.validate_python(payload)
