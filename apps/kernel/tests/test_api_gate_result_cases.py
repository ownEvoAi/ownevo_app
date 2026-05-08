"""Unit tests for the W5.1 `_gate_result_cases_from_audit` helper.

The helper reconstructs the per-eval-case breakdown shown in the
proposal-detail sidebar from the gate audit payloads — no DB and no
schema change required. These tests exercise the four shapes the API
emits without a Postgres dependency:

  * gate-passed (no regressions, some newly admitted)
  * gate-failed (regressions present)
  * gate-run-started but no completion (race window)
  * neither audit kind (hand-seeded proposal — returns None)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ownevo_kernel.api.models import AuditEntry
from ownevo_kernel.api.routes.proposals import _gate_result_cases_from_audit


def _audit(
    kind: str,
    payload: dict,
    *,
    seq: int = 1,
) -> AuditEntry:
    return AuditEntry(
        id=uuid4(),
        seq=seq,
        kind=kind,
        actor="test:gate",
        payload=payload,
        created_at=datetime.now(UTC),
    )


def test_returns_none_when_no_gate_audits():
    cases = _gate_result_cases_from_audit([])
    assert cases is None


def test_returns_none_when_only_unrelated_audits():
    cases = _gate_result_cases_from_audit(
        [_audit("approval-recorded", {"decision": "approve"})]
    )
    assert cases is None


def test_pass_path_no_regressions():
    started = _audit(
        "gate-run-started",
        {"prior_eval_task_ids": ["case-A", "case-B", "case-C"]},
        seq=1,
    )
    completed = _audit(
        "gate-run-completed",
        {
            "decision": "pass",
            "failed_prior_task_ids": [],
            "promotable_task_ids": ["case-D", "case-E"],
        },
        seq=2,
    )

    cases = _gate_result_cases_from_audit([started, completed])
    assert cases is not None
    assert cases.passed == ["case-A", "case-B", "case-C"]
    assert cases.regressed == []
    assert cases.newly_admitted == ["case-D", "case-E"]
    assert cases.unknown is False


def test_fail_path_regressions_subtract_from_passed():
    started = _audit(
        "gate-run-started",
        {"prior_eval_task_ids": ["case-A", "case-B", "case-C"]},
        seq=1,
    )
    completed = _audit(
        "gate-run-completed",
        {
            "decision": "fail-regression",
            "failed_prior_task_ids": ["case-B"],
            "promotable_task_ids": [],
        },
        seq=2,
    )

    cases = _gate_result_cases_from_audit([started, completed])
    assert cases is not None
    assert cases.passed == ["case-A", "case-C"]
    assert cases.regressed == ["case-B"]
    assert cases.newly_admitted == []
    assert cases.unknown is False


def test_started_only_marks_unknown_true():
    started = _audit(
        "gate-run-started",
        {"prior_eval_task_ids": ["case-A", "case-B"]},
        seq=1,
    )

    cases = _gate_result_cases_from_audit([started])
    assert cases is not None
    assert cases.passed == ["case-A", "case-B"]
    assert cases.regressed == []
    assert cases.newly_admitted == []
    assert cases.unknown is True


def test_handles_missing_prior_list():
    """A completed-only audit (e.g. `gate-run-started` fell outside the
    LIMIT 500 window) must mark `unknown=True` — we can't accurately
    report the prior-case breakdown without the started entry."""
    completed = _audit(
        "gate-run-completed",
        {
            "decision": "pass",
            "failed_prior_task_ids": [],
            "promotable_task_ids": ["case-X"],
        },
        seq=1,
    )

    cases = _gate_result_cases_from_audit([completed])
    assert cases is not None
    assert cases.passed == []
    assert cases.regressed == []
    assert cases.newly_admitted == ["case-X"]
    assert cases.unknown is True


def test_coerces_non_string_task_ids_to_str():
    """Defensive: if a payload sneaks in a non-string id (e.g. int from
    a buggy producer), the helper should not crash the API render."""
    started = _audit(
        "gate-run-started",
        {"prior_eval_task_ids": [42, "case-A"]},
        seq=1,
    )
    completed = _audit(
        "gate-run-completed",
        {
            "decision": "pass",
            "failed_prior_task_ids": [],
            "promotable_task_ids": [],
        },
        seq=2,
    )

    cases = _gate_result_cases_from_audit([started, completed])
    assert cases is not None
    assert cases.passed == ["42", "case-A"]
    assert cases.unknown is False
