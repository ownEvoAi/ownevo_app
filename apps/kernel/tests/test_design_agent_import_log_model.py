"""Pure-model tests for the import-discovery log (no DB required).

Covers `DesignAgentImportLog` / `ReverseDiscoveryRecord` validation,
`is_empty()`, and `load_design_agent_import_log` decoding from the JSONB
column's str / bytes / dict / None shapes.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.design_agent.import_log import (
    DesignAgentImportLog,
    ReverseDiscoveryRecord,
    load_design_agent_import_log,
)
from ownevo_kernel.design_agent.log import DesignAgentLogEntry
from pydantic import ValidationError


def _record() -> ReverseDiscoveryRecord:
    return ReverseDiscoveryRecord(
        inferred_summary="Reconciles invoices against purchase orders.",
        basis="traces",
        source="llm",
        decision="confirmed",
        final_definition="Reconciles invoices against purchase orders.",
    )


def test_is_empty_true_for_default() -> None:
    assert DesignAgentImportLog().is_empty() is True


def test_is_empty_false_with_reverse_discovery() -> None:
    assert DesignAgentImportLog(reverse_discovery=_record()).is_empty() is False


def test_is_empty_false_with_transcript() -> None:
    log = DesignAgentImportLog(
        discovery_transcript=(
            DesignAgentLogEntry(
                question_index=0, kind="metric", question="Q?", answer="A."
            ),
        )
    )
    assert log.is_empty() is False


def test_decision_literal_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ReverseDiscoveryRecord(
            inferred_summary="x",
            basis="traces",
            source="llm",
            decision="maybe",  # type: ignore[arg-type]
        )


def test_basis_literal_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ReverseDiscoveryRecord(
            inferred_summary="x",
            basis="vibes",  # type: ignore[arg-type]
            source="llm",
            decision="confirmed",
        )


def test_skipped_decision_allows_null_final_definition() -> None:
    rec = ReverseDiscoveryRecord(
        inferred_summary="x",
        basis="traces",
        source="fallback",
        decision="skipped",
    )
    assert rec.final_definition is None


def test_load_roundtrip_from_str_bytes_and_dict() -> None:
    log = DesignAgentImportLog(reverse_discovery=_record())
    as_json = log.model_dump_json()

    assert load_design_agent_import_log(as_json) == log
    assert load_design_agent_import_log(as_json.encode("utf-8")) == log
    assert load_design_agent_import_log(log.model_dump()) == log
    assert load_design_agent_import_log(None) is None
