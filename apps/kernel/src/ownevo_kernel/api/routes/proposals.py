"""`/api/proposals` — list, detail, approve, reject (W2.5).

Each handler is a thin SQL fetch + Pydantic shape-up; the actual state
transition logic lives in `ownevo_kernel.approvals.service`. Shape
errors raise `HTTPException` with the right status code so the web app
can render an inline error.

Status code conventions
-----------------------
  * 200 — success on read; success on decide (returns updated state).
  * 404 — proposal id not found.
  * 409 — proposal exists but is not in `gate-passed` (illegal
    decision attempt). Maps to `ApprovalStateError`.
  * 422 — body validation failure (FastAPI default). Includes the
    `decided_by` empty-string check.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, status

from ...approvals import (
    ApprovalStateError,
    ProposalNotFoundError,
    approve_proposal,
    deploy_proposal,
    reject_proposal,
    request_changes_proposal,
    rollback_proposal,
)
from ...types import ApproverType
from ..deps import ConnDep, DemoModeCheck
from ..jsonb import decode_jsonb_obj
from ..models import (
    ApprovalDetail,
    ApproveResponse,
    AuditEntry,
    DecideRequest,
    DeployRequest,
    DeployResponse,
    GateResultCases,
    IterationDetail,
    ProposalDetail,
    ProposalList,
    ProposalSummary,
    WorkflowDetail,
)

router = APIRouter(prefix="/api/proposals", tags=["proposals"])

# Hard-cap on list-endpoint pagination so a misconfigured client can't
# request the entire history in one call. The W5 polish UI will paginate
# explicitly; W2.5 doesn't.
_MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=ProposalList)
async def list_proposals(
    conn: ConnDep,
    state: str | None = Query(
        default=None,
        description="Filter by proposal state (e.g., 'gate-passed').",
        pattern=r"^(pending|in-gate|gate-failed|gate-passed|approved-awaiting-deploy|deployed|rejected|rolled-back|changes-requested)$",
    ),
    workflow_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT),
) -> ProposalList:
    """List proposals, optionally filtered. Newest first.

    The total count returned is the count under the same filter — it
    isn't capped by `limit`, so the UI can render "showing 50 of N".
    """
    where_clauses: list[str] = []
    params: list[Any] = []

    if state is not None:
        params.append(state)
        where_clauses.append(f"p.state = ${len(params)}::proposal_state")
    if workflow_id is not None:
        params.append(workflow_id)
        where_clauses.append(f"i.workflow_id = ${len(params)}")

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    total = await conn.fetchval(
        f"""
        SELECT COUNT(*)
        FROM proposals p
        JOIN iterations i ON i.id = p.iteration_id
        {where}
        """,
        *params,
    )

    params.append(limit)
    rows = await conn.fetch(
        f"""
        SELECT
            p.id,
            p.iteration_id,
            i.iteration_index,
            p.skill_id,
            i.workflow_id,
            w.description AS workflow_description,
            p.state::text AS state,
            p.plain_language_summary,
            p.eval_score,
            p.eval_rationale,
            p.expected_impact,
            p.created_at,
            p.state_updated_at
        FROM proposals p
        JOIN iterations i ON i.id = p.iteration_id
        JOIN workflows w ON w.id = i.workflow_id
        {where}
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    items = [_row_to_summary(r) for r in rows]
    return ProposalList(items=items, total=int(total or 0))


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@router.get("/{proposal_id}", response_model=ProposalDetail)
async def get_proposal(
    proposal_id: UUID,
    conn: ConnDep,
) -> ProposalDetail:
    proposal_row = await conn.fetchrow(
        """
        SELECT
            p.id,
            p.iteration_id,
            p.skill_id,
            p.parent_version_id,
            p.proposed_content,
            p.plain_language_summary,
            p.expected_impact,
            p.state::text AS state,
            p.eval_score,
            p.eval_rationale,
            p.created_at,
            p.state_updated_at,
            i.id AS iter_id,
            i.iteration_index,
            i.state::text AS iter_state,
            i.val_score,
            i.best_ever_score_before,
            i.best_ever_score_after,
            i.sandbox_error_class::text AS sandbox_error_class,
            i.started_at,
            i.ended_at,
            w.id AS workflow_id,
            w.description AS workflow_description,
            w.mode::text AS workflow_mode
        FROM proposals p
        JOIN iterations i ON i.id = p.iteration_id
        JOIN workflows w ON w.id = i.workflow_id
        WHERE p.id = $1
        """,
        proposal_id,
    )
    if proposal_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal {proposal_id} not found",
        )

    parent_version_content: str | None = None
    parent_version_seq: int | None = None
    if proposal_row["parent_version_id"] is not None:
        parent_row = await conn.fetchrow(
            "SELECT content, version_seq FROM skill_versions WHERE id = $1",
            proposal_row["parent_version_id"],
        )
        if parent_row is not None:
            parent_version_content = parent_row["content"]
            parent_version_seq = parent_row["version_seq"]

    audit_rows = await conn.fetch(
        """
        SELECT id, seq, kind::text AS kind, actor, payload, created_at
        FROM audit_entries
        WHERE related_id = $1 OR related_id = $2
        ORDER BY seq ASC
        LIMIT 500
        """,
        proposal_row["iter_id"],
        proposal_id,
    )

    # Gate audit entries are fetched in a separate, uncapped query so that
    # they're never silently dropped by the LIMIT 500 display cap above.
    # Gate entries are written last (highest seq) for any iteration — the
    # display cap can exclude them for proposals with many preceding entries.
    gate_audit_rows = await conn.fetch(
        """
        SELECT id, seq, kind::text AS kind, actor, payload, created_at
        FROM audit_entries
        WHERE related_id = $1
          AND kind IN ('gate-run-started', 'gate-run-completed')
        ORDER BY seq ASC
        """,
        proposal_row["iter_id"],
    )

    approval_row = await conn.fetchrow(
        """
        SELECT id, decided_by, approver_type::text AS approver_type, decision,
               comment, became_eval_case_id, decided_at
        FROM approvals
        WHERE proposal_id = $1
        """,
        proposal_id,
    )

    def _make_audit_entry(r: Any) -> AuditEntry:
        return AuditEntry(
            id=r["id"],
            seq=r["seq"],
            kind=r["kind"],
            actor=r["actor"],
            payload=decode_jsonb_obj(r["payload"]) or {},
            created_at=r["created_at"],
        )

    audit_entries = [_make_audit_entry(r) for r in audit_rows]
    gate_audit_entries = [_make_audit_entry(r) for r in gate_audit_rows]

    return ProposalDetail(
        id=proposal_row["id"],
        iteration_id=proposal_row["iteration_id"],
        skill_id=proposal_row["skill_id"],
        parent_version_id=proposal_row["parent_version_id"],
        state=proposal_row["state"],
        proposed_content=proposal_row["proposed_content"],
        parent_version_content=parent_version_content,
        parent_version_seq=parent_version_seq,
        plain_language_summary=proposal_row["plain_language_summary"],
        eval_score=_to_float(proposal_row["eval_score"]),
        eval_rationale=proposal_row["eval_rationale"],
        expected_impact=decode_jsonb_obj(proposal_row["expected_impact"]),
        created_at=proposal_row["created_at"],
        state_updated_at=proposal_row["state_updated_at"],
        iteration=IterationDetail(
            id=proposal_row["iter_id"],
            iteration_index=proposal_row["iteration_index"],
            state=proposal_row["iter_state"],
            val_score=_to_float(proposal_row["val_score"]),
            best_ever_score_before=_to_float(proposal_row["best_ever_score_before"]),
            best_ever_score_after=_to_float(proposal_row["best_ever_score_after"]),
            sandbox_error_class=proposal_row["sandbox_error_class"],
            started_at=proposal_row["started_at"],
            ended_at=proposal_row["ended_at"],
        ),
        workflow=WorkflowDetail(
            id=proposal_row["workflow_id"],
            description=proposal_row["workflow_description"],
            mode=proposal_row["workflow_mode"],
        ),
        audit_entries=audit_entries,
        approval=_approval_from_row(approval_row) if approval_row else None,
        gate_result_cases=_gate_result_cases_from_audit(gate_audit_entries),
    )


