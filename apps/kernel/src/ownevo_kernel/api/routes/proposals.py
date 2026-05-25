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

import asyncio
import logging
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ...approvals import (
    ApprovalStateError,
    ProposalNotFoundError,
    approve_proposal,
    deploy_proposal,
    reject_proposal,
    request_changes_proposal,
    rollback_proposal,
)
from ...audit import append_audit_entry
from ...proposals.ordering_inversion import (
    check_metric_ordering_inversion,
)
from ...proposals.ordering_inversion import (
    to_api_dict as inversion_check_to_dict,
)
from ...types import ApproverType, AuditKind
from .._integration_credentials import get_credential_plaintext
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

_log = logging.getLogger(__name__)


def _safe_commit_url(url: str) -> str:
    """Return `url` unchanged only when it starts with `https://`; else `""`.

    Prevents XSS via `javascript:` hrefs in LangSmith commit URLs.  Applied
    both to freshly-received push results and to audit-log readbacks so that
    entries written before the guard was added are also sanitised on egress.
    """
    return url if url.startswith("https://") else ""


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
            p.kind::text AS kind,
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
            p.kind::text AS kind,
            p.parent_version_id,
            p.proposed_content,
            p.proposed_payload,
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
        kind=proposal_row["kind"],
        parent_version_id=proposal_row["parent_version_id"],
        state=proposal_row["state"],
        proposed_content=proposal_row["proposed_content"],
        proposed_payload=decode_jsonb_obj(proposal_row["proposed_payload"]),
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
# 9.2.3 — ordering-inversion check for kind='metric' proposals
# ---------------------------------------------------------------------------


@router.get("/{proposal_id}/ordering-inversion-check")
async def get_metric_ordering_inversion_check(
    proposal_id: UUID,
    conn: ConnDep,
) -> dict[str, Any]:
    """Re-score every prior iteration under the proposed metric and
    return per-iteration deltas + inversion flags.

    Only meaningful for kind='metric' proposals. Returns a body shaped
    by `proposals.ordering_inversion.to_api_dict`. The proposal-detail
    surface renders this above the approval form so the reviewer sees
    the consequence of approving the metric change before they click.

    404 when the proposal id doesn't exist; 422 when the proposal is
    not kind='metric'; otherwise 200 with `status='ok'` /
    `'unavailable'` describing whether the check could run.
    """
    proposal_row = await conn.fetchrow(
        """
        SELECT p.id, p.kind::text AS kind, p.proposed_payload,
               i.workflow_id
        FROM proposals p
        JOIN iterations i ON i.id = p.iteration_id
        WHERE p.id = $1
        """,
        proposal_id,
    )
    if proposal_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal {proposal_id} not found",
        )
    if proposal_row["kind"] != "metric":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Ordering-inversion check applies to kind='metric' "
                "proposals only."
            ),
        )

    proposed = decode_jsonb_obj(proposal_row["proposed_payload"]) or {}

    result = await check_metric_ordering_inversion(
        conn,
        workflow_id=proposal_row["workflow_id"],
        proposed_metric_payload=proposed,
    )
    return inversion_check_to_dict(result)


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


class ShipLangSmithRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipped_by: str = Field(default="human", max_length=128)


class ShipLangSmithResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    prompt_id: str
    commit_hash: str
    commit_url: str
    already_shipped: bool


