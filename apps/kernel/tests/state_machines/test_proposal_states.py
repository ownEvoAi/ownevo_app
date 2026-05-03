"""State machine tests for proposal transitions.

Covers all rows in docs/STATE_MACHINES.md § Proposal transition table:
  - Unit tests: each legal transition is reachable
  - Negative tests: illegal shortcuts are rejected
  - Audit-coupling: each transition produces the correct AuditKind
  - Autonomous mode: gate-passed → approved-awaiting-deploy without human approval row

Implementations (gate runner, approval service) land in W2. These tests
define the contract the implementations must satisfy.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.types import (
    ApproverType,
    AuditKind,
    ProposalState,
    WorkflowMode,
)

# ---------------------------------------------------------------------------
# Helpers — lightweight state machine simulator (no DB; tests the logic only)
# ---------------------------------------------------------------------------


class _StateMachineError(Exception):
    pass


_LEGAL_TRANSITIONS: dict[ProposalState, set[ProposalState]] = {
    ProposalState.PENDING: {ProposalState.IN_GATE},
    ProposalState.IN_GATE: {
        ProposalState.GATE_FAILED,
        ProposalState.GATE_PASSED,
        ProposalState.REJECTED,
    },
    ProposalState.GATE_FAILED: {ProposalState.PENDING},
    ProposalState.GATE_PASSED: {
        ProposalState.APPROVED_AWAITING_DEPLOY,
        ProposalState.REJECTED,
    },
    ProposalState.APPROVED_AWAITING_DEPLOY: {ProposalState.DEPLOYED},
    ProposalState.DEPLOYED: {ProposalState.ROLLED_BACK},
    ProposalState.REJECTED: set(),
    ProposalState.ROLLED_BACK: set(),
}

_TRANSITION_AUDIT_KIND: dict[tuple[ProposalState, ProposalState], AuditKind] = {
    (ProposalState.PENDING, ProposalState.IN_GATE): AuditKind.GATE_RUN_STARTED,
    (ProposalState.IN_GATE, ProposalState.GATE_FAILED): AuditKind.GATE_RUN_COMPLETED,
    (ProposalState.IN_GATE, ProposalState.GATE_PASSED): AuditKind.GATE_RUN_COMPLETED,
    (ProposalState.IN_GATE, ProposalState.REJECTED): AuditKind.PROPOSAL_REJECTED,
    (ProposalState.GATE_FAILED, ProposalState.PENDING): AuditKind.PROPOSAL_CREATED,
    (ProposalState.GATE_PASSED, ProposalState.APPROVED_AWAITING_DEPLOY):
        AuditKind.PROPOSAL_APPROVED,
    (ProposalState.GATE_PASSED, ProposalState.REJECTED): AuditKind.PROPOSAL_REJECTED,
    (ProposalState.APPROVED_AWAITING_DEPLOY, ProposalState.DEPLOYED): AuditKind.PROPOSAL_DEPLOYED,
    (ProposalState.DEPLOYED, ProposalState.ROLLED_BACK): AuditKind.PROPOSAL_ROLLED_BACK,
}


def transition(from_state: ProposalState, to_state: ProposalState) -> AuditKind:
    """Apply a transition and return the expected AuditKind."""
    if to_state not in _LEGAL_TRANSITIONS.get(from_state, set()):
        raise _StateMachineError(f"Illegal: {from_state} → {to_state}")
    return _TRANSITION_AUDIT_KIND[(from_state, to_state)]


# ---------------------------------------------------------------------------
# Unit tests — every legal transition
# ---------------------------------------------------------------------------


def test_pending_to_in_gate():
    kind = transition(ProposalState.PENDING, ProposalState.IN_GATE)
    assert kind == AuditKind.GATE_RUN_STARTED


def test_in_gate_to_gate_failed():
    kind = transition(ProposalState.IN_GATE, ProposalState.GATE_FAILED)
    assert kind == AuditKind.GATE_RUN_COMPLETED


def test_in_gate_to_gate_passed():
    kind = transition(ProposalState.IN_GATE, ProposalState.GATE_PASSED)
    assert kind == AuditKind.GATE_RUN_COMPLETED


def test_in_gate_to_rejected():
    kind = transition(ProposalState.IN_GATE, ProposalState.REJECTED)
    assert kind == AuditKind.PROPOSAL_REJECTED


def test_gate_failed_back_to_pending():
    kind = transition(ProposalState.GATE_FAILED, ProposalState.PENDING)
    assert kind == AuditKind.PROPOSAL_CREATED


def test_gate_passed_to_approved_awaiting_deploy():
    kind = transition(ProposalState.GATE_PASSED, ProposalState.APPROVED_AWAITING_DEPLOY)
    assert kind == AuditKind.PROPOSAL_APPROVED


def test_gate_passed_to_rejected():
    kind = transition(ProposalState.GATE_PASSED, ProposalState.REJECTED)
    assert kind == AuditKind.PROPOSAL_REJECTED


def test_approved_awaiting_deploy_to_deployed():
    kind = transition(ProposalState.APPROVED_AWAITING_DEPLOY, ProposalState.DEPLOYED)
    assert kind == AuditKind.PROPOSAL_DEPLOYED


def test_deployed_to_rolled_back():
    kind = transition(ProposalState.DEPLOYED, ProposalState.ROLLED_BACK)
    assert kind == AuditKind.PROPOSAL_ROLLED_BACK


# ---------------------------------------------------------------------------
# Negative tests — illegal shortcuts must raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("illegal_to", [
    ProposalState.APPROVED_AWAITING_DEPLOY,
    ProposalState.DEPLOYED,
    ProposalState.ROLLED_BACK,
    ProposalState.GATE_PASSED,
    ProposalState.GATE_FAILED,
])
def test_pending_cannot_skip_in_gate(illegal_to: ProposalState):
    with pytest.raises(_StateMachineError):
        transition(ProposalState.PENDING, illegal_to)


def test_rejected_is_terminal():
    for any_state in ProposalState:
        if any_state != ProposalState.REJECTED:
            with pytest.raises(_StateMachineError):
                transition(ProposalState.REJECTED, any_state)


def test_rolled_back_is_terminal():
    for any_state in ProposalState:
        if any_state != ProposalState.ROLLED_BACK:
            with pytest.raises(_StateMachineError):
                transition(ProposalState.ROLLED_BACK, any_state)


def test_gate_passed_cannot_go_back_to_pending():
    with pytest.raises(_StateMachineError):
        transition(ProposalState.GATE_PASSED, ProposalState.PENDING)


def test_deployed_cannot_go_back_to_approved():
    with pytest.raises(_StateMachineError):
        transition(ProposalState.DEPLOYED, ProposalState.APPROVED_AWAITING_DEPLOY)


# ---------------------------------------------------------------------------
# Audit-coupling — every transition maps to exactly one AuditKind
# ---------------------------------------------------------------------------


def test_all_legal_transitions_have_audit_kind():
    """No legal transition is missing from the audit-kind map."""
    for from_state, to_states in _LEGAL_TRANSITIONS.items():
        for to_state in to_states:
            assert (from_state, to_state) in _TRANSITION_AUDIT_KIND, (
                f"Missing AuditKind for {from_state} → {to_state}"
            )


# ---------------------------------------------------------------------------
# Autonomous mode — gate-passed → approved-awaiting-deploy without human row
# ---------------------------------------------------------------------------


def test_autonomous_mode_approver_type_is_autonomous():
    """In autonomous mode the approval is recorded with approver_type=AUTONOMOUS."""
    assert ApproverType.AUTONOMOUS == "autonomous"


def test_autonomous_workflow_mode_value():
    assert WorkflowMode.AUTONOMOUS == "autonomous"
    assert WorkflowMode.GATED == "gated"


def test_autonomous_gate_passed_transition_is_legal():
    """gate-passed → approved-awaiting-deploy must be legal for autonomous mode."""
    kind = transition(ProposalState.GATE_PASSED, ProposalState.APPROVED_AWAITING_DEPLOY)
    assert kind == AuditKind.PROPOSAL_APPROVED
