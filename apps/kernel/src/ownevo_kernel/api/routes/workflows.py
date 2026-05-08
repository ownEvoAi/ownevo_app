"""`/api/workflows` — workflow list + per-workflow iteration timeline.

Drives the W7 Health page (workflow-rows table) and the LiftChart
component (iteration_index × val_score line + annotated dots).

Both endpoints are read-only joins over `workflows` + `iterations` +
`proposals`. Pagination is intentionally absent for MVP — the demo
workspace has 4 workflows and at most a few hundred iterations per
workflow. TODO-18 covers pagination if real customers push the row
count.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, HTTPException, status

from ..deps import ConnDep
from ..models import (
    IterationList,
    IterationPoint,
    WorkflowList,
    WorkflowSummary,
)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# Approved proposal states — W2.5 STATE_MACHINES.md.
# 'approved-awaiting-deploy' is the post-decision state before the
# kernel deploys; 'deployed' is post-deploy. Both indicate human/LLM
# judge approval, so both contribute to `last_improved_at`.
_APPROVED_STATES = ("approved-awaiting-deploy", "deployed")
_PENDING_STATES = ("gate-passed",)


@router.get("", response_model=WorkflowList)
async def list_workflows(conn: ConnDep) -> WorkflowList:
    """List every workflow with summary metrics for the Health page.

    Sorted by `created_at ASC` so demand-prediction (the bootstrap
    workflow) ranks first; the demo flow follows that visual ordering.
    """
    rows = await conn.fetch(
        """
        SELECT
            w.id,
            w.description,
            w.mode::text                                AS mode,
            (
                SELECT COUNT(*)::int
                FROM iterations i
                WHERE i.workflow_id = w.id
                  AND i.state <> 'running'
            )                                           AS iteration_count,
            (
                SELECT MAX(i.best_ever_score_after)
                FROM iterations i
                WHERE i.workflow_id = w.id
            )                                           AS best_ever_score,
            (
                SELECT MAX(p.state_updated_at)
                FROM proposals p
                JOIN iterations i ON i.id = p.iteration_id
                WHERE i.workflow_id = w.id
                  AND p.state = ANY($1::text[])
            )                                           AS last_improved_at,
            (
                SELECT COUNT(*)::int
                FROM proposals p
                JOIN iterations i ON i.id = p.iteration_id
                WHERE i.workflow_id = w.id
                  AND p.state = ANY($2::text[])
            )                                           AS pending_proposals_count
        FROM workflows w
        ORDER BY w.created_at ASC, w.id ASC
        """,
        list(_APPROVED_STATES),
        list(_PENDING_STATES),
    )

    items = [_row_to_summary(r) for r in rows]
    return WorkflowList(items=items, total=len(items))


@router.get("/{workflow_id}/iterations", response_model=IterationList)
async def list_iterations(workflow_id: str, conn: ConnDep) -> IterationList:
    """Chronological iterations for the LiftChart.

    One row per iteration; the UI plots `iteration_index` × `val_score`
    and overlays a dot wherever `has_approved_proposal=True`. Running
    iterations are excluded — `val_score` is null until the gate
    finishes, and an in-flight point would dangle the line.
    """
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        )

    rows = await conn.fetch(
        """
        SELECT
            i.iteration_index,
            i.val_score,
            i.best_ever_score_after,
            i.state::text                               AS state,
            i.ended_at,
            EXISTS (
                SELECT 1
                FROM proposals p
                WHERE p.iteration_id = i.id
                  AND p.state = ANY($2::text[])
            )                                           AS has_approved_proposal
        FROM iterations i
        WHERE i.workflow_id = $1
          AND i.state <> 'running'
        ORDER BY i.iteration_index ASC
        """,
        workflow_id,
        list(_APPROVED_STATES),
    )

    points = [
        IterationPoint(
            iteration_index=r["iteration_index"],
            val_score=float(r["val_score"]) if r["val_score"] is not None else None,
            best_ever_score_after=(
                float(r["best_ever_score_after"])
                if r["best_ever_score_after"] is not None
                else None
            ),
            state=r["state"],
            has_approved_proposal=bool(r["has_approved_proposal"]),
            ended_at=r["ended_at"],
        )
        for r in rows
    ]
    return IterationList(workflow_id=workflow_id, items=points)


def _row_to_summary(row: asyncpg.Record) -> WorkflowSummary:
    return WorkflowSummary(
        id=row["id"],
        description=row["description"],
        mode=row["mode"],
        iteration_count=row["iteration_count"],
        best_ever_score=(
            float(row["best_ever_score"]) if row["best_ever_score"] is not None else None
        ),
        last_improved_at=row["last_improved_at"],
        pending_proposals_count=row["pending_proposals_count"],
    )