# ---------------------------------------------------------------------------
# Approve / reject
# ---------------------------------------------------------------------------


@router.post(
    "/{proposal_id}/approve",
    response_model=ApproveResponse,
    status_code=status.HTTP_200_OK,
)
async def approve(
    proposal_id: UUID,
    body: DecideRequest,
    conn: ConnDep,
) -> ApproveResponse:
    return await _decide(
        conn=conn,
        proposal_id=proposal_id,
        body=body,
        decision="approve",
    )


@router.post(
    "/{proposal_id}/reject",
    response_model=ApproveResponse,
    status_code=status.HTTP_200_OK,
)
async def reject(
    proposal_id: UUID,
    body: DecideRequest,
    conn: ConnDep,
) -> ApproveResponse:
    return await _decide(
        conn=conn,
        proposal_id=proposal_id,
        body=body,
        decision="reject",
    )


@router.post(
    "/{proposal_id}/request-changes",
    response_model=ApproveResponse,
    status_code=status.HTTP_200_OK,
)
async def request_changes(
    proposal_id: UUID,
    body: DecideRequest,
    conn: ConnDep,
) -> ApproveResponse:
    """Transition `gate-passed` → `changes-requested` with steering text.

    The comment is required — it IS the steering input that the next
    iteration on this workflow threads into the agent + proposer
    prompt. Returns 422 if the comment is missing or empty.
    """
    if not body.comment or not body.comment.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "comment is required for /request-changes — the steering "
                "text drives the next iteration's proposer"
            ),
        )
    return await _decide(
        conn=conn,
        proposal_id=proposal_id,
        body=body,
        decision="request-changes",
    )


