"""ownEvo kernel domain types.

Mirror of `apps/kernel/migrations/0001_substrate.sql` schema. Source-of-truth
for the schema is the SQL; these Pydantic models are how Python code reads
and writes that schema. JSONB fields use plain `dict[str, Any]` typing here;
typed sub-models live alongside the consumers (e.g., `gate/result.py`).

Single-tenant for MVP per D4 — no `workspace_id` field. Phase-2 retrofit
will add it across every model in this file.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums (mirror the SQL enums in 0001_substrate.sql)
# ---------------------------------------------------------------------------


class SkillKind(StrEnum):
    PYTHON = "python"
    INSTRUCTION = "instruction"
    COMPOSITE = "composite"


class ProvenanceKind(StrEnum):
    HAND_AUTHORED = "hand-authored"
    CLUSTER_DERIVED = "cluster-derived"
    NL_GEN = "nl-gen"
    RETENTION_VIOLATION = "retention-violation"
    REJECTED_FEEDBACK = "rejected-feedback"


class IterationState(StrEnum):
    RUNNING = "running"
    GATE_PASS = "gate-pass"
    GATE_BLOCKED_REGRESSION = "gate-blocked-regression"
    GATE_BLOCKED_NO_IMPROVEMENT = "gate-blocked-no-improvement"
    SANDBOX_ERROR = "sandbox-error"


class WorkflowMode(StrEnum):
    GATED = "gated"
    AUTONOMOUS = "autonomous"


class ProposalState(StrEnum):
    PENDING = "pending"
    IN_GATE = "in-gate"
    GATE_FAILED = "gate-failed"
    GATE_PASSED = "gate-passed"
    REJECTED = "rejected"
    APPROVED_AWAITING_DEPLOY = "approved-awaiting-deploy"
    DEPLOYED = "deployed"
    ROLLED_BACK = "rolled-back"


class ApproverType(StrEnum):
    HUMAN = "human"
    LLM_JUDGE = "llm-judge"
    AUTONOMOUS = "autonomous"


class SandboxErrorClass(StrEnum):
    """D3 — sandbox runtime failures.

    Distinct from logical tool errors. The gate runner does NOT advance
    `best_ever_score` when an iteration ends in any of these.
    """

    TIMEOUT = "Timeout"
    OOM = "OOM"
    CRASH = "Crash"


class AuditKind(StrEnum):
    """Every state change in proposals/iterations/skills/clusters/etc. writes
    an audit_entries row with one of these kinds. Keep in sync with
    `0001_substrate.sql`."""

    SKILL_VERSION_CREATED = "skill-version-created"
    GATE_RUN_STARTED = "gate-run-started"
    GATE_RUN_COMPLETED = "gate-run-completed"
    PROPOSAL_CREATED = "proposal-created"
    PROPOSAL_APPROVED = "proposal-approved"
    PROPOSAL_REJECTED = "proposal-rejected"
    PROPOSAL_DEPLOYED = "proposal-deployed"
    PROPOSAL_ROLLED_BACK = "proposal-rolled-back"
    EVAL_CASE_ADDED = "eval-case-added"
    CLUSTER_CREATED = "cluster-created"
    CLUSTER_RELABELED = "cluster-relabeled"
    WORKFLOW_CREATED = "workflow-created"
    META_EVAL_RESULT = "meta-eval-result"
    SCHEMA_MIGRATION = "schema-migration"
    DEPLOYMENT_CREATED = "deployment-created"
    DEPLOYMENT_UPDATED = "deployment-updated"


# ---------------------------------------------------------------------------
# Core entities
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    """Common Pydantic config for kernel domain types."""

    model_config = ConfigDict(extra="forbid")


class Workflow(_Base):
    """The user's described workflow + NL-gen-generated artifacts.

    `spec` is the frozen-schema JSONB shape from the NL-gen pipeline;
    typed sub-models for it live in `nl_gen/spec.py` (W3).
    """

    id: str
    description: str
    spec: dict[str, Any]
    metric_id: str | None = None
    sim_skill_id: str | None = None
    meta_eval_score: float | None = Field(default=None, ge=0.0, le=1.0)
    mode: WorkflowMode = WorkflowMode.GATED
    created_at: datetime


class Skill(_Base):
    id: str
    kind: SkillKind
    head_version_id: UUID | None = None
    workflow_id: str | None = None
    capability_tags: list[str] = Field(default_factory=list)
    created_at: datetime


class SkillVersion(_Base):
    id: UUID
    skill_id: str
    parent_version_id: UUID | None = None
    version_seq: int = Field(ge=1)
    content: str
    retention_block: dict[str, Any] | None = None
    diff_summary: str | None = None
    created_at: datetime
    created_by: str  # "agent:<model_id>" | "human:<id>" | "nl-gen"


class EvalCase(_Base):
    id: UUID
    workflow_id: str | None = None
    provenance: ProvenanceKind
    cluster_id: UUID | None = None
    input: dict[str, Any]
    expected_behavior: dict[str, Any]
    regression_tolerance: float | None = None
    is_test_fold: bool = False
    created_at: datetime


class FailureCluster(_Base):
    id: UUID
    workflow_id: str | None = None
    label: str
    label_eval_score: float | None = Field(default=None, ge=0.0, le=1.0)
    severity: Literal["high", "medium", "low"]
    # pgvector vector(384) — sentence-transformers/all-MiniLM-L6-v2 dim.
    # Most kernel readers don't need the centroid (similarity ops happen in SQL
    # via pgvector), but exposing it here lets `SELECT *` round-trip cleanly
    # under `extra="forbid"` and unblocks any consumer that needs the embedding.
    centroid: list[float] | None = None
    sample_trace_ids: list[UUID] = Field(default_factory=list)
    cluster_size: int = Field(ge=1)
    quality_score: float | None = None
    created_at: datetime


class SkillDeployment(_Base):
    """A named deployment config for a skill — same content, different runtime.

    Enables A/B testing and per-model comparison: deploy the same skill under
    'control' (sonnet, temp=0.7) and 'opus-low-temp' (opus, temp=0.2) simultaneously.
    Iterations reference deployment_id so the lift chart separates variant lines.
    `run_config` keys: temperature, tools, system_prompt_override, timeout_ms.
    """

    id: UUID
    skill_id: str
    config_tag: str
    model_id: str
    run_config: dict[str, Any] = Field(default_factory=dict)
    traffic_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    is_active: bool = True
    created_at: datetime


class Iteration(_Base):
    id: UUID
    workflow_id: str
    iteration_index: int = Field(ge=0)
    proposed_skill_version_id: UUID | None = None
    parent_skill_version_id: UUID | None = None
    state: IterationState = IterationState.RUNNING
    sandbox_error_class: SandboxErrorClass | None = None
    val_score: float | None = None
    best_ever_score_before: float | None = None
    best_ever_score_after: float | None = None
    cluster_id: UUID | None = None
    deployment_id: UUID | None = None
    token_budget_used: int | None = None
    token_budget_total: int | None = None
    started_at: datetime
    ended_at: datetime | None = None


class Proposal(_Base):
    """A proposed skill change pending decision.

    Maps to `proposals` table. State machine documented in
    `docs/STATE_MACHINES.md`.

    Greenfield-rewrite of the `core/agentos_harness/types.py:Proposal` shape.
    Borrows: `eval_score` + `eval_rationale` for LLM-judge integration.
    Diverges: text → plain_language_summary, evidence_count → expected_impact,
    raw_pattern dropped (clusters live separately), state machine added.
    """

    id: UUID
    iteration_id: UUID
    skill_id: str
    parent_version_id: UUID | None = None
    proposed_content: str
    plain_language_summary: str
    expected_impact: dict[str, Any] | None = None
    state: ProposalState = ProposalState.PENDING
    eval_score: float | None = Field(default=None, ge=0.0, le=1.0)
    eval_rationale: str | None = None
    created_at: datetime
    state_updated_at: datetime


class ProposalAction(_Base):
    """Structured executable action for a proposal.

    Shape inspired by `core/agentos_harness/types.py:ProposalAction` (MIT);
    greenfield implementation. Discriminator-based pattern preserved;
    ownEvo-specific extension: `regression_gate` action type per D6 — gate
    outcomes flow through the same proposal pipeline as skill mutations.
    """

    action_type: Literal[
        "workflow_update",
        "tool_priority",
        "prompt_refinement",
        "config_update",
        "regression_gate",  # D6 — eng review extension
    ]
    target: str
    value: Any
    reason: str = ""


class Approval(_Base):
    id: UUID
    proposal_id: UUID
    decided_by: str  # "human:<id>" | "llm-judge-stub" | "autonomous"
    approver_type: ApproverType
    decision: Literal["approve", "reject"]
    comment: str | None = None
    became_eval_case_id: UUID | None = None
    decided_at: datetime


class AuditEntry(_Base):
    """Append-only WORM (D2). Read-only here; writes go through the
    audit module which enforces canonical-JSON serialization.
    """

    id: UUID
    seq: int
    kind: AuditKind
    payload: dict[str, Any]
    related_id: UUID | None = None
    actor: str
    created_at: datetime


class MetaEvalResult(_Base):
    """NL-gen meta-eval output (D7).

    Stored in `meta_evals` table; coverage_score becomes the "sim covers
    11/12 of your description" badge in the workspace UI.
    """

    id: UUID
    workflow_id: str
    description: str
    coverage_score: float = Field(ge=0.0, le=1.0)
    per_dimension: dict[str, Any]
    judge_model: str
    passed_threshold: bool
    created_at: datetime


class Trace(_Base):
    """An AgentEvent stream from one agent run.

    `events` is `list[AgentEvent]` from `ownevo-trace-format`; using
    `list[dict]` here to avoid forcing every kernel reader to import
    Pydantic discriminated-union machinery. Canonical typing happens at
    the trace-pipeline boundary (W1.5).
    """

    id: UUID
    workflow_id: str | None = None
    iteration_id: UUID | None = None
    skill_version_id: UUID | None = None
    events: list[dict[str, Any]]
    started_at: datetime
    ended_at: datetime | None = None
    metric_outputs: dict[str, Any] | None = None
    token_usage: dict[str, Any] | None = None


class Learning(_Base):
    """Mirrors auto-harness's `learnings.md` append-only file.

    Loop-stuck alert (W2.4a) fires when no Learning is created in 2h.
    """

    id: UUID
    iteration_id: UUID | None = None
    kind: Literal["hypothesis", "observation", "request-to-human", "failure-note"]
    content: str
    created_at: datetime
