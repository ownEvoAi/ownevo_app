"""Mapper unit tests over the hand-crafted OTLP-JSON fixture set.

≥20 fixtures defined in `_fixture_cases.py`. Each is exercised in two
modes:

  1. Through `decode_otlp_payload(dict)` — the in-process call path
     used by tests, batch importers, and the planned dry-run script.
  2. Round-tripped via JSON bytes — the wire path the HTTP layer sees.

Every accepted fixture also has its expected AgentEvent shape pinned
(kind + a handful of load-bearing fields). The full Pydantic
validation through `AgentEventAdapter` happens inside the mapper, so
a malformed expected shape would have failed at decode time.
"""

from __future__ import annotations

import json

import pytest
from ownevo_kernel.middleware.otel_receiver import (
    OtelDecodeError,
    OversizedPayloadError,
    decode_otlp_payload,
)

from ._fixture_cases import CASES, FixtureCase, write_fixtures_to_disk


def _ids(cases: list[FixtureCase]) -> list[str]:
    return [c.name for c in cases]


def _exception_class(name: str) -> type[BaseException]:
    return {
        "OtelDecodeError": OtelDecodeError,
        "OversizedPayloadError": OversizedPayloadError,
    }[name]


@pytest.fixture(autouse=True, scope="module")
def _persist_fixtures_to_disk() -> None:
    """Mirror the in-memory fixtures onto disk for human review.

    Side-effect-only; never asserts. The on-disk files are gitignored
    until the first commit (see `.gitignore` for the fixture dir) —
    once committed, they pin the wire shape against silent drift in
    `_fixture_cases.py`. Re-running the suite refreshes the mirror.
    """
    write_fixtures_to_disk()


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_decode_matches_expected_shape(case: FixtureCase) -> None:
    if case.raises:
        with pytest.raises(_exception_class(case.raises)):
            decode_otlp_payload(case.payload)
        return

    batch = decode_otlp_payload(case.payload)

    assert len(batch.events) == len(case.expected_events), (
        f"{case.name}: event count mismatch — got {len(batch.events)}, "
        f"expected {len(case.expected_events)}; warnings={batch.warnings}"
    )

    for got, expected in zip(batch.events, case.expected_events, strict=True):
        assert got.type == expected.kind, (
            f"{case.name}: kind mismatch — got {got.type}, expected {expected.kind}"
        )
        for field_name, field_value in expected.fields.items():
            actual = getattr(got, field_name)
            assert actual == field_value, (
                f"{case.name}: field {field_name!r} mismatch — "
                f"got {actual!r}, expected {field_value!r}"
            )

    if case.min_warnings:
        assert len(batch.warnings) >= case.min_warnings, (
            f"{case.name}: expected ≥{case.min_warnings} warnings, "
            f"got {len(batch.warnings)}"
        )
    if case.max_warnings is not None:
        assert len(batch.warnings) <= case.max_warnings, (
            f"{case.name}: expected ≤{case.max_warnings} warnings, "
            f"got {len(batch.warnings)}"
        )


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c.raises is None and isinstance(c.payload, dict)],
    ids=_ids([c for c in CASES if c.raises is None and isinstance(c.payload, dict)]),
)
def test_decode_from_json_bytes(case: FixtureCase) -> None:
    """Same fixture, this time round-tripped through JSON bytes.

    Mirrors the HTTP wire path (FastAPI hands the route bytes, the
    receiver decodes). Asserts only event-count parity; the per-field
    assertions are covered by the dict-path test above.
    """
    raw = json.dumps(case.payload).encode("utf-8")
    batch = decode_otlp_payload(raw)
    assert len(batch.events) == len(case.expected_events), (
        f"{case.name}: byte-path event count mismatch"
    )


def test_fixture_set_size() -> None:
    """Fixture set must cover every documented operation variant (≥20 cases)."""
    assert len(CASES) >= 20, f"only {len(CASES)} fixture cases; need ≥20 to cover all op variants"


def test_fixture_names_unique() -> None:
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))


def test_oversize_cap_is_configurable() -> None:
    """Caller can lower the cap to stress the size check."""
    small_payload = b'{"resourceSpans":[]}'
    # Default cap accepts it; cap of 5 bytes rejects it.
    decode_otlp_payload(small_payload, max_body_bytes=10_000)
    with pytest.raises(OversizedPayloadError):
        decode_otlp_payload(small_payload, max_body_bytes=5)


def test_empty_batch_decodes_to_zero_events() -> None:
    batch = decode_otlp_payload({"resourceSpans": []})
    assert batch.events == []
    assert batch.warnings == []