# ---------------------------------------------------------------------------
# Deploy / rollback
# ---------------------------------------------------------------------------


@router.post(
    "/{proposal_id}/deploy",
    response_model=DeployResponse,
    status_code=status.HTTP_200_OK,
)
async def deploy(
    proposal_id: UUID,
    body: DeployRequest,
    conn: ConnDep,
    _: DemoModeCheck,
) -> DeployResponse:
    """Transition `approved-awaiting-deploy` → `deployed`.

    Sets `skills.deployed_version_id` to the proposal's skill version.
    Returns 409 if the proposal isn't approved-awaiting-deploy or if
    another proposal on the same skill is already deployed (caller
    must rollback first).
    """
    return await _deploy_or_rollback(
        conn=conn,
        proposal_id=proposal_id,
        decided_by=body.decided_by,
        action="deploy",
    )


@router.post(
    "/{proposal_id}/rollback",
    response_model=DeployResponse,
    status_code=status.HTTP_200_OK,
)
async def rollback(
    proposal_id: UUID,
    body: DeployRequest,
    conn: ConnDep,
    _: DemoModeCheck,
) -> DeployResponse:
    """Transition `deployed` → `rolled-back`.

    Reverts `skills.deployed_version_id` to the most recent prior
    deployment on the same skill, or NULL if none. Returns 409 if the
    proposal isn't currently deployed.
    """
    return await _deploy_or_rollback(
        conn=conn,
        proposal_id=proposal_id,
        decided_by=body.decided_by,
        action="rollback",
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _deploy_or_rollback(
    *,
    conn: asyncpg.Connection,
    proposal_id: UUID,
    decided_by: str,
    action: Literal["deploy", "rollback"],
) -> DeployResponse:
    fn = deploy_proposal if action == "deploy" else rollback_proposal
    try:
        proposal = await fn(
            conn,
            proposal_id=proposal_id,
            decided_by=decided_by,
        )
    except ProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ApprovalStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    skill_deployed_version_id = await conn.fetchval(
        "SELECT deployed_version_id FROM skills WHERE id = $1",
        proposal.skill_id,
    )
    return DeployResponse(
        proposal_id=proposal_id,
        state=proposal.state.value,
        skill_id=proposal.skill_id,
        skill_deployed_version_id=skill_deployed_version_id,
    )


_DECISION_DISPATCH = {
    "approve": (approve_proposal, "approved-awaiting-deploy"),
    "reject": (reject_proposal, "rejected"),
    "request-changes": (request_changes_proposal, "changes-requested"),
}


async def _decide(
    *,
    conn: asyncpg.Connection,
    proposal_id: UUID,
    body: DecideRequest,
    decision: str,
) -> ApproveResponse:
    approver_type = _resolve_approver_type(body.approver_type)
    fn, new_state = _DECISION_DISPATCH[decision]
    try:
        approval = await fn(
            conn,
            proposal_id=proposal_id,
            decided_by=body.decided_by,
            approver_type=approver_type,
            comment=body.comment,
        )
    except ProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ApprovalStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    return ApproveResponse(
        proposal_id=proposal_id,
        state=new_state,
        approval=ApprovalDetail(
            id=approval.id,
            decided_by=approval.decided_by,
            approver_type=approval.approver_type.value,
            decision=approval.decision,
            comment=approval.comment,
            became_eval_case_id=approval.became_eval_case_id,
            decided_at=approval.decided_at,
        ),
    )


def _resolve_approver_type(raw: str | None) -> ApproverType:
    if raw is None:
        return ApproverType.HUMAN
    try:
        return ApproverType(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"approver_type must be one of "
                f"{[t.value for t in ApproverType]!r}; got {raw!r}"
            ),
        ) from exc


