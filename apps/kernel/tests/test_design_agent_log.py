"""Schema tests for `design_agent.log` (PLAN 9.1.4).

DB-free. Pins the contract that the persistence helper + audit-chain
mirror rely on:

  * `DesignAgentLog` is frozen, extra-forbid; round-trips through JSON
  * `DesignAgentLogEntry` accepts every `DiscoveryQuestionKind` variant
  * Skipped entries (`answer is None`) are first-class
  * The matching audit-kind values exist on `AuditKind`
"""

from __future__ import annotations

import pytest
from ownevo_kernel.design_agent.ambiguity import AmbiguityFinding, AmbiguityReport
from ownevo_kernel.design_agent.log import (
    DESIGN_AGENT_ACTOR,
    DesignAgentLog,
    DesignAgentLogEntry,
    load_design_agent_log,
)
from ownevo_kernel.design_agent.prompts import DISCOVERY_QUESTION_KINDS
from ownevo_kernel.types import AuditKind
from pydantic import ValidationError


def test_audit_kind_includes_design_agent_variants() -> None:
    """The two new audit-kind values land on the Python enum and read
    back via `AuditKind(value)` from a payload returned by the API."""
    assert AuditKind.DESIGN_AGENT_NEGOTIATION == "design-agent-negotiation"
    assert AuditKind.DESIGN_AGENT_AMBIGUITY == "design-agent-ambiguity"
    assert AuditKind("design-agent-negotiation") is AuditKind.DESIGN_AGENT_NEGOTIATION
    assert AuditKind("design-agent-ambiguity") is AuditKind.DESIGN_AGENT_AMBIGUITY


def test_design_agent_actor_is_a_stable_constant() -> None:
    """The actor string is queried from the audit tab — pin it so renames
    surface as test failures rather than silent UI drift."""
    assert DESIGN_AGENT_ACTOR == "design-agent"


@pytest.mark.parametrize("kind", DISCOVERY_QUESTION_KINDS)
def test_log_entry_accepts_every_discovery_kind(kind: str) -> None:
    entry = DesignAgentLogEntry(
        question_index=0,
        kind=kind,
        question="Q?",
        answer="A.",
    )
    assert entry.kind == kind


def test_log_entry_accepts_null_answer_for_skips() -> None:
    entry = DesignAgentLogEntry(
        question_index=2,
        kind="metric",
        question="Q?",
        answer=None,
    )
    assert entry.answer is None


def test_log_entry_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        DesignAgentLogEntry(
            question_index=0,
            kind="bogus-kind",  # type: ignore[arg-type]
            question="Q?",
        )


def test_log_entry_rejects_negative_question_index() -> None:
    with pytest.raises(ValidationError):
        DesignAgentLogEntry(
            question_index=-1,
            kind="metric",
            question="Q?",
            answer="A.",
        )


def test_design_agent_log_defaults_to_empty() -> None:
    log = DesignAgentLog()
    assert log.discovery_transcript == ()
    assert log.ambiguity_report is None


def test_design_agent_log_round_trips_through_json() -> None:
    log = DesignAgentLog(
        discovery_transcript=(
            DesignAgentLogEntry(
                question_index=0,
                kind="metric",
                question="Recall vs. precision?",
                answer="Recall",
            ),
            DesignAgentLogEntry(
                question_index=1,
                kind="ambiguity",
                question="Which baseline?",
                answer=None,
            ),
        ),
        ambiguity_report=AmbiguityReport(
            workflow_spec_id="test-spec",
            findings=(
                AmbiguityFinding(
                    kind="conflict",
                    severity="high",
                    location="description",
                    summary="x",
                    suggested_question="y?",
                ),
            ),
        ),
    )
    rt = DesignAgentLog.model_validate_json(log.model_dump_json())
    assert rt == log


def test_design_agent_log_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DesignAgentLog.model_validate(
            {"discovery_transcript": [], "rogue_field": "no"}
        )


def test_load_design_agent_log_handles_string_input() -> None:
    """asyncpg often returns JSONB as a raw string."""
    log = DesignAgentLog()
    parsed = load_design_agent_log(log.model_dump_json())
    assert parsed == log


def test_load_design_agent_log_handles_dict_input() -> None:
    log = DesignAgentLog(
        discovery_transcript=(
            DesignAgentLogEntry(question_index=0, kind="metric", question="Q?"),
        ),
    )
    parsed = load_design_agent_log(log.model_dump(mode="json"))
    assert parsed == log


def test_load_design_agent_log_handles_none() -> None:
    assert load_design_agent_log(None) is None


def test_load_design_agent_log_handles_bytes() -> None:
    log = DesignAgentLog()
    parsed = load_design_agent_log(log.model_dump_json().encode("utf-8"))
    assert parsed == log


def test_log_entry_rejects_oversized_question() -> None:
    with pytest.raises(ValidationError):
        DesignAgentLogEntry(question_index=0, kind="metric", question="x" * 4097)


def test_log_entry_rejects_oversized_answer() -> None:
    with pytest.raises(ValidationError):
        DesignAgentLogEntry(
            question_index=0, kind="metric", question="Q?", answer="a" * 4097
        )


def test_design_agent_log_rejects_transcript_over_max_items() -> None:
    entries = tuple(
        DesignAgentLogEntry(question_index=i, kind="metric", question="Q?")
        for i in range(21)
    )
    with pytest.raises(ValidationError):
        DesignAgentLog(discovery_transcript=entries)


def test_ambiguity_report_high_severity_count_in_json() -> None:
    """high_severity_count must appear in model_dump_json and be correct."""
    report = AmbiguityReport(
        workflow_spec_id="test",
        findings=(
            AmbiguityFinding(
                kind="conflict",
                severity="high",
                location="description",
                summary="x",
                suggested_question="y?",
            ),
            AmbiguityFinding(
                kind="conflict",
                severity="medium",
                location="description",
                summary="z",
                suggested_question="w?",
            ),
        ),
    )
    data = report.model_dump()
    assert "high_severity_count" in data
    assert data["high_severity_count"] == 1


def test_ambiguity_report_high_severity_count_round_trips() -> None:
    """Round-trip through JSON must preserve high_severity_count (model_validator fix)."""
    report = AmbiguityReport(
        workflow_spec_id="rt-test",
        findings=(
            AmbiguityFinding(
                kind="inferred-artifact",
                severity="high",
                location="reviewer",
                summary="x",
                suggested_question="y?",
            ),
        ),
    )
    rt = AmbiguityReport.model_validate_json(report.model_dump_json())
    assert rt.high_severity_count == 1
    assert rt == report