def test_str_payload_path_is_accepted() -> None:
    """str payloads (not bytes) go through the string decode branch."""
    import json as _json

    payload_dict = {"resourceSpans": []}
    batch = decode_otlp_payload(_json.dumps(payload_dict))
    assert batch.events == []
    assert batch.warnings == []


def test_str_payload_oversize_raises() -> None:
    """OversizedPayloadError is raised for oversized str payloads."""
    import json as _json

    small = _json.dumps({"resourceSpans": []})
    with pytest.raises(OversizedPayloadError):
        decode_otlp_payload(small, max_body_bytes=5)


def test_tool_status_unset_emits_warning() -> None:
    """STATUS_CODE_UNSET (numeric 0) on a tool span maps to 'ok' with a warning."""
    from ._fixture_helpers import make_span, str_attr, wrap_batch

    span = make_span(
        span_id="a1b2c3d4e5f60001",
        trace_id="a1b2c3d4e5f6000100000000000000aa",
        name="gen_ai.execute_tool",
        attributes=[
            str_attr("gen_ai.operation.name", "execute_tool"),
            str_attr("gen_ai.tool.call.id", "c_unset"),
            str_attr("gen_ai.tool.name", "unset_tool"),
            str_attr("gen_ai.tool.call.arguments", "{}"),
            str_attr("gen_ai.tool.call.result", "null"),
        ],
        status_code=0,  # STATUS_CODE_UNSET
    )
    batch = decode_otlp_payload(wrap_batch([span]))
    result = next((e for e in batch.events if e.type == "tool_call_result"), None)
    assert result is not None
    assert result.status == "ok"
    assert any("UNSET" in w.reason for w in batch.warnings)


def test_external_error_class_emits_warning() -> None:
    """ownevo.error_class received over external OTLP ingest emits a warning."""
    from ._fixture_helpers import make_span, str_attr, wrap_batch

    span = make_span(
        span_id="b2c3d4e5f6700001",
        trace_id="b2c3d4e5f670000100000000000000bb",
        name="gen_ai.execute_tool",
        attributes=[
            str_attr("gen_ai.operation.name", "execute_tool"),
            str_attr("gen_ai.tool.call.id", "c_ext"),
            str_attr("gen_ai.tool.name", "ext_tool"),
            str_attr("gen_ai.tool.call.arguments", "{}"),
            str_attr("gen_ai.tool.call.result", "null"),
            str_attr("ownevo.error_class", "Timeout"),
        ],
        status_code=2,  # STATUS_CODE_ERROR
        status_message="sandbox timeout",
    )
    batch = decode_otlp_payload(wrap_batch([span]))
    assert any("unattested" in w.reason.lower() for w in batch.warnings)


def test_retrieval_documents_cap() -> None:
    """Retrieval spans with > _MAX_RETRIEVAL_DOCS_PER_SPAN docs are truncated."""
    from ownevo_kernel.middleware.otel_receiver.mapper import _MAX_RETRIEVAL_DOCS_PER_SPAN

    from ._fixture_helpers import kvlist_value, make_span, str_attr, wrap_batch

    doc = kvlist_value([str_attr("id", "d"), str_attr("content", "x")])
    over_limit = _MAX_RETRIEVAL_DOCS_PER_SPAN + 1
    span = make_span(
        span_id="c3d4e5f670800001",
        trace_id="c3d4e5f67080000100000000000000cc",
        name="gen_ai.retrieval",
        attributes=[
            str_attr("gen_ai.operation.name", "retrieval"),
            {
                "key": "gen_ai.retrieval.documents",
                "value": {"arrayValue": {"values": [doc] * over_limit}},
            },
        ],
    )
    batch = decode_otlp_payload(wrap_batch([span]))
    assert len(batch.events) == _MAX_RETRIEVAL_DOCS_PER_SPAN
    assert any("capping" in w.reason.lower() for w in batch.warnings)


def test_unicode_text_passes_through() -> None:
    """UTF-8 text content survives the decode round-trip."""
    from ._fixture_helpers import (
        assistant_text_messages,
        make_span,
        str_attr,
        wrap_batch,
    )

    payload = wrap_batch(
        [
            make_span(
                span_id="abcd" * 4,
                trace_id="ef01" * 8,
                attributes=[
                    str_attr("gen_ai.operation.name", "chat"),
                    str_attr("gen_ai.response.model", "claude-opus-4-7"),
                    {
                        "key": "gen_ai.output.messages",
                        "value": assistant_text_messages("héllo, 日本語, 🚀"),
                    },
                ],
            ),
        ],
    )
    batch = decode_otlp_payload(payload)
    assert len(batch.events) == 1
    assert batch.events[0].text == "héllo, 日本語, 🚀"
