"""Proposal deploy/rollback — `approved-awaiting-deploy` → `deployed` → `rolled-back`.

The state machine in `docs/STATE_MACHINES.md` defines two transitions
beyond the gate-passed → approved/rejected pair handled by `service.py`:

  approved-awaiting-deploy → deployed     (operator hits Deploy)
  deployed                 → rolled-back  (operator hits Rollback)

Both transitions update `skills.deployed_version_id`, the production
pointer (separate from `head_version_id`, which tracks the best
gate-validated version — see migration 0003 for the split):

  * Deploy: set `deployed_version_id` to this proposal's
    `iterations.proposed_skill_version_id`. Caller must first rollback
    any currently-deployed proposal on the same skill — at most one
    proposal per skill may be in state='deployed' at a time.
  * Rollback: walk the audit log backwards for `proposal-deployed`
    entries on the same skill; revert `deployed_version_id` to the
    most recent prior deployment, or NULL if none. The rolled-back
    proposal stays terminal; restoring its content requires a fresh
    proposal pointing at the older version.

Audit semantics: `proposal-deployed` and `proposal-rolled-back` carry
`{proposal_id, skill_id, deployed_version_id, prior_deployed_version_id}`.
The prior pointer captures rollback lineage so the audit chain alone
reconstructs which version was production-live at any point in time.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from ..api.jsonb import decode_jsonb_obj
from ..audit import append_audit_entry
from ..types import (
    AuditKind,
    Proposal,
    ProposalState,
)
from .apply import APPLY_BY_KIND
from .service import ApprovalStateError, ProposalNotFoundError


async def deploy_proposal(
    conn: asyncpg.Connection,
    *,
    proposal_id: UUID,
    decided_by: str,
) -> Proposal:
    """Transition `approved-awaiting-deploy` → `deployed`.

    Sets `skills.deployed_version_id` to this proposal's skill version.
    The skill must have no other proposal currently in `deployed` state —
    callers must rollback the live one first. This keeps the invariant
    "at most one deployed proposal per skill" trivially enforceable
    without a partial unique index.

    Raises:
        ProposalNotFoundError: no proposal with this id.
        ApprovalStateError: proposal is not in `approved-awaiting-deploy`,
            or the proposal's iteration has no `proposed_skill_version_id`,
            or another proposal on the same skill is already deployed.
    """
    if not decided_by or not decided_by.strip():
        raise ValueError("decided_by must be a non-empty string")

    async with conn.transaction():
        proposal_row = await conn.fetchrow(
            """
            SELECT p.id, p.state::text AS state, p.skill_id, p.iteration_id,
                   p.kind::text AS kind, p.proposed_payload,
                   i.proposed_skill_version_id, i.workflow_id
            FROM proposals p
            JOIN iterations i ON i.id = p.iteration_id
            WHERE p.id = $1
            FOR UPDATE OF p
            """,
            proposal_id,
        )
        if proposal_row is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")

        current_state = ProposalState(proposal_row["state"])
        if current_state != ProposalState.APPROVED_AWAITING_DEPLOY:
            raise ApprovalStateError(
                f"Proposal {proposal_id} is in state {current_state.value!r}; "
                f"only {ProposalState.APPROVED_AWAITING_DEPLOY.value!r} can be deployed",
            )

        # Non-skill kinds dispatch to the per-kind apply functions.
        # They have no skill_versions row to point at — the workflow
        # row is the production surface, and the apply function does
        # the actual UPDATE.
        kind: str = proposal_row["kind"] or "skill"
        if kind != "skill":
            workflow_id: str = proposal_row["workflow_id"]
            apply_fn = APPLY_BY_KIND.get(kind)
            if apply_fn is None:
                raise ApprovalStateError(
                    f"Proposal {proposal_id} has unknown kind {kind!r}; "
                    "no apply handler registered",
                )
            payload = decode_jsonb_obj(proposal_row["proposed_payload"]) or {}
            apply_summary = await apply_fn(
                conn,
                workflow_id=workflow_id,
                payload=payload,
            )
            await conn.execute(
                """
                UPDATE proposals
                SET state            = 'deployed'::proposal_state,
                    state_updated_at = now()
                WHERE id = $1
                """,
                proposal_id,
            )
            await append_audit_entry(
                conn,
                kind=AuditKind.PROPOSAL_DEPLOYED,
                payload={
                    "proposal_id": str(proposal_id),
                    "workflow_id": workflow_id,
                    "kind": kind,
                    "applied": apply_summary,
                },
                actor=decided_by,
                related_id=proposal_id,
            )
            return await _fetch_proposal(conn, proposal_id)

        skill_id: str = proposal_row["skill_id"]
        proposed_version_id: UUID | None = proposal_row["proposed_skill_version_id"]
        if proposed_version_id is None:
            # The iteration row didn't pre-register a skill_version, so we
            # have no concrete version to point production at. Surfaces as
            # 409 from the HTTP layer — fixable by re-running the loop.
            raise ApprovalStateError(
                f"Proposal {proposal_id} has no proposed_skill_version_id; "
                "cannot deploy without a registered skill version",
            )

        # Enforce single-deployed invariant on this skill. A future
        # multi-deployment ("variant") feature would relax this; today
        # the customer agent reads one version, full stop.
        live_other = await conn.fetchval(
            """
            SELECT id FROM proposals
            WHERE skill_id = $1
              AND state = 'deployed'::proposal_state
              AND id != $2
            LIMIT 1
            """,
            skill_id,
            proposal_id,
        )
        if live_other is not None:
            raise ApprovalStateError(
                f"Skill {skill_id!r} already has a deployed proposal "
                f"({live_other}); rollback that proposal before deploying a new one",
            )

        prior_deployed_version_id = await conn.fetchval(
            "SELECT deployed_version_id FROM skills WHERE id = $1",
            skill_id,
        )

        await conn.execute(
            """
            UPDATE proposals
            SET state            = 'deployed'::proposal_state,
                state_updated_at = now()
            WHERE id = $1
            """,
            proposal_id,
        )

        await conn.execute(
            "UPDATE skills SET deployed_version_id = $2 WHERE id = $1",
            skill_id,
            proposed_version_id,
        )

        await append_audit_entry(
            conn,
            kind=AuditKind.PROPOSAL_DEPLOYED,
            payload={
                "proposal_id": str(proposal_id),
                "skill_id": skill_id,
                "deployed_version_id": str(proposed_version_id),
                "prior_deployed_version_id": (
                    str(prior_deployed_version_id)
                    if prior_deployed_version_id is not None
                    else None
                ),
            },
            actor=decided_by,
            related_id=proposal_id,
        )

        return await _fetch_proposal(conn, proposal_id)


async def rollback_proposal(
    conn: asyncpg.Connection,
    *,
    proposal_id: UUID,
    decided_by: str,
) -> Proposal:
    """Transition `deployed` → `rolled-back`.

    Reverts `skills.deployed_version_id` to the most recent prior
    `proposal-deployed` audit entry on the same skill (or NULL if there
    is none). The rolled-back proposal stays terminal; restoring an
    older version's *content* requires a new proposal — this only moves
    the production pointer.

    Raises:
        ProposalNotFoundError: no proposal with this id.
        ApprovalStateError: proposal is not in `deployed`.
    """
    if not decided_by or not decided_by.strip():
        raise ValueError("decided_by must be a non-empty string")

    async with conn.transaction():
        proposal_row = await conn.fetchrow(
            """
            SELECT p.id, p.state::text AS state, p.skill_id, p.iteration_id,
                   p.kind::text AS kind, i.proposed_skill_version_id
            FROM proposals p
            JOIN iterations i ON i.id = p.iteration_id
            WHERE p.id = $1
            FOR UPDATE OF p
            """,
            proposal_id,
        )
        if proposal_row is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")

        current_state = ProposalState(proposal_row["state"])
        if current_state != ProposalState.DEPLOYED:
            raise ApprovalStateError(
                f"Proposal {proposal_id} is in state {current_state.value!r}; "
                f"only {ProposalState.DEPLOYED.value!r} can be rolled back",
            )

        # Non-skill artifact proposals (description / metric / sim /
        # ui-view) have no skill_versions pointer to revert. The
        # rollback UX for those kinds is "create a new proposal pointing
        # at the previous value". Guard here so a direct API call can't
        # silently succeed without actually reverting anything.
        if proposal_row.get("kind") and proposal_row["kind"] not in ("skill", None, ""):
            raise ApprovalStateError(
                f"Proposal {proposal_id} has kind {proposal_row['kind']!r}; "
                "rollback is only supported for kind='skill' proposals. "
                "To revert this change, create a new proposal with the prior value.",
            )

        skill_id: str = proposal_row["skill_id"]
        rolled_back_version_id: UUID | None = proposal_row["proposed_skill_version_id"]

        # Find the prior production version: the iteration of the most
        # recent OTHER proposal that is still in 'deployed' state.
        # Filtering p.state = 'deployed' avoids restoring to a version
        # whose proposal was itself rolled-back — that would leave
        # deployed_version_id non-null with no matching deployed proposal,
        # producing a stuck "Deployed v{n}" UI state with no escape hatch.
        prior_row = await conn.fetchrow(
            """
            SELECT i.proposed_skill_version_id
            FROM audit_entries ae
            JOIN proposals p   ON p.id = ae.related_id
            JOIN iterations i  ON i.id = p.iteration_id
            WHERE ae.kind = 'proposal-deployed'::audit_kind
              AND p.skill_id = $1
              AND ae.related_id != $2
              AND p.state = 'deployed'::proposal_state
            ORDER BY ae.seq DESC
            LIMIT 1
            """,
            skill_id,
            proposal_id,
        )
        prior_version_id: UUID | None = (
            prior_row["proposed_skill_version_id"] if prior_row is not None else None
        )

        await conn.execute(
            """
            UPDATE proposals
            SET state            = 'rolled-back'::proposal_state,
                state_updated_at = now()
            WHERE id = $1
            """,
            proposal_id,
        )

        await conn.execute(
            "UPDATE skills SET deployed_version_id = $2 WHERE id = $1",
            skill_id,
            prior_version_id,
        )

        await append_audit_entry(
            conn,
            kind=AuditKind.PROPOSAL_ROLLED_BACK,
            payload={
                "proposal_id": str(proposal_id),
                "skill_id": skill_id,
                "rolled_back_version_id": (
                    str(rolled_back_version_id)
                    if rolled_back_version_id is not None
                    else None
                ),
                "restored_deployed_version_id": (
                    str(prior_version_id) if prior_version_id is not None else None
                ),
            },
            actor=decided_by,
            related_id=proposal_id,
        )

        return await _fetch_proposal(conn, proposal_id)


async def _fetch_proposal(
    conn: asyncpg.Connection, proposal_id: UUID,
) -> Proposal:
    row = await conn.fetchrow(
        """
        SELECT id, iteration_id, skill_id, parent_version_id,
               proposed_content, plain_language_summary, expected_impact,
               state::text AS state, eval_score, eval_rationale,
               created_at, state_updated_at
        FROM proposals
        WHERE id = $1
        """,
        proposal_id,
    )
    if row is None:  # pragma: no cover — caller already located this row
        raise ProposalNotFoundError(f"Proposal {proposal_id} not found")
    expected_impact: Any = row["expected_impact"]
    if isinstance(expected_impact, str):
        expected_impact = json.loads(expected_impact)
    return Proposal(
        id=row["id"],
        iteration_id=row["iteration_id"],
        skill_id=row["skill_id"],
        parent_version_id=row["parent_version_id"],
        proposed_content=row["proposed_content"],
        plain_language_summary=row["plain_language_summary"],
        expected_impact=expected_impact,
        state=ProposalState(row["state"]),
        eval_score=float(row["eval_score"]) if row["eval_score"] is not None else None,
        eval_rationale=row["eval_rationale"],
        created_at=row["created_at"],
        state_updated_at=row["state_updated_at"],
    )


__all__ = [
    "deploy_proposal",
    "rollback_proposal",
]
