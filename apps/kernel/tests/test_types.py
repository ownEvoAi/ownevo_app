"""Tests for kernel domain types.

Covers W1.2 deliverable + part of W1 spike's go/no-go criterion: the
domain types (mirroring SCHEMA.md / 0001_substrate.sql) construct cleanly
and the D6 `regression_gate` ProposalAction extension is recognized.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from ownevo_kernel.evolution import Curator, Proposer, Reflector, Tracker
from ownevo_kernel.types import (
    Approval,
    AuditEntry,
    AuditKind,
    EvalCase,
    FailureCluster,
    Iteration,
    IterationState,
    Learning,
    MetaEvalResult,
    Proposal,
    ProposalAction,
    ProposalState,
    ProvenanceKind,
    SandboxErrorClass,
    Skill,
    SkillKind,
    SkillVersion,
    Trace,
    Workflow,
)
from pydantic import ValidationError


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# D6 — regression_gate ProposalAction extension
# ---------------------------------------------------------------------------


def test_proposal_action_accepts_existing_4_action_types():
    """Backward compatibility with the core/ ProposalAction shape."""
    for action_type in (
        "workflow_update",
        "tool_priority",
        "prompt_refinement",
        "config_update",
    ):
        action = ProposalAction(
            action_type=action_type,
            target="some_target",
            value="some_value",
            reason="test",
        )
        assert action.action_type == action_type


def test_proposal_action_accepts_regression_gate_d6():
    """D6 — gate outcomes flow through the same proposal pipeline as
    skill mutations. New action_type added without breaking the union."""
    action = ProposalAction(
        action_type="regression_gate",
        target="iteration:abc",
        value={"admitted": True, "val_score": 0.78},
        reason="passed all 47 prior eval cases, advanced best-ever from 0.74",
    )
    assert action.action_type == "regression_gate"


def test_proposal_action_rejects_unknown_type():
    with pytest.raises(ValidationError):
        ProposalAction(
            action_type="not_a_real_action",
            target="x",
            value=None,
        )


# ---------------------------------------------------------------------------
# Domain types construct from valid input (mirrors 0001_substrate.sql)
# ---------------------------------------------------------------------------


def test_workflow_constructs_with_required_fields():
    wf = Workflow(
        id="supply-chain-demand-forecast",
        description="Forecast weekly demand at SKU-store level...",
        spec={"tools": [], "ui": {}},
        created_at=_now(),
    )
    assert wf.id == "supply-chain-demand-forecast"
    assert wf.meta_eval_score is None  # not yet evaluated


def test_skill_and_version_construct():
    skill_id = "m5-feature-engineer"
    skill = Skill(
        id=skill_id,
        kind=SkillKind.PYTHON,
        capability_tags=["forecasting", "feature-engineering"],
        created_at=_now(),
    )
    version = SkillVersion(
        id=uuid4(),
        skill_id=skill_id,
        version_seq=1,
        content="def engineer_features(df): ...",
        retention_block={"remembers": [], "refetches": [], "stateless": True},
        created_at=_now(),
        created_by="agent:claude-opus-4-7",
    )
    assert skill.kind == SkillKind.PYTHON
    assert version.version_seq == 1


def test_iteration_with_sandbox_error_d3():
    """D3 — sandbox runtime failure recorded with error_class."""
    it = Iteration(
        id=uuid4(),
        workflow_id="w1",
        iteration_index=12,
        state=IterationState.SANDBOX_ERROR,
        sandbox_error_class=SandboxErrorClass.OOM,
        started_at=_now(),
        ended_at=_now(),
    )
    assert it.state == IterationState.SANDBOX_ERROR
    assert it.sandbox_error_class == SandboxErrorClass.OOM


def test_proposal_starts_pending():
    p = Proposal(
        id=uuid4(),
        iteration_id=uuid4(),
        skill_id="m5-feature-engineer",
        proposed_content="...",
        plain_language_summary="Add winter-PNW seasonal feature",
        created_at=_now(),
        state_updated_at=_now(),
    )
    assert p.state == ProposalState.PENDING


def test_eval_case_provenance_includes_retention_violation_and_rejected_feedback():
    """Provenance taxonomy from the eng review: cluster-derived, nl-gen,
    retention-violation, rejected-feedback all map to test cases."""
    ec = EvalCase(
        id=uuid4(),
        provenance=ProvenanceKind.RETENTION_VIOLATION,
        input={"source_id": "supplier_doc:lead_time", "T_plus": "25h"},
        expected_behavior={"trace_must_contain": "re-fetch tool call"},
        created_at=_now(),
    )
    assert ec.provenance == ProvenanceKind.RETENTION_VIOLATION


def test_failure_cluster_with_label_eval_score_d4():
    """D4 — every cluster carries a judge-vs-human agreement score."""
    fc = FailureCluster(
        id=uuid4(),
        label="winter footwear in Pacific NW Q4",
        label_eval_score=0.85,
        severity="medium",
        cluster_size=12,
        quality_score=0.72,
        created_at=_now(),
    )
    assert fc.label_eval_score == 0.85


def test_failure_cluster_round_trips_centroid():
    """`SELECT *` from failure_clusters must round-trip under extra='forbid'.
    centroid is pgvector vector(384) in SQL; here it's list[float] | None."""
    centroid = [0.01] * 384
    fc = FailureCluster(
        id=uuid4(),
        label="x",
        severity="low",
        centroid=centroid,
        cluster_size=3,
        created_at=_now(),
    )
    assert fc.centroid is not None
    assert len(fc.centroid) == 384

    # Default to None when the SELECT didn't include centroid.
    fc_no_centroid = FailureCluster(
        id=uuid4(),
        label="x",
        severity="low",
        cluster_size=3,
        created_at=_now(),
    )
    assert fc_no_centroid.centroid is None