@router.post(
    "/{proposal_id}/ship-langsmith",
    response_model=ShipLangSmithResponse,
    status_code=status.HTTP_200_OK,
)
async def ship_langsmith(
    proposal_id: UUID,
    body: ShipLangSmithRequest,
    conn: ConnDep,
    _: DemoModeCheck,
) -> ShipLangSmithResponse:
    """Push a deployed fix back to the customer's LangSmith workspace.

    Preconditions (422 unless met): the proposal is `kind='skill'`, in
    state `deployed`, on a workflow with `origin='langsmith'`, whose
    skill carries a `langsmith_prompt_id`. A configured LangSmith
    credential is required (424 when absent). 503 when the server-side
    master encryption key is not configured.

    Idempotent: if this proposal already has a `fix-shipped-langsmith`
    audit entry, the existing commit is returned (`already_shipped=true`)
    without a second push. Adapter failures map to their HTTP status and
    do NOT roll back the deploy — retry is just another POST.
    """
    row = await conn.fetchrow(
        """
        SELECT p.state::text   AS state,
               p.kind::text     AS kind,
               p.skill_id,
               p.plain_language_summary,
               i.workflow_id,
               w.origin         AS workflow_origin,
               s.langsmith_prompt_id,
               sv.content       AS deployed_content
        FROM proposals p
        JOIN iterations i ON i.id = p.iteration_id
        JOIN workflows w ON w.id = i.workflow_id
        LEFT JOIN skills s ON s.id = p.skill_id
        LEFT JOIN skill_versions sv ON sv.id = s.deployed_version_id
        WHERE p.id = $1
        """,
        proposal_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Proposal not found")

    # Idempotency: a prior successful ship left an audit row.
    existing = await conn.fetchrow(
        "SELECT payload FROM audit_entries "
        "WHERE kind = 'fix-shipped-langsmith' AND related_id = $1 "
        "ORDER BY created_at DESC LIMIT 1",
        proposal_id,
    )
    if existing is not None:
        payload = decode_jsonb_obj(existing["payload"])
        return ShipLangSmithResponse(
            proposal_id=proposal_id,
            prompt_id=payload.get("prompt_id", ""),
            commit_hash=payload.get("commit_hash", ""),
            # Re-apply the https:// guard: audit entries written before the
            # sanitisation was added may carry non-https URLs.
            commit_url=_safe_commit_url(payload.get("commit_url", "")),
            already_shipped=True,
        )

    if (row["kind"] or "skill") != "skill":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only skill proposals can ship to LangSmith",
        )
    if row["state"] != "deployed":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Proposal must be deployed before shipping",
        )
    if row["workflow_origin"] != "langsmith":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Workflow is not LangSmith-originated",
        )
    prompt_id = row["langsmith_prompt_id"]
    if not prompt_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Skill has no langsmith_prompt_id; bind it first",
        )
    content = row["deployed_content"]
    if not content:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No deployed skill version to ship",
        )

    from ...secrets import CredentialsDecryptError, CredentialsKeyMissingError

    try:
        api_key = await get_credential_plaintext(conn, "langsmith")
    except CredentialsKeyMissingError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential encryption key is not configured on this server",
        ) from None
    except CredentialsDecryptError:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored credential could not be decrypted — re-enter the key in Settings",
        ) from None
    if api_key is None:
        raise HTTPException(
            status.HTTP_424_FAILED_DEPENDENCY,
            detail="No LangSmith credential configured",
        )

    from ...middleware.langsmith_push import (
        LangSmithAuthError,
        LangSmithConflictError,
        LangSmithNetworkError,
        LangSmithNotFoundError,
        LangSmithPushError,
        LangSmithRateLimitError,
        push_fix,
    )

    summary = row["plain_language_summary"] or "Approved fix"

    # Advisory lock prevents concurrent requests from both calling push_fix
    # (which is not idempotent — each call creates a new LangSmith commit).
    # The lock is transaction-scoped so it auto-releases on commit or rollback;
    # pg_advisory_xact_lock blocks until the lock is free (bounded by the
    # LangSmith timeout on the competing request's push_fix call, ≤ 30 s).
    # Using `int.from_bytes(uuid.bytes[:8])` gives a deterministic 64-bit key
    # per proposal; UUID v4 entropy makes collisions negligible.
    lock_key = int.from_bytes(proposal_id.bytes[:8], "big", signed=True)

    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

        # Re-check idempotency inside the lock: a concurrent request that
        # finished between our outer check and the lock acquisition would
        # already have written the audit entry.
        locked_existing = await conn.fetchrow(
            "SELECT payload FROM audit_entries "
            "WHERE kind = 'fix-shipped-langsmith' AND related_id = $1 "
            "ORDER BY created_at DESC LIMIT 1",
            proposal_id,
        )
        if locked_existing is not None:
            locked_payload = decode_jsonb_obj(locked_existing["payload"])
            return ShipLangSmithResponse(
                proposal_id=proposal_id,
                prompt_id=locked_payload.get("prompt_id", ""),
                commit_hash=locked_payload.get("commit_hash", ""),
                commit_url=_safe_commit_url(locked_payload.get("commit_url", "")),
                already_shipped=True,
            )

        try:
            result = await asyncio.to_thread(
                push_fix,
                api_key=api_key,
                prompt_id=prompt_id,
                instruction_text=content,
                commit_description=summary,
            )
        except LangSmithAuthError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)[:200]) from exc
        except LangSmithNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)[:200]) from exc
        except LangSmithConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)[:200]) from exc
        except LangSmithRateLimitError as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)[:200]
            ) from exc
        except (LangSmithNetworkError, LangSmithPushError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)[:200]) from exc

        # Sanitise the URL before writing it to the append-only audit log and
        # returning it to the browser (where it becomes an <a href>). React does
        # not strip javascript: hrefs; a self-hosted or compromised LangSmith
        # server could inject a XSS payload that executes on click.
        raw_url = result.commit_url
        commit_url = _safe_commit_url(raw_url)
        if commit_url != raw_url:
            _log.warning(
                "ship-langsmith: push_fix returned unexpected commit_url scheme %r; "
                "clearing URL from response to prevent XSS",
                raw_url[:60],
            )

        # Write audit inside a savepoint so a UniqueViolationError (theoretically
        # unreachable with the advisory lock, but kept for defence-in-depth) does
        # not abort the outer transaction.
        try:
            async with conn.transaction():  # savepoint
                await append_audit_entry(
                    conn,
                    kind=AuditKind.FIX_SHIPPED_LANGSMITH,
                    payload={
                        "prompt_id": result.prompt_id,
                        "commit_hash": result.commit_hash,
                        "commit_url": commit_url,
                        "workflow_id": row["workflow_id"],
                    },
                    actor=body.shipped_by,
                    related_id=proposal_id,
                )
        except asyncpg.UniqueViolationError as exc:
            # Belt-and-suspenders: advisory lock makes this path unreachable for
            # concurrent requests, but guard against any future non-locking code
            # path or direct DB writes. Only swallow our own index's violation.
            if getattr(exc, "constraint_name", None) != "audit_entries_ship_langsmith_once_idx":
                raise
            winner = await conn.fetchrow(
                "SELECT payload FROM audit_entries "
                "WHERE kind = 'fix-shipped-langsmith' AND related_id = $1 "
                "ORDER BY created_at DESC LIMIT 1",
                proposal_id,
            )
            if winner is not None:
                winner_payload = decode_jsonb_obj(winner["payload"])
                return ShipLangSmithResponse(
                    proposal_id=proposal_id,
                    prompt_id=winner_payload.get("prompt_id", ""),
                    commit_hash=winner_payload.get("commit_hash", ""),
                    commit_url=_safe_commit_url(winner_payload.get("commit_url", "")),
                    already_shipped=True,
                )
            # Unique violation but no row found — winning transaction rolled back.
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="LangSmith push succeeded but audit write failed — retry to re-record",
            ) from exc

    return ShipLangSmithResponse(
        proposal_id=proposal_id,
        prompt_id=result.prompt_id,
        commit_hash=result.commit_hash,
        commit_url=commit_url,
        already_shipped=False,
    )


class ShipCopilotStudioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipped_by: str = Field(default="human", max_length=128)


class ShipCopilotStudioResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    summary: str
    instruction_text: str
    already_delivered: bool


@router.post(
    "/{proposal_id}/ship-copilot-studio",
    response_model=ShipCopilotStudioResponse,
    status_code=status.HTTP_200_OK,
)
async def ship_copilot_studio(
    proposal_id: UUID,
    body: ShipCopilotStudioRequest,
    conn: ConnDep,
    _: DemoModeCheck,
) -> ShipCopilotStudioResponse:
    """Deliver a deployed fix to a Copilot Studio workflow as a diff to apply.

    Microsoft exposes no programmatic fix-feedback API, so unlike
    `ship-langsmith` this makes **no external call**: it records the
    plain-language summary + the new instruction text to the audit chain
    (`fix-exported-copilot-studio`) for the customer to apply by hand in
    the Copilot Studio UI, and returns that text for the approval card to
    render.

    Preconditions (422 unless met): the proposal is `kind='skill'`, in
    state `deployed`, on a workflow with `origin='copilot_studio'`, with a
    deployed skill version to deliver.

    Idempotent: a prior `fix-exported-copilot-studio` audit entry is
    returned (`already_delivered=true`) without re-recording. The partial
    unique index (migration 0027) makes the concurrent-double-deliver race
    fail closed; the savepoint below converts that into the idempotent
    repeat.
    """
    row = await conn.fetchrow(
        """
        SELECT p.state::text   AS state,
               p.kind::text     AS kind,
               p.plain_language_summary,
               i.workflow_id,
               w.origin         AS workflow_origin,
               sv.content       AS deployed_content
        FROM proposals p
        JOIN iterations i ON i.id = p.iteration_id
        JOIN workflows w ON w.id = i.workflow_id
        LEFT JOIN skills s ON s.id = p.skill_id
        LEFT JOIN skill_versions sv ON sv.id = s.deployed_version_id
        WHERE p.id = $1
        """,
        proposal_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Proposal not found")

    existing = await conn.fetchrow(
        "SELECT payload FROM audit_entries "
        "WHERE kind = $2::audit_kind AND related_id = $1 "
        "ORDER BY created_at DESC LIMIT 1",
        proposal_id,
        AuditKind.FIX_EXPORTED_COPILOT_STUDIO.value,
    )
    if existing is not None:
        payload = decode_jsonb_obj(existing["payload"])
        return ShipCopilotStudioResponse(
            proposal_id=proposal_id,
            summary=payload.get("summary", ""),
            instruction_text=payload.get("instruction_text", ""),
            already_delivered=True,
        )

    if (row["kind"] or "skill") != "skill":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only skill proposals can be delivered to Copilot Studio",
        )
    if row["state"] != "deployed":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Proposal must be deployed before delivering",
        )
    if row["workflow_origin"] != "copilot_studio":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Workflow is not Copilot Studio-originated",
        )
    content = row["deployed_content"]
    if not content:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No deployed skill version to deliver",
        )

    summary = row["plain_language_summary"] or "Approved fix"

    # No external side effect to dedupe (unlike ship-langsmith's push), so
    # the partial unique index alone guards the concurrent-double-deliver
    # race. Write inside a savepoint so the unique violation is recoverable.
    try:
        async with conn.transaction():  # savepoint
            await append_audit_entry(
                conn,
                kind=AuditKind.FIX_EXPORTED_COPILOT_STUDIO,
                payload={
                    "summary": summary,
                    "instruction_text": content,
                    "workflow_id": row["workflow_id"],
                },
                actor=body.shipped_by,
                related_id=proposal_id,
            )
    except asyncpg.UniqueViolationError as exc:
        if getattr(exc, "constraint_name", None) != "audit_entries_ship_copilot_studio_once_idx":
            raise
        winner = await conn.fetchrow(
            "SELECT payload FROM audit_entries "
            "WHERE kind = $2::audit_kind AND related_id = $1 "
            "ORDER BY created_at DESC LIMIT 1",
            proposal_id,
            AuditKind.FIX_EXPORTED_COPILOT_STUDIO.value,
        )
        if winner is not None:
            winner_payload = decode_jsonb_obj(winner["payload"])
            return ShipCopilotStudioResponse(
                proposal_id=proposal_id,
                summary=winner_payload.get("summary", ""),
                instruction_text=winner_payload.get("instruction_text", ""),
                already_delivered=True,
            )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Audit write conflicted but no entry found — retry to re-record",
        ) from exc

    return ShipCopilotStudioResponse(
        proposal_id=proposal_id,
        summary=summary,
        instruction_text=content,
        already_delivered=False,
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

    skill_deployed_version_id: UUID | None = None
    if proposal.skill_id is not None:
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
    # `kind` was added in migration 0017. Other callers of this helper
    # (legacy fixtures, partial unit-test rows) may not select it; fall
    # back to the historical 'skill' value rather than KeyError.
    kind_val: str
    try:
        kind_val = row["kind"]
    except (KeyError, IndexError):
        kind_val = "skill"
    return ProposalSummary(
        id=row["id"],
        iteration_id=row["iteration_id"],
        iteration_index=row["iteration_index"],
        skill_id=row["skill_id"],
        kind=kind_val,
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
