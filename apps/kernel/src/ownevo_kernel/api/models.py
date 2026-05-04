"""Request/response models for the approval REST API.

Wire shapes are deliberately flat — the web app consumes raw JSON
without an ORM layer. Field names match the DB columns where possible,
JSON-friendly transforms (UUID → str, datetime → ISO 8601) come from
Pydantic's default mode='json' encoders.

Why mirror DB models instead of re-using them
---------------------------------------------
The kernel's Pydantic types (`Proposal`, `Iteration`, `Approval`, etc.)
are write-side models — they include fields the API doesn't need
(internal seq, raw payloads) and miss joined fields the API does need
(`workflow.description`, `iteration_index`, `parent_version_content`).
Defining HTTP-shaped DTOs here keeps the API contract stable when DB
columns are added or renamed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


class ProposalSummary(_Strict):
    """Row in the approval queue list view.

    Joined across `proposals` + `iterations` + `workflows` so the inbox
    page renders without N+1 fetches.
    """

    id: UUID
    iteration_id: UUID
    iteration_index: int
    skill_id: str
    workflow_id: str
    workflow_description: str
    state: str
    plain_language_summary: str
    eval_score: float | None
    eval_rationale: str | None
    expected_impact: dict[str, Any] | None
    created_at: datetime
    state_updated_at: datetime


class ProposalList(_Strict):
    items: list[ProposalSummary]
    total: int


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


class IterationDetail(_Strict):
    id: UUID
    iteration_index: int
    state: str
    val_score: float | None
    best_ever_score_before: float | None
    best_ever_score_after: float | None
    sandbox_error_class: str | None
    started_at: datetime
    ended_at: datetime | None


class WorkflowDetail(_Strict):
    id: str
    description: str
    mode: str


class AuditEntry(_Strict):
    id: UUID
    seq: int
    kind: str
    actor: str
    payload: dict[str, Any]
    created_at: datetime


class ApprovalDetail(_Strict):
    id: UUID
    decided_by: str
    approver_type: str
    decision: str
    comment: str | None
    became_eval_case_id: UUID | None
    decided_at: datetime


class ProposalDetail(_Strict):
    id: UUID
    iteration_id: UUID
    skill_id: str
    parent_version_id: UUID | None
    state: str
    proposed_content: str
    parent_version_content: str | None  # null on the bootstrap iteration
    parent_version_seq: int | None
    plain_language_summary: str
    eval_score: float | None
    eval_rationale: str | None
    expected_impact: dict[str, Any] | None
    created_at: datetime
    state_updated_at: datetime
    iteration: IterationDetail
    workflow: WorkflowDetail
    audit_entries: list[AuditEntry]
    approval: ApprovalDetail | None


# ---------------------------------------------------------------------------
# Approve / reject endpoints
# ---------------------------------------------------------------------------


class DecideRequest(BaseModel):
    """Common body for approve + reject. The path verb already encodes
    the decision; the body carries the actor + optional comment."""

    model_config = ConfigDict(extra="forbid")

    decided_by: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Convention: 'human:<userid>' for human, 'llm-judge' for the "
            "LLM-judge variant, 'autonomous' for the workflow.mode='autonomous' "
            "auto-approve path."
        ),
    )
    comment: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "Optional reviewer comment. On reject + non-empty comment, the "
            "comment becomes the `expected_behavior.note` of a new eval case "
            "with provenance='rejected-feedback' (linked via "
            "approvals.became_eval_case_id)."
        ),
    )
    approver_type: str | None = Field(
        default=None,
        description=(
            "Optional override; defaults to 'human'. Valid: 'human' | 'llm-judge' "
            "| 'autonomous'. The autonomous path is reserved for "
            "workflow.mode='autonomous' callers — UI clients should not set this."
        ),
    )


class ApproveResponse(_Strict):
    proposal_id: UUID
    state: str  # the new proposal state
    approval: ApprovalDetail


class HealthResponse(_Strict):
    status: str
    db: str  # 'ok' if the connection pool answers, else error class name


__all__ = [
    "ApprovalDetail",
    "ApproveResponse",
    "AuditEntry",
    "DecideRequest",
    "HealthResponse",
    "IterationDetail",
    "ProposalDetail",
    "ProposalList",
    "ProposalSummary",
    "WorkflowDetail",
]