def test_audit_entry_kind_includes_d2_state_transitions():
    """Every state-machine transition has an audit-kind mapping per
    docs/STATE_MACHINES.md."""
    required_kinds = {
        AuditKind.PROPOSAL_CREATED,
        AuditKind.GATE_RUN_STARTED,
        AuditKind.GATE_RUN_COMPLETED,
        AuditKind.PROPOSAL_APPROVED,
        AuditKind.PROPOSAL_REJECTED,
        AuditKind.PROPOSAL_DEPLOYED,
        AuditKind.PROPOSAL_ROLLED_BACK,
    }
    for kind in required_kinds:
        ae = AuditEntry(
            id=uuid4(),
            seq=1,
            kind=kind,
            payload={},
            actor="test",
            created_at=_now(),
        )
        assert ae.kind == kind


def test_meta_eval_result_d7():
    """D7 — meta-eval coverage score in [0, 1]."""
    me = MetaEvalResult(
        id=uuid4(),
        workflow_id="w1",
        description="Forecast weekly demand...",
        coverage_score=0.92,
        per_dimension={
            "sim_completeness": 0.91,
            "eval_coverage": 0.85,
            "metric_alignment": 1.0,
        },
        judge_model="claude-opus-4-7",
        passed_threshold=True,
        created_at=_now(),
    )
    assert me.coverage_score == 0.92
    assert me.passed_threshold


@pytest.mark.parametrize("bad_score", [1.5, -0.1])
def test_meta_eval_result_rejects_out_of_range_score(bad_score: float):
    with pytest.raises(ValidationError):
        MetaEvalResult(
            id=uuid4(),
            workflow_id="w1",
            description="...",
            coverage_score=bad_score,  # outside [0.0, 1.0]
            per_dimension={},
            judge_model="x",
            passed_threshold=False,
            created_at=_now(),
        )


def test_approval_with_become_eval_case():
    """Reject + comment flow: approval references the EvalCase row
    that was generated from the comment."""
    ap = Approval(
        id=uuid4(),
        proposal_id=uuid4(),
        decided_by="human:founder",
        decision="reject",
        comment="Don't weight last quarter's stock-outs so heavily",
        became_eval_case_id=uuid4(),
        decided_at=_now(),
    )
    assert ap.decision == "reject"
    assert ap.became_eval_case_id is not None


def test_learning_kinds_match_loop_stuck_alert_consumer():
    """W2.4a loop-stuck alert reads from this enum's surface."""
    for kind in ("hypothesis", "observation", "request-to-human", "failure-note"):
        ln = Learning(
            id=uuid4(),
            kind=kind,
            content="x",
            created_at=_now(),
        )
        assert ln.kind == kind


def test_trace_holds_event_array():
    """Trace.events is the AgentEvent[] from packages/trace-format/. We
    store as list[dict] here; typed at the trace-pipeline boundary."""
    tr = Trace(
        id=uuid4(),
        workflow_id="w1",
        events=[{"type": "skill_loaded", "skill_id": "s1"}],
        started_at=_now(),
    )
    assert len(tr.events) == 1


# ---------------------------------------------------------------------------
# Greenfield evolution Protocol scaffolding (the spike's structural carry-over)
# ---------------------------------------------------------------------------


def test_evolution_protocols_exposed():
    """Spike result: the 4-stage pattern (Tracker / Reflector / Curator /
    Proposer) is preserved as Protocol scaffolding. Implementations land
    in W2 once gate + clustering pipelines exist."""
    assert Tracker is not None
    assert Reflector is not None
    assert Curator is not None
    assert Proposer is not None
