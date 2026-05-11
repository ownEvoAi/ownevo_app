"""`/api/skills` + `/api/workflows/{id}/skills` — skill inspection.

W7 slices 9 + 10 (PLAN rows 7.1.10 + 7.1.11). Drives the per-skill
detail surface (prompt variant for `kind='instruction'`, code variant
for `kind='python'`) and the per-workflow skill list.

Both endpoints are read-only joins over `skills` + `skill_versions` +
`workflows` + `eval_cases`. The detail endpoint inlines the head and
parent version content so the web renderer can compute the diff
without a separate fetch.
"""

from __future__ import annotations

import asyncio

import asyncpg
from fastapi import APIRouter, HTTPException, status

from ..deps import ConnDep
from ..jsonb import decode_jsonb_obj
from ..models import (
    SkillDetail,
    SkillList,
    SkillRelatedEvalCase,
    SkillSummary,
    SkillVersionSummary,
)

skill_router = APIRouter(prefix="/api/skills", tags=["skills"])
workflow_skills_router = APIRouter(prefix="/api/workflows", tags=["skills"])

# Cap related eval cases on the detail page. The skill detail surface
# renders a "what test cases is this skill on the hook for" section;
# more than this and the page becomes a list view rather than a
# focused detail.
_RELATED_EVAL_CASES_LIMIT = 12


@skill_router.get("", response_model=SkillList)
async def list_skills(conn: ConnDep) -> SkillList:
    """Workspace-scoped index of every skill — drives the Skills
    library page (PLAN row 8.0.4 / `www/preview/.../11-skills-registry.html`).

    Single-tenant for MVP per D4 — no workspace filter is applied yet.
    Multi-tenant retrofit (TODO-1) adds `WHERE workspace_id = $1`.

    Sorted by `kind` (instruction first, then python, then composite —
    mirrors the per-workflow listing) then `id` ASC for stable ordering.
    """
    rows = await conn.fetch(
        """
        SELECT
            s.id,
            s.kind::text                AS kind,
            s.workflow_id,
            s.capability_tags,
            s.head_version_id,
            sv.version_seq              AS head_version_seq,
            sv.created_at               AS head_created_at
        FROM skills s
        LEFT JOIN skill_versions sv ON sv.id = s.head_version_id
        ORDER BY
            CASE s.kind
                WHEN 'instruction' THEN 0
                WHEN 'python'      THEN 1
                WHEN 'composite'   THEN 2
                ELSE 3
            END,
            s.id ASC
        """,
    )
    return SkillList(items=[_row_to_skill_summary(r) for r in rows])


@workflow_skills_router.get(
    "/{workflow_id}/skills", response_model=SkillList,
)
async def list_workflow_skills(workflow_id: str, conn: ConnDep) -> SkillList:
    """Return every skill bound to a workflow.

    Sorted by `kind` (instruction first, then python, then composite —
    matches the agent-anatomy pane mock layout where prompts sit above
    code) then `id` ASC for stable ordering.
    """
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1", workflow_id,
    )
    if not workflow_exists:
        # Static message — never reflect the user-supplied path param.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    rows = await conn.fetch(
        """
        SELECT
            s.id,
            s.kind::text                AS kind,
            s.workflow_id,
            s.capability_tags,
            s.head_version_id,
            sv.version_seq              AS head_version_seq,
            sv.created_at               AS head_created_at
        FROM skills s
        LEFT JOIN skill_versions sv ON sv.id = s.head_version_id
        WHERE s.workflow_id = $1
        ORDER BY
            CASE s.kind
                WHEN 'instruction' THEN 0
                WHEN 'python'      THEN 1
                WHEN 'composite'   THEN 2
                ELSE 3
            END,
            s.id ASC
        """,
        workflow_id,
    )
    return SkillList(items=[_row_to_skill_summary(r) for r in rows])


