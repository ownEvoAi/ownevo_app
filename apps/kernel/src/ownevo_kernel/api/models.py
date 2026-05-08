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


class GateResultCases(_Strict):
    """W5.1 — per-eval-case breakdown for the proposal sidebar.

    Reconstructed from the `gate-run-started` + `gate-run-completed`
    audit payloads (`prior_eval_task_ids`, `failed_prior_task_ids`,
    `promotable_task_ids`); no schema change required. Only the gate's
    own task-id strings are exposed — they double as the human-readable
    case label since the benchmark code (m5, labour, synthetic) mints
    them as meaningful identifiers, not UUIDs.

    `passed` counts cases the prior suite kept passing under the
    candidate. `regressed` is non-empty only on FAIL_REGRESSION.
    `newly_admitted` is non-empty only on PASS (the W3 cluster→eval-case
    promotion list). `unknown` is True when the gate hasn't completed
    (rare race — UI should hide the breakdown).
    """

    passed: list[str]
    regressed: list[str]
    newly_admitted: list[str]
    unknown: bool = False


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
    gate_result_cases: GateResultCases | None


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


# ---------------------------------------------------------------------------
# Workflow-list + lift-chart endpoints (W7 slice 2)
# ---------------------------------------------------------------------------


class WorkflowSummary(_Strict):
    """Row in the Health page's workflow-rows table.

    Joined across `workflows` + `iterations` + `proposals` so the Health
    page renders without N+1 fetches. `iteration_count` is the number of
    finalized iterations on the workflow (gate-pass / gate-blocked / etc.
    — anything not 'running'), driving the right-side "Last improved"
    cell. `pending_proposals_count` covers gate-passed proposals waiting
    for human/llm-judge approval.
    """

    id: str
    description: str
    mode: str  # 'gated' | 'autonomous'
    iteration_count: int
    best_ever_score: float | None
    last_improved_at: datetime | None  # most recent approved proposal's state_updated_at
    pending_proposals_count: int


class WorkflowList(_Strict):
    items: list[WorkflowSummary]
    total: int


class IterationPoint(_Strict):
    """One point on the lift chart.

    Iteration-keyed (not day-keyed) — every iteration is a point per
    W7_SLICE.md resolved decision. `has_approved_proposal` drives the
    annotated-dot overlay; `state` lets the UI distinguish gate-pass
    from gate-blocked-no-improvement vs sandbox-error visually.
    """

    iteration_index: int
    val_score: float | None
    best_ever_score_after: float | None
    state: str
    has_approved_proposal: bool
    ended_at: datetime | None


class IterationList(_Strict):
    workflow_id: str
    items: list[IterationPoint]


# ---------------------------------------------------------------------------
# Failure clusters (W7 slice 3)
# ---------------------------------------------------------------------------


class FailureClusterSummary(_Strict):
    """Card-shaped row for the workflow Failures view.

    `centroid` is omitted intentionally — 384 floats per row blow up
    the JSON payload and the UI doesn't render the embedding. If a
    debug view ever needs it, add a separate detail endpoint.
    """

    id: UUID
    workflow_id: str | None
    label: str
    severity: str  # 'high' | 'medium' | 'low'
    cluster_size: int
    label_eval_score: float | None
    quality_score: float | None
    sample_trace_ids: list[UUID]
    created_at: datetime


class FailureClusterList(_Strict):
    workflow_id: str
    items: list[FailureClusterSummary]


__all__ = [
    "ApprovalDetail",
    "ApproveResponse",
    "AuditEntry",
    "DecideRequest",
    "FailureClusterList",
    "FailureClusterSummary",
    "GateResultCases",
    "HealthResponse",
    "IterationDetail",
    "IterationList",
    "IterationPoint",
    "ProposalDetail",
    "ProposalList",
    "ProposalSummary",
    "WorkflowDetail",
    "WorkflowList",
    "WorkflowSummary",
]
