"""DB-backed integration tests for the approval service (W2.5).

Pins every transition rule from `docs/STATE_MACHINES.md § Proposal`:

  * `gate-passed → approved-awaiting-deploy` via `approve_proposal`
  * `gate-passed → rejected` via `reject_proposal`
  * Reject + comment → eval_case (provenance=rejected-feedback) +
    `approvals.became_eval_case_id` link
  * Illegal start states (`in-gate`, `gate-failed`, terminal) → ApprovalStateError
  * Unknown proposal id → ProposalNotFoundError
  * Audit entries written with the correct kind + actor + related_id

Uses the `db` fixture from conftest.py — fresh DB per test, migration
applied, asyncpg connection.
"""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest
from ownevo_kernel.approvals import (
    ApprovalStateError,
    ProposalNotFoundError,
    approve_proposal,
    reject_proposal,
)
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.types import (
    ApproverType,
    AuditKind,
    ProposalState,
    ProvenanceKind,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Fixture helpers — seed the dependency graph (workflow → skill → iteration → proposal)
# ---------------------------------------------------------------------------


async def _seed_proposal_in_state(
    conn: asyncpg.Connection,
    *,
    state: ProposalState = ProposalState.GATE_PASSED,
    workflow_id: str = "wf-test-approvals",
    skill_id: str = "test.skill.approval",
):
    """Returns (proposal_id, iteration_id) — seeded through every FK."""
    await conn.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, 'test', '{}'::jsonb) "
        "ON CONFLICT DO NOTHING",
        workflow_id,
    )
    await conn.execute(
        "INSERT INTO skills (id, kind) VALUES ($1, 'python'::skill_kind) "
        "ON CONFLICT DO NOTHING",
        skill_id,
    )
    iteration_id = await conn.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, val_score,
                                best_ever_score_after, ended_at)
        VALUES ($1, 0, 'gate-pass'::iteration_state, 0.95, 0.95, now())
        RETURNING id
        """,
        workflow_id,
    )
    proposal_id = await conn.fetchval(
        """
        INSERT INTO proposals (
            iteration_id, skill_id, proposed_content, plain_language_summary,
            state, eval_score, eval_rationale
        )
        VALUES ($1, $2, 'pretend skill body', 'Test proposal', $3::proposal_state,
                0.95, 'Gate passed: val_score 0.95 (initial baseline)')
        RETURNING id
        """,
        iteration_id,
        skill_id,
        state.value,
    )
    return proposal_id, iteration_id


# ---------------------------------------------------------------------------
# Approve path
# ---------------------------------------------------------------------------


async def test_approve_transitions_to_awaiting_deploy(db: asyncpg.Connection):
    proposal_id, _ = await _seed_proposal_in_state(db)

    approval = await approve_proposal(
        db,
        proposal_id=proposal_id,
        decided_by="human:jit",
        comment="LGTM",
    )

    # Approval row populated with comment, no became_eval_case_id
    # (approvals don't auto-seed eval cases — only rejections do).
    assert approval.proposal_id == proposal_id
    assert approval.decision == "approve"
    assert approval.approver_type == ApproverType.HUMAN
    assert approval.decided_by == "human:jit"
    assert approval.comment == "LGTM"
    assert approval.became_eval_case_id is None

    # Proposal state advanced.
    new_state = await db.fetchval(
        "SELECT state::text FROM proposals WHERE id = $1", proposal_id,
    )
    assert new_state == ProposalState.APPROVED_AWAITING_DEPLOY.value

    # Audit entry — proposal-approved, related_id=proposal_id, actor=decided_by.
    audit_row = await db.fetchrow(
        "SELECT kind::text AS kind, actor, payload, related_id "
        "FROM audit_entries WHERE related_id = $1",
        proposal_id,
    )
    assert audit_row["kind"] == AuditKind.PROPOSAL_APPROVED.value
    assert audit_row["actor"] == "human:jit"
    assert audit_row["related_id"] == proposal_id


async def test_approve_with_autonomous_approver_type(db: asyncpg.Connection):
    """workflow.mode='autonomous' should record approver_type='autonomous'
    and write the same audit kind without a human user string."""
    proposal_id, _ = await _seed_proposal_in_state(db)

    approval = await approve_proposal(
        db,
        proposal_id=proposal_id,
        decided_by="autonomous",
        approver_type=ApproverType.AUTONOMOUS,
    )
    assert approval.approver_type == ApproverType.AUTONOMOUS
    assert approval.decided_by == "autonomous"
    assert approval.comment is None


# ---------------------------------------------------------------------------
# Reject path
# ---------------------------------------------------------------------------


async def test_reject_without_comment_no_eval_case(db: asyncpg.Connection):
    proposal_id, _ = await _seed_proposal_in_state(db)

    approval = await reject_proposal(
        db,
        proposal_id=proposal_id,
        decided_by="human:jit",
    )

    assert approval.decision == "reject"
    assert approval.became_eval_case_id is None  # no comment → no eval case

    new_state = await db.fetchval(
        "SELECT state::text FROM proposals WHERE id = $1", proposal_id,
    )
    assert new_state == ProposalState.REJECTED.value


async def test_reject_with_comment_creates_eval_case(db: asyncpg.Connection):
    proposal_id, _ = await _seed_proposal_in_state(
        db, workflow_id="wf-reject-comment",
    )

    approval = await reject_proposal(
        db,
        proposal_id=proposal_id,
        decided_by="human:jit",
        comment="This handler still misses the weekend OT cap on PT contractors.",
    )

    # became_eval_case_id linked
    assert approval.became_eval_case_id is not None

    # Eval case created with provenance=rejected-feedback
    case_row = await db.fetchrow(
        "SELECT provenance::text AS provenance, workflow_id, expected_behavior, input "
        "FROM eval_cases WHERE id = $1",
        approval.became_eval_case_id,
    )
    assert case_row["provenance"] == ProvenanceKind.REJECTED_FEEDBACK.value
    assert case_row["workflow_id"] == "wf-reject-comment"
    # JSONB returned as str by asyncpg — parse to verify shape
    import json
    expected = json.loads(case_row["expected_behavior"]) \
        if isinstance(case_row["expected_behavior"], str) else case_row["expected_behavior"]
    assert "weekend OT cap" in expected["note"]
    assert expected["source"] == "human-rejection-comment"

    # Audit payload references the eval case
    payload_raw = await db.fetchval(
        "SELECT payload FROM audit_entries WHERE related_id = $1",
        proposal_id,
    )
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    assert payload["became_eval_case_id"] == str(approval.became_eval_case_id)
    assert payload["decision"] == "reject"


async def test_reject_with_whitespace_only_comment_treated_as_no_comment(
    db: asyncpg.Connection,
):
    proposal_id, _ = await _seed_proposal_in_state(db)

    approval = await reject_proposal(
        db,
        proposal_id=proposal_id,
        decided_by="human:jit",
        comment="   \t\n  ",
    )

    assert approval.comment is None
    assert approval.became_eval_case_id is None


# ---------------------------------------------------------------------------
# State validation — illegal start states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "illegal_state",
    [
        ProposalState.IN_GATE,
        ProposalState.GATE_FAILED,
        ProposalState.REJECTED,
        ProposalState.APPROVED_AWAITING_DEPLOY,
    ],
)
async def test_approve_rejects_non_gate_passed_state(
    db: asyncpg.Connection,
    illegal_state: ProposalState,
):
    # Skip 'in-gate' if proposed_skill_version_id is required for that state — it
    # isn't in this schema, but the proposal in that state would still have a
    # null parent_version_id which is fine.
    proposal_id, _ = await _seed_proposal_in_state(db, state=illegal_state)
    with pytest.raises(ApprovalStateError, match="only 'gate-passed'"):
        await approve_proposal(
            db, proposal_id=proposal_id, decided_by="human:jit",
        )


async def test_reject_rejects_non_gate_passed_state(db: asyncpg.Connection):
    proposal_id, _ = await _seed_proposal_in_state(db, state=ProposalState.IN_GATE)
    with pytest.raises(ApprovalStateError):
        await reject_proposal(
            db, proposal_id=proposal_id, decided_by="human:jit",
        )


async def test_unknown_proposal_id_raises_not_found(db: asyncpg.Connection):
    with pytest.raises(ProposalNotFoundError):
        await approve_proposal(
            db, proposal_id=uuid4(), decided_by="human:jit",
        )


async def test_double_approve_raises_state_error(db: asyncpg.Connection):
    """Once a proposal is approved, a second approve raises (same as
    re-approving any non-gate-passed state)."""
    proposal_id, _ = await _seed_proposal_in_state(db)

    await approve_proposal(db, proposal_id=proposal_id, decided_by="human:jit")
    with pytest.raises(ApprovalStateError):
        await approve_proposal(
            db, proposal_id=proposal_id, decided_by="human:jit",
        )


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


async def test_decided_by_must_be_non_empty(db: asyncpg.Connection):
    proposal_id, _ = await _seed_proposal_in_state(db)
    with pytest.raises(ValueError, match="decided_by"):
        await approve_proposal(db, proposal_id=proposal_id, decided_by="")
    with pytest.raises(ValueError, match="decided_by"):
        await approve_proposal(db, proposal_id=proposal_id, decided_by="   ")