@skill_router.get("/{skill_id}", response_model=SkillDetail)
async def get_skill(skill_id: str, conn: ConnDep) -> SkillDetail:
    """Per-skill detail with head + parent content + version history."""
    skill = await conn.fetchrow(
        """
        SELECT
            s.id,
            s.kind::text        AS kind,
            s.workflow_id,
            s.capability_tags,
            s.head_version_id,
            s.deployed_version_id,
            sv_dep.version_seq  AS deployed_version_seq,
            w.description       AS workflow_description
        FROM skills s
        LEFT JOIN workflows w ON w.id = s.workflow_id
        LEFT JOIN skill_versions sv_dep ON sv_dep.id = s.deployed_version_id
        WHERE s.id = $1
        """,
        skill_id,
    )
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )

    head = None
    if skill["head_version_id"] is not None:
        head = await conn.fetchrow(
            """
            SELECT
                id, version_seq, parent_version_id, content, retention_block,
                diff_summary, created_at, created_by
            FROM skill_versions
            WHERE id = $1
            """,
            skill["head_version_id"],
        )
        if head is None:
            # head_version_id pointer exists but the referenced row is
            # gone — DB-level corruption. Surface loudly instead of
            # degrading to the empty-state UI shape (which would lie
            # to the operator that the skill has no version yet).
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Skill head_version_id references missing row",
            )

    parent_content = None
    parent_version_seq = None
    if head is not None and head["parent_version_id"] is not None:
        parent = await conn.fetchrow(
            "SELECT version_seq, content FROM skill_versions WHERE id = $1",
            head["parent_version_id"],
        )
        if parent is not None:
            parent_content = parent["content"]
            parent_version_seq = parent["version_seq"]

    # history_rows, deploy affordances, and related eval cases are all
    # independent of each other after `skill` is fetched. asyncio.gather
    # communicates that and is ready for a pool-per-coroutine upgrade.
    history_rows, deployable_row, deployed_proposal_id, related = (
        await asyncio.gather(
            conn.fetch(
                """
                SELECT id, version_seq, parent_version_id, diff_summary,
                       created_by, created_at
                FROM skill_versions
                WHERE skill_id = $1
                ORDER BY version_seq DESC
                """,
                skill_id,
            ),
            conn.fetchrow(
                """
                SELECT p.id, i.proposed_skill_version_id, sv.version_seq
                FROM proposals p
                JOIN iterations i ON i.id = p.iteration_id
                LEFT JOIN skill_versions sv ON sv.id = i.proposed_skill_version_id
                WHERE p.skill_id = $1
                  AND p.state = 'approved-awaiting-deploy'::proposal_state
                ORDER BY p.state_updated_at DESC
                LIMIT 1
                """,
                skill_id,
            ),
            conn.fetchval(
                """
                SELECT id FROM proposals
                WHERE skill_id = $1
                  AND state = 'deployed'::proposal_state
                ORDER BY state_updated_at DESC
                LIMIT 1
                """,
                skill_id,
            ),
            _related_eval_cases(
                conn, skill_id=skill_id,
                kind=skill["kind"],
                workflow_id=skill["workflow_id"],
            ),
        )
    )
    versions = [
        SkillVersionSummary(
            id=r["id"],
            version_seq=r["version_seq"],
            parent_version_id=r["parent_version_id"],
            diff_summary=r["diff_summary"],
            created_by=r["created_by"],
            created_at=r["created_at"],
        )
        for r in history_rows
    ]

    return SkillDetail(
        id=skill["id"],
        kind=skill["kind"],
        workflow_id=skill["workflow_id"],
        workflow_description=skill["workflow_description"],
        capability_tags=list(skill["capability_tags"] or []),
        head_version_id=skill["head_version_id"],
        head_version_seq=head["version_seq"] if head else None,
        head_content=head["content"] if head else None,
        head_retention_block=decode_jsonb_obj(head["retention_block"])
        if head
        else None,
        head_diff_summary=head["diff_summary"] if head else None,
        head_created_at=head["created_at"] if head else None,
        head_created_by=head["created_by"] if head else None,
        parent_content=parent_content,
        parent_version_seq=parent_version_seq,
        deployed_version_id=skill["deployed_version_id"],
        deployed_version_seq=skill["deployed_version_seq"],
        deployable_proposal_id=(
            deployable_row["id"] if deployable_row is not None else None
        ),
        deployable_proposal_version_seq=(
            deployable_row["version_seq"] if deployable_row is not None else None
        ),
        deployed_proposal_id=deployed_proposal_id,
        versions=versions,
        related_eval_cases=related,
    )


async def _related_eval_cases(
    conn: asyncpg.Connection,
    *,
    skill_id: str,
    kind: str,
    workflow_id: str | None,
) -> list[SkillRelatedEvalCase]:
    """Surface the eval cases this skill is on the hook for.

    Instruction skills (W7 slice 9 / 7.1.10): retention-violation cases
    on the bound workflow. Code skills (W7 slice 10 / 7.1.11): cases
    whose cluster spawned the iteration that promoted a proposal on
    this skill. Both reduce to "what test cases moved when this
    skill changed" for the demo flow.
    """
    if workflow_id is None:
        return []

    if kind == "instruction":
        rows = await conn.fetch(
            """
            SELECT id, workflow_id, provenance::text AS provenance,
                   expected_behavior, is_test_fold, created_at
            FROM eval_cases
            WHERE workflow_id = $1
              AND provenance = 'retention-violation'
            ORDER BY created_at DESC
            LIMIT $2
            """,
            workflow_id,
            _RELATED_EVAL_CASES_LIMIT,
        )
    else:
        # Code / composite skills: cases linked to clusters that spawned
        # iterations which proposed against this skill. The join chain:
        # eval_cases.cluster_id -> iterations.cluster_id -> proposals.skill_id.
        rows = await conn.fetch(
            """
            SELECT DISTINCT
                ec.id, ec.workflow_id, ec.provenance::text AS provenance,
                ec.expected_behavior, ec.is_test_fold, ec.created_at
            FROM eval_cases ec
            JOIN iterations i ON i.cluster_id = ec.cluster_id
            JOIN proposals  p ON p.iteration_id = i.id
            WHERE p.skill_id = $1
              AND ec.cluster_id IS NOT NULL
            ORDER BY ec.created_at DESC
            LIMIT $2
            """,
            skill_id,
            _RELATED_EVAL_CASES_LIMIT,
        )

    return [
        SkillRelatedEvalCase(
            id=r["id"],
            workflow_id=r["workflow_id"],
            provenance=r["provenance"],
            expected_behavior=decode_jsonb_obj(r["expected_behavior"]),
            is_test_fold=bool(r["is_test_fold"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


def _row_to_skill_summary(row: asyncpg.Record) -> SkillSummary:
    return SkillSummary(
        id=row["id"],
        kind=row["kind"],
        workflow_id=row["workflow_id"],
        capability_tags=list(row["capability_tags"] or []),
        head_version_id=row["head_version_id"],
        head_version_seq=row["head_version_seq"],
        head_created_at=row["head_created_at"],
    )


