"""Tests for the AgentEvent typed schema.

Covers W1 spike's go/no-go criterion: "at least one test passes against
the new types." We exercise enough of the schema to prove:

  - Discriminated-union parsing (dict -> typed variant) works
  - D3 sandbox failure semantics on ToolCallResult are enforced
  - Constructor + JSON round-trip is identity for each variant
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from ownevo_format import (
    AgentEventAdapter,
    Citation,
    ContentDelta,
    MonitorSignal,
    ReasoningDelta,
    SkillLoaded,
    ToolCallResult,
    ToolCallStart,
    is_skill_loaded,
    is_tool_call_result,
)
from pydantic import ValidationError


def _base_fields() -> dict:
    return {
        "event_id": uuid4(),
        "trace_id": uuid4(),
        "iteration_id": uuid4(),
        "timestamp": datetime.now(UTC),
        "parent_span_id": None,
    }


# ---------------------------------------------------------------------------
# Discriminated-union parsing — the W3 schema-freeze contract
# ---------------------------------------------------------------------------


def test_skill_loaded_parses_as_typed_variant():
    payload = {
        **_base_fields(),
        "type": "skill_loaded",
        "skill_id": "supplier-negotiation",
        "version_seq": 7,
        "retention_acknowledged": True,
    }
    event = AgentEventAdapter.validate_python(payload)
    assert isinstance(event, SkillLoaded)
    assert is_skill_loaded(event)
    assert event.skill_id == "supplier-negotiation"
    assert event.version_seq == 7


def test_tool_call_result_parses_as_typed_variant():
    payload = {
        **_base_fields(),
        "type": "tool_call_result",
        "call_id": "toolu_abc",
        "name": "lookup_supplier",
        "status": "ok",
        "output": {"lead_time_days": 14},
        "duration_ms": 420,
    }
    event = AgentEventAdapter.validate_python(payload)
    assert isinstance(event, ToolCallResult)
    assert is_tool_call_result(event)
    assert event.status == "ok"
    assert event.error_class is None


def test_unknown_type_rejected():
    payload = {
        **_base_fields(),
        "type": "not_a_real_variant",
    }
    with pytest.raises(ValidationError):
        AgentEventAdapter.validate_python(payload)


def test_missing_type_rejected():
    payload = {
        **_base_fields(),
        "skill_id": "x",
        "version_seq": 1,
    }
    with pytest.raises(ValidationError):
        AgentEventAdapter.validate_python(payload)


# ---------------------------------------------------------------------------
# D3 — ToolCallResult error semantics
# ---------------------------------------------------------------------------


def test_tool_call_ok_rejects_error_field():
    """status='ok' with non-null `error` is malformed per SPEC.md."""
    with pytest.raises(ValidationError):
        ToolCallResult(
            **_base_fields(),
            type="tool_call_result",
            call_id="c1",
            name="x",
            status="ok",
            output=None,
            duration_ms=10,
            error="this should not be allowed",
        )


def test_tool_call_ok_rejects_error_class():
    """status='ok' with non-null `error_class` is malformed per SPEC.md."""
    with pytest.raises(ValidationError):
        ToolCallResult(
            **_base_fields(),
            type="tool_call_result",
            call_id="c1",
            name="x",
            status="ok",
            output=None,
            duration_ms=10,
            error_class="Timeout",
        )


def test_tool_call_error_requires_error_field():
    """status='error' MUST have a non-null `error` message."""
    with pytest.raises(ValidationError):
        ToolCallResult(
            **_base_fields(),
            type="tool_call_result",
            call_id="c1",
            name="x",
            status="error",
            output=None,
            duration_ms=10,
        )


def test_sandbox_runtime_error_carries_error_class():
    """D3 — Timeout / OOM / Crash records error_class so the gate doesn't
    advance best_ever_score on sandbox failures."""
    event = ToolCallResult(
        **_base_fields(),
        type="tool_call_result",
        call_id="c1",
        name="run_pipeline",
        status="error",
        output=None,
        duration_ms=600_000,
        error="Sandbox timeout exceeded 600s",
        error_class="Timeout",
    )
    assert event.error_class == "Timeout"


def test_logical_error_omits_error_class():
    """Tool-internal logical error: error is set, error_class is None.
    Distinguishes from sandbox-runtime kills (D3)."""
    event = ToolCallResult(
        **_base_fields(),
        type="tool_call_result",
        call_id="c1",
        name="lookup_supplier",
        status="error",
        output=None,
        duration_ms=42,
        error="Supplier not found in registry",
    )
    assert event.error == "Supplier not found in registry"
    assert event.error_class is None


# ---------------------------------------------------------------------------
# Round-trip — every variant constructs cleanly
# ---------------------------------------------------------------------------


def test_all_variants_construct_cleanly():
    base = _base_fields()
    variants = [
        ContentDelta(**base, type="content_delta", text="hi", model="claude-opus-4-7"),
        ReasoningDelta(**base, type="reasoning_delta", text="thinking", model="claude-opus-4-7"),
        ToolCallStart(**base, type="tool_call_start", call_id="c1", name="x", args={}),
        ToolCallResult(
            **base,
            type="tool_call_result",
            call_id="c1",
            name="x",
            status="ok",
            output={"k": "v"},
            duration_ms=1,
        ),
        SkillLoaded(**base, type="skill_loaded", skill_id="s1", version_seq=1),
        Citation(**base, type="citation", ref=1, source="src", quote="q"),
        MonitorSignal(
            **base,
            type="monitor_signal",
            monitor="loop_detection",
            severity="warn",
        ),
    ]
    assert len(variants) == 7
    # round-trip via dump+adapter
    for v in variants:
        roundtripped = AgentEventAdapter.validate_python(v.model_dump(mode="python"))
        assert type(roundtripped) is type(v)


# ---------------------------------------------------------------------------
# Boundary / constraint tests added by review
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_ms", [-1, -100])
def test_tool_call_result_rejects_negative_duration_ms(bad_ms: int):
    with pytest.raises(ValidationError):
        ToolCallResult(
            **_base_fields(), type="tool_call_result",
            call_id="c", name="x", status="ok", output=None, duration_ms=bad_ms,
        )


def test_citation_rejects_zero_ref():
    with pytest.raises(ValidationError):
        Citation(**_base_fields(), type="citation", ref=0, source="s", quote="q")


def test_skill_loaded_rejects_zero_version_seq():
    with pytest.raises(ValidationError):
        SkillLoaded(**_base_fields(), type="skill_loaded", skill_id="s", version_seq=0)