def _row_to_summary(row: asyncpg.Record) -> ProposalSummary:
    return ProposalSummary(
        id=row["id"],
        iteration_id=row["iteration_id"],
        iteration_index=row["iteration_index"],
        skill_id=row["skill_id"],
        workflow_id=row["workflow_id"],
        workflow_description=row["workflow_description"],
        state=row["state"],
        plain_language_summary=row["plain_language_summary"],
        eval_score=_to_float(row["eval_score"]),
        eval_rationale=row["eval_rationale"],
        expected_impact=decode_jsonb_obj(row["expected_impact"]),
        created_at=row["created_at"],
        state_updated_at=row["state_updated_at"],
    )


def _approval_from_row(row: asyncpg.Record) -> ApprovalDetail:
    return ApprovalDetail(
        id=row["id"],
        decided_by=row["decided_by"],
        approver_type=row["approver_type"],
        decision=row["decision"],
        comment=row["comment"],
        became_eval_case_id=row["became_eval_case_id"],
        decided_at=row["decided_at"],
    )


def _to_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _gate_result_cases_from_audit(
    entries: list[AuditEntry],
) -> GateResultCases | None:
    """Reconstruct the per-eval-case breakdown from gate audit payloads.

    `gate-run-started` carries the prior suite (`prior_eval_task_ids`).
    `gate-run-completed` carries the outcome lists
    (`failed_prior_task_ids`, `promotable_task_ids`). The set difference
    `prior - failed` is the passing prior cases. No DB schema change —
    we read what the gate already audits.

    Returns None when neither audit kind is present (e.g. a hand-seeded
    proposal that bypassed the gate persistence path).
    """
    started = next((e for e in entries if e.kind == "gate-run-started"), None)
    completed = next(
        (e for e in entries if e.kind == "gate-run-completed"), None
    )
    if started is None and completed is None:
        return None

    prior = _string_list(
        (started.payload if started else {}).get("prior_eval_task_ids")
    )
    failed = _string_list(
        (completed.payload if completed else {}).get("failed_prior_task_ids")
    )
    promotable = _string_list(
        (completed.payload if completed else {}).get("promotable_task_ids")
    )
    failed_set = set(failed)
    passed = [t for t in prior if t not in failed_set]
    return GateResultCases(
        passed=passed,
        regressed=failed,
        newly_admitted=promotable,
        unknown=(completed is None) or (started is None),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value]
