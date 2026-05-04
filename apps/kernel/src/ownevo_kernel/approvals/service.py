"""Proposal approval/rejection — `gate-passed` → terminal state.

`approve_proposal` and `reject_proposal` are the load-bearing entry
points. Both run inside a single transaction:

  1. Lock the proposal row (`SELECT … FOR UPDATE`) to serialize
     concurrent decisions on the same proposal — the
     `UNIQUE(proposal_id)` constraint on `approvals` would catch a
     double-decide eventually, but the lock turns it into a clean
     error instead of a UniqueViolationError surfacing to the caller.
  2. Validate the proposal is in `gate-passed` (the only legal
     starting state per `docs/STATE_MACHINES.md`).
  3. INSERT the `approvals` row.
  4. UPDATE `proposals.state` to the terminal state.
  5. (reject + comment only) INSERT an eval_case with
     provenance=`rejected-feedback`, then UPDATE
     `approvals.became_eval_case_id` to link them. The comment IS the
     failure description the next gate run will protect against.
  6. Append `audit_entries` (`proposal-approved` or `proposal-rejected`).

Why not fold this into `persist_gate_run`
-----------------------------------------
`persist_gate_run` finalizes proposals on the gate's *automatic*
decision (logical regression / no-improvement / sandbox error). The
human/LLM-judge layer above gate-passed is a separate trust seam: the
gate established that the change is safe to consider; the approver
decides whether it ships. Keeping them in different modules keeps the
gate runner free of human-mode concerns.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from ..audit import append_audit_entry
from ..eval_cases import add_eval_case
from ..types import (
    Approval,
    ApproverType,
    AuditKind,
    EvalCase,
    ProposalState,
    ProvenanceKind,
)


class ApprovalStateError(Exception):
    """Proposal exists but is not in a state that admits approve/reject.

    Raised when `proposals.state != 'gate-passed'`. Common causes:
      * Proposal already approved or rejected (idempotent: caller should
        re-fetch and surface the existing decision rather than retry).
      * Proposal still `in-gate` — gate run hasn't finished yet.
      * Proposal `gate-failed` — sandbox error, can't approve a non-validated
        change.
    """


class ProposalNotFoundError(LookupError):
    """No proposal with this id. Surface as 404 from the HTTP layer."""


async def approve_proposal(
    conn: asyncpg.Connection,
    *,
    proposal_id: UUID,
    decided_by: str,
    approver_type: ApproverType = ApproverType.HUMAN,
    comment: str | None = None,
) -> Approval:
    """Transition `gate-passed` → `approved-awaiting-deploy`.

    Args:
        conn: asyncpg connection — caller MUST NOT wrap this in another
            transaction; the function manages its own.
        proposal_id: target proposal.
        decided_by: actor string. Convention: `human:<userid>` for human,
            `llm-judge` for the LLM judge variant, `autonomous` for the
            workflow.mode='autonomous' auto-approve path.
        approver_type: which approver flavor decided. Defaults to HUMAN
            (the W2.5 UI surface); autonomous and llm-judge paths set this
            explicitly.
        comment: optional reviewer comment. Stored on the approvals row
            (no eval case is created on approve — only on reject does
            the comment become a regression case).

    Raises:
        ProposalNotFoundError: no row with this id.
        ApprovalStateError: proposal is not in `gate-passed`.
    """
    return await _decide(
        conn,
        proposal_id=proposal_id,
        decision="approve",
        target_state=ProposalState.APPROVED_AWAITING_DEPLOY,
        audit_kind=AuditKind.PROPOSAL_APPROVED,
        decided_by=decided_by,
        approver_type=approver_type,
        comment=comment,
    )


async def reject_proposal(
    conn: asyncpg.Connection,
    *,
    proposal_id: UUID,
    decided_by: str,
    approver_type: ApproverType = ApproverType.HUMAN,
    comment: str | None = None,
) -> Approval:
    """Transition `gate-passed` → `rejected`.

    If `comment` is non-empty, also creates an eval_case with
    provenance=`rejected-feedback` and links it via
    `approvals.became_eval_case_id`. The comment text becomes the
    eval case's `expected_behavior.note` so the next gate iteration
    has a regression case derived directly from the rejection.

    The eval case is workflow-scoped (matches the proposal's iteration's
    workflow). Empty/whitespace-only comments are treated as no comment
    — no eval case created, `became_eval_case_id` stays null.
    """
    return await _decide(
        conn,
        proposal_id=proposal_id,
        decision="reject",
        target_state=ProposalState.REJECTED,
        audit_kind=AuditKind.PROPOSAL_REJECTED,
        decided_by=decided_by,
        approver_type=approver_type,
        comment=comment,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _decide(
    conn: asyncpg.Connection,
    *,
    proposal_id: UUID,
    decision: str,  # "approve" | "reject"
    target_state: ProposalState,
    audit_kind: AuditKind,
    decided_by: str,
    approver_type: ApproverType,
    comment: str | None,
) -> Approval:
    if not decided_by or not decided_by.strip():
        raise ValueError("decided_by must be a non-empty string")

    normalized_comment = comment.strip() if comment else None
    if normalized_comment == "":
        normalized_comment = None

    async with conn.transaction():
        # 1. Lock proposal — serializes double-decide attempts.
        proposal_row = await conn.fetchrow(
            """
            SELECT p.id, p.state::text AS state, p.iteration_id, p.skill_id,
                   i.workflow_id
            FROM proposals p
            JOIN iterations i ON i.id = p.iteration_id
            WHERE p.id = $1
            FOR UPDATE OF p
            """,
            proposal_id,
        )
        if proposal_row is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")

        # 2. State validation: only `gate-passed` admits a decision.
        current_state = ProposalState(proposal_row["state"])
        if current_state != ProposalState.GATE_PASSED:
            raise ApprovalStateError(
                f"Proposal {proposal_id} is in state {current_state.value!r}; "
                f"only {ProposalState.GATE_PASSED.value!r} can be approved/rejected",
            )

        workflow_id: str = proposal_row["workflow_id"]

        # 3. Insert approvals row. UNIQUE(proposal_id) means concurrent
        # callers race to be first; the row-lock above keeps the race
        # honest, and a UniqueViolationError here would surface as 500
        # to the caller (caller is expected to have lost the race).
        approval_row = await conn.fetchrow(
            """
            INSERT INTO approvals (
                proposal_id, decided_by, approver_type, decision, comment
            )
            VALUES ($1, $2, $3::approver_type, $4, $5)
            RETURNING id, proposal_id, decided_by, approver_type::text AS approver_type,
                      decision, comment, became_eval_case_id, decided_at
            """,
            proposal_id,
            decided_by,
            approver_type.value,
            decision,
            normalized_comment,
        )
        approval_id: UUID = approval_row["id"]

        # 4. Advance proposal state.
        await conn.execute(
            """
            UPDATE proposals
            SET state            = $2::proposal_state,
                state_updated_at = now()
            WHERE id = $1
            """,
            proposal_id,
            target_state.value,
        )

        # 5. Reject + comment → seed eval case + link.
        eval_case: EvalCase | None = None
        if decision == "reject" and normalized_comment is not None:
            eval_case = await add_eval_case(
                conn,
                workflow_id=workflow_id,
                provenance=ProvenanceKind.REJECTED_FEEDBACK,
                input={
                    "proposal_id": str(proposal_id),
                    "skill_id": proposal_row["skill_id"],
                },
                expected_behavior={
                    "note": normalized_comment,
                    "source": "human-rejection-comment",
                },
            )
            await conn.execute(
                "UPDATE approvals SET became_eval_case_id = $2 WHERE id = $1",
                approval_id,
                eval_case.id,
            )

        # 6. Audit entry.
        audit_payload: dict[str, Any] = {
            "proposal_id": str(proposal_id),
            "decision": decision,
            "approver_type": approver_type.value,
            "decided_by": decided_by,
        }
        if normalized_comment is not None:
            audit_payload["comment"] = normalized_comment
        if eval_case is not None:
            audit_payload["became_eval_case_id"] = str(eval_case.id)
        await append_audit_entry(
            conn,
            kind=audit_kind,
            payload=audit_payload,
            actor=decided_by,
            related_id=proposal_id,
        )

    # Return the post-link approval (with became_eval_case_id populated).
    return Approval(
        id=approval_row["id"],
        proposal_id=approval_row["proposal_id"],
        decided_by=approval_row["decided_by"],
        approver_type=ApproverType(approval_row["approver_type"]),
        decision=approval_row["decision"],
        comment=approval_row["comment"],
        became_eval_case_id=eval_case.id if eval_case is not None else None,
        decided_at=approval_row["decided_at"],
    )
