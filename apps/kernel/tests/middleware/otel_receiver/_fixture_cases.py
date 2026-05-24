"""Hand-crafted OTLP-JSON fixtures with their expected decoded shapes.

20+ cases covering:

  * Every gen_ai.operation.name the receiver recognises (chat,
    text_completion, generate_content, execute_tool, invoke_agent,
    invoke_workflow, retrieval) — happy path.
  * Tool-call lifecycle pairs (start + result, both success and error).
  * Reasoning content alongside text.
  * Malformed payloads (not JSON, not an object, missing
    resourceSpans, span missing trace_id / span_id, oversize body).
  * Missing required fields (LLM span with no model, tool span with no
    call_id).
  * Tenant-isolation edge case (resource attribute carrying a
    workspace id — currently informational only, but documented).
  * Snake-case keys (some emitters drop OTLP camelCase).
  * Unknown operation name (skipped with warning).

Each case is a `FixtureCase`: the payload dict, a list of expected
`AgentEvent` summaries (kind + load-bearing fields), expected
warning count, and any expected raised error class.

The fixtures are also persisted to JSON files under `fixtures/` for
human review — the test runner re-derives them from this module so
the on-disk copies cannot silently drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._fixture_helpers import (
    assistant_text_and_reasoning_messages,
    assistant_text_messages,
    int_attr,
    make_span,
    str_attr,
    wrap_batch,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@dataclass
class ExpectedEvent:
    kind: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class FixtureCase:
    name: str
    payload: Any  # dict / str / bytes
    expected_events: list[ExpectedEvent] = field(default_factory=list)
    min_warnings: int = 0
    max_warnings: int | None = None
    raises: str | None = None  # exception class name, e.g. "OtelDecodeError"


# Stable test IDs — 16-byte traceId, 8-byte spanId hex (OTel spec widths).
T1 = "11111111111111111111111111111111"
T2 = "22222222222222222222222222222222"
T3 = "33333333333333333333333333333333"
SP1 = "1111111111111111"
SP2 = "2222222222222222"
SP3 = "3333333333333333"
SP4 = "4444444444444444"
SP5 = "5555555555555555"
SP6 = "6666666666666666"
SP7 = "7777777777777777"
SP8 = "8888888888888888"


def _chat_span(
    *,
    span_id: str = SP1,
    trace_id: str = T1,
    text: str = "Hello, supplier S-7821 has 14-day lead time.",
    model: str = "claude-opus-4-7",
) -> dict[str, Any]:
    return make_span(
        span_id=span_id,
        trace_id=trace_id,
        name="gen_ai.chat",
        attributes=[
            str_attr("gen_ai.operation.name", "chat"),
            str_attr("gen_ai.response.model", model),
            int_attr("gen_ai.usage.input_tokens", 120),
            int_attr("gen_ai.usage.output_tokens", 35),
            {"key": "gen_ai.output.messages", "value": assistant_text_messages(text)},
        ],
    )


def _tool_span(
    *,
    call_id: str = "toolu_abc",
    name: str = "lookup_supplier",
    span_id: str = SP2,
    trace_id: str = T1,
    parent: str = SP1,
    status_code: int = 1,
    status_message: str = "",
    args_json: str = '{"supplier_id":"S-7821"}',
    result_json: str = '{"lead_time_days":14}',
    error_class: str | None = None,
) -> dict[str, Any]:
    attrs = [
        str_attr("gen_ai.operation.name", "execute_tool"),
        str_attr("gen_ai.tool.call.id", call_id),
        str_attr("gen_ai.tool.name", name),
        str_attr("gen_ai.tool.call.arguments", args_json),
        str_attr("gen_ai.tool.call.result", result_json),
    ]
    if error_class:
        attrs.append(str_attr("ownevo.error_class", error_class))
    return make_span(
        span_id=span_id,
        trace_id=trace_id,
        parent_span_id=parent,
        name="gen_ai.execute_tool",
        attributes=attrs,
        status_code=status_code,
        status_message=status_message,
        start_ns=1_700_000_001_000_000_000,
        end_ns=1_700_000_001_420_000_000,
    )


def _retrieval_span(
    *,
    span_id: str = SP3,
    trace_id: str = T1,
    parent: str = SP1,
) -> dict[str, Any]:
    return make_span(
        span_id=span_id,
        trace_id=trace_id,
        parent_span_id=parent,
        name="gen_ai.retrieval",
        attributes=[
            str_attr("gen_ai.operation.name", "retrieval"),
            {
                "key": "gen_ai.retrieval.documents",
                "value": {
                    "arrayValue": {
                        "values": [
                            {
                                "kvlistValue": {
                                    "values": [
                                        str_attr("id", "doc-1"),
                                        str_attr(
                                            "content",
                                            "Supplier S-7821 14-day lead time.",
                                        ),
                                    ],
                                },
                            },
                            {
                                "kvlistValue": {
                                    "values": [
                                        str_attr("id", "doc-2"),
                                        str_attr("content", "Capacity 0.82."),
                                    ],
                                },
                            },
                        ],
                    },
                },
            },
        ],
    )


def _build_cases() -> list[FixtureCase]:
    cases: list[FixtureCase] = []

    # ---- happy-path: chat / text_completion / generate_content ----

    cases.append(
        FixtureCase(
            name="01_chat_basic_text",
            payload=wrap_batch([_chat_span()]),
            expected_events=[
                ExpectedEvent(
                    "content_delta",
                    {
                        "model": "claude-opus-4-7",
                        "text": "Hello, supplier S-7821 has 14-day lead time.",
                    },
                ),
            ],
        ),
    )

    cases.append(
        FixtureCase(
            name="02_text_completion_op",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[
                            str_attr("gen_ai.operation.name", "text_completion"),
                            str_attr("gen_ai.request.model", "gpt-4o"),
                            {
                                "key": "gen_ai.output.messages",
                                "value": assistant_text_messages("done"),
                            },
                        ],
                    ),
                ],
            ),
            expected_events=[
                ExpectedEvent("content_delta", {"model": "gpt-4o", "text": "done"}),
            ],
        ),
    )

    cases.append(
        FixtureCase(
            name="03_generate_content_op",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[
                            str_attr("gen_ai.operation.name", "generate_content"),
                            str_attr("gen_ai.response.model", "gemini-1.5-pro"),
                            {
                                "key": "gen_ai.output.messages",
                                "value": assistant_text_messages("ok"),
                            },
                        ],
                    ),
                ],
            ),
            expected_events=[ExpectedEvent("content_delta", {"text": "ok"})],
        ),
    )

    # ---- reasoning content ----

    cases.append(
        FixtureCase(
            name="04_chat_with_reasoning",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[
                            str_attr("gen_ai.operation.name", "chat"),
                            str_attr("gen_ai.response.model", "claude-opus-4-7"),
                            int_attr("gen_ai.usage.reasoning.output_tokens", 22),
                            {
                                "key": "gen_ai.output.messages",
                                "value": assistant_text_and_reasoning_messages(
                                    text="Action taken.",
                                    reasoning="Checking supplier capacity first.",
                                ),
                            },
                        ],
                    ),
                ],
            ),
            expected_events=[
                ExpectedEvent("content_delta", {"text": "Action taken."}),
                ExpectedEvent(
                    "reasoning_delta",
                    {"text": "Checking supplier capacity first."},
                ),
            ],
        ),
    )

    # ---- tool call: success ----

    cases.append(
        FixtureCase(
            name="05_tool_call_ok",
            payload=wrap_batch([_tool_span()]),
            expected_events=[
                ExpectedEvent(
                    "tool_call_start",
                    {"call_id": "toolu_abc", "name": "lookup_supplier"},
                ),
                ExpectedEvent(
                    "tool_call_result",
                    {
                        "call_id": "toolu_abc",
                        "status": "ok",
                        "duration_ms": 420,
                    },
                ),
            ],
        ),
    )

    # ---- tool call: logical error (status=error, no error_class) ----

    cases.append(
        FixtureCase(
            name="06_tool_call_logical_error",
            payload=wrap_batch(
                [
                    _tool_span(
                        status_code=2,
                        status_message="supplier not found",
                        result_json='{"error":"missing"}',
                    ),
                ],
            ),
            expected_events=[
                ExpectedEvent("tool_call_start", {"name": "lookup_supplier"}),
                ExpectedEvent(
                    "tool_call_result",
                    {"status": "error", "error_class": None},
                ),
            ],
        ),
    )

    # ---- tool call: sandbox timeout (status=error, error_class=Timeout) ----

    cases.append(
        FixtureCase(
            name="07_tool_call_sandbox_timeout",
            payload=wrap_batch(
                [
                    _tool_span(
                        status_code=2,
                        status_message="sandbox timeout",
                        error_class="Timeout",
                    ),
                ],
            ),
            expected_events=[
                ExpectedEvent("tool_call_start", {}),
                ExpectedEvent(
                    "tool_call_result",
                    {"status": "error", "error_class": "Timeout"},
                ),
            ],
        ),
    )

    # ---- agent-root spans: consumed, no event ----

    cases.append(
        FixtureCase(
            name="08_invoke_agent_root",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[str_attr("gen_ai.operation.name", "invoke_agent")],
                    ),
                ],
            ),
            expected_events=[],
        ),
    )

    cases.append(
        FixtureCase(
            name="09_invoke_workflow_root",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[str_attr("gen_ai.operation.name", "invoke_workflow")],
                    ),
                ],
            ),
            expected_events=[],
        ),
    )

    # ---- retrieval → citations ----

    cases.append(
        FixtureCase(
            name="10_retrieval_with_documents",
            payload=wrap_batch([_retrieval_span()]),
            expected_events=[
                ExpectedEvent("citation", {"ref": 1, "source": "doc-1"}),
                ExpectedEvent("citation", {"ref": 2, "source": "doc-2"}),
            ],
        ),
    )

    cases.append(
        FixtureCase(
            name="11_retrieval_no_documents_skipped",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[str_attr("gen_ai.operation.name", "retrieval")],
                    ),
                ],
            ),
            expected_events=[],
        ),
    )

    # ---- end-to-end: agent root + LLM call + tool pair ----

    cases.append(
        FixtureCase(
            name="12_end_to_end_agent_run",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[
                            str_attr("gen_ai.operation.name", "invoke_agent"),
                        ],
                    ),
                    _chat_span(span_id=SP2, trace_id=T1),
                    _tool_span(span_id=SP3, trace_id=T1, parent=SP1),
                ],
            ),
            expected_events=[
                ExpectedEvent("content_delta", {}),
                ExpectedEvent("tool_call_start", {}),
                ExpectedEvent("tool_call_result", {}),
            ],
        ),
    )

    # ---- snake-case keys (some emitters omit camelCase) ----

    cases.append(
        FixtureCase(
            name="13_snake_case_keys",
            payload={
                "resource_spans": [
                    {
                        "scope_spans": [
                            {
                                "spans": [
                                    {
                                        "trace_id": T1,
                                        "span_id": SP1,
                                        "parent_span_id": "",
                                        "name": "x",
                                        "start_time_unix_nano": "1700000000000000000",
                                        "end_time_unix_nano": "1700000000500000000",
                                        "attributes": [
                                            str_attr("gen_ai.operation.name", "chat"),
                                            str_attr(
                                                "gen_ai.response.model",
                                                "claude-opus-4-7",
                                            ),
                                            {
                                                "key": "gen_ai.output.messages",
                                                "value": assistant_text_messages("ok"),
                                            },
                                        ],
                                        "status": {"code": 1},
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
            expected_events=[ExpectedEvent("content_delta", {"text": "ok"})],
        ),
    )

    # ---- unknown operation → skipped with warning ----

    cases.append(
        FixtureCase(
            name="14_unknown_operation_skipped",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[
                            str_attr("gen_ai.operation.name", "wat_is_this"),
                        ],
                    ),
                ],
            ),
            expected_events=[],
            min_warnings=1,
        ),
    )

    # ---- LLM span missing model → skipped with warning ----

    cases.append(
        FixtureCase(
            name="15_llm_missing_model_skipped",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[
                            str_attr("gen_ai.operation.name", "chat"),
                            {
                                "key": "gen_ai.output.messages",
                                "value": assistant_text_messages("hi"),
                            },
                        ],
                    ),
                ],
            ),
            expected_events=[],
            min_warnings=1,
        ),
    )

    # ---- tool span missing call_id → skipped with warning ----

    cases.append(
        FixtureCase(
            name="16_tool_missing_call_id_skipped",
            payload=wrap_batch(
                [
                    make_span(
                        span_id=SP1,
                        trace_id=T1,
                        attributes=[
                            str_attr("gen_ai.operation.name", "execute_tool"),
                            str_attr("gen_ai.tool.name", "search"),
                        ],
                    ),
                ],
            ),
            expected_events=[],
            min_warnings=1,
        ),
    )

    # ---- span missing trace_id → skipped with warning ----

    cases.append(
        FixtureCase(
            name="17_span_missing_trace_id_skipped",
            payload={
                "resourceSpans": [
                    {
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "spanId": SP1,
                                        "startTimeUnixNano": "1700000000000000000",
                                        "endTimeUnixNano": "1700000000100000000",
                                        "attributes": [
                                            str_attr("gen_ai.operation.name", "chat"),
                                        ],
                                        "status": {"code": 1},
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
            expected_events=[],
            min_warnings=1,
        ),
    )

    # ---- malformed payloads ----

    cases.append(
        FixtureCase(
            name="18_malformed_not_json",
            payload=b"{not json at all",
            raises="OtelDecodeError",
        ),
    )

    cases.append(
        FixtureCase(
            name="19_missing_resource_spans",
            payload={"otherField": []},
            raises="OtelDecodeError",
        ),
    )

    cases.append(
        FixtureCase(
            name="20_oversized_payload",
            payload=b'{"resourceSpans":[]}' + b" " * (9 * 1024 * 1024),
            raises="OversizedPayloadError",
        ),
    )

    # ---- tenant-isolation edge case: resource carries workspace id ----

    cases.append(
        FixtureCase(
            name="21_resource_workspace_id",
            payload={
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                str_attr("ownevo.workspace_id", "ws-demo"),
                                str_attr("service.name", "customer-langchain-agent"),
                            ],
                        },
                        "scopeSpans": [
                            {
                                "scope": {"name": "langsmith-collector-proxy"},
                                "spans": [_chat_span()],
                            },
                        ],
                    },
                ],
            },
            expected_events=[ExpectedEvent("content_delta", {})],
        ),
    )

    # ---- payload is a JSON array (not an object) → 400 ----

    cases.append(
        FixtureCase(
            name="22_payload_is_array",
            payload=b"[]",
            raises="OtelDecodeError",
        ),
    )

    return cases


CASES: list[FixtureCase] = _build_cases()


# ---------------------------------------------------------------------------
# On-disk JSON mirror for human review.
# ---------------------------------------------------------------------------


_MAX_DISK_MIRROR_BYTES = 64 * 1024
"""Cap on per-fixture on-disk size. Oversize-payload cases (multi-MB)
are summarised rather than written verbatim — the in-memory case
still exercises the full body."""


def write_fixtures_to_disk() -> None:
    """Persist every fixture's payload to `fixtures/<name>.json`.

    Only payloads that are dict-shaped are written; raw bytes (malformed
    cases) get a `.raw.txt` mirror instead so reviewers see the exact
    body the receiver rejected. Payloads larger than the disk-mirror
    cap (the oversize-payload case is many MB) get a `.summary.txt`
    one-liner instead — the in-memory case still drives the receiver
    with the full body, only the on-disk reviewer copy is summarised.
    """
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        if isinstance(case.payload, dict):
            target = FIXTURE_DIR / f"{case.name}.json"
            target.write_text(json.dumps(case.payload, indent=2, sort_keys=True))
        elif isinstance(case.payload, (bytes, str)):
            raw = (
                case.payload
                if isinstance(case.payload, bytes)
                else case.payload.encode("utf-8")
            )
            if len(raw) > _MAX_DISK_MIRROR_BYTES:
                summary_target = FIXTURE_DIR / f"{case.name}.summary.txt"
                summary_target.write_text(
                    f"# oversize fixture — {len(raw)} bytes, "
                    f"not mirrored verbatim. See _fixture_cases.py::{case.name}.",
                )
            else:
                txt_target = FIXTURE_DIR / f"{case.name}.raw.txt"
                try:
                    txt_target.write_text(raw.decode("utf-8", errors="replace"))
                except Exception:  # noqa: BLE001
                    txt_target.write_bytes(raw[:1024])
