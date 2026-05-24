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
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


class ProposalSummary(_Strict):
    """Row in the approval queue list view.

    Joined across `proposals` + `iterations` + `workflows` so the inbox
    page renders without N+1 fetches.

    `kind` discriminates skill edits from non-skill artifact edits
    (description / metric / sim / ui-primitive). For non-skill kinds
    `skill_id` is null and the per-artifact payload lives on the
    detail endpoint.
    """

    id: UUID
    iteration_id: UUID
    iteration_index: int
    skill_id: str | None
    kind: str = "skill"
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
    skill_id: str | None
    kind: str = "skill"
    parent_version_id: UUID | None
    state: str
    proposed_content: str
    # Non-skill artifact payload (description / metric / sim /
    # ui-primitive). Null for kind='skill' where `proposed_content`
    # carries the new skill body.
    proposed_payload: dict[str, Any] | None = None
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


class DeployRequest(BaseModel):
    """Body for /deploy and /rollback. The path verb encodes the action;
    the body carries the actor for the audit log."""

    model_config = ConfigDict(extra="forbid")

    decided_by: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Convention: 'human:<userid>' for human, 'autonomous' for the "
            "workflow.mode='autonomous' auto-deploy path."
        ),
    )

    @field_validator("decided_by")
    @classmethod
    def decided_by_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("decided_by must not be blank")
        return v


class DeployResponse(_Strict):
    """Result of a deploy / rollback transition.

    `state` is the new proposal state ('deployed' | 'rolled-back').
    `skill_deployed_version_id` is the skill's production pointer after
    the transition — null after a rollback that left no prior deployment.
    """

    proposal_id: UUID
    state: Literal["deployed", "rolled-back"]
    skill_id: str
    skill_deployed_version_id: UUID | None


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
    cell. `running_iteration_count` counts iterations currently in the
    'running' state — drives the Health page in-flight indicator
    `pending_proposals_count` covers gate-passed proposals
    waiting for human/llm-judge approval.
    """

    id: str
    description: str
    mode: str  # 'gated' | 'autonomous'
    # 'benchmark' tags workflows that exist for kernel validation
    # (M5 forecasting, tau-bench replays) — they share the substrate
    # with customer workflows but the UI surfaces them in a separate
    # sidebar section so a domain expert isn't confused. NULL/absent
    # = production (default).
    kind: str | None = None
    iteration_count: int
    running_iteration_count: int = 0
    # When >0 running iterations, the oldest one's started_at. Lets the
    # Health page flag iterations that have been "running" for hours —
    # almost always a crashed/abandoned run that didn't get marked
    # sandbox-error (e.g., kernel killed mid-loop). Null when nothing is
    # in flight.
    oldest_running_started_at: datetime | None = None
    best_ever_score: float | None
    last_improved_at: datetime | None  # most recent approved proposal's state_updated_at
    pending_proposals_count: int


class WorkflowList(_Strict):
    items: list[WorkflowSummary]
    total: int


class EvalCaseProvenance(_Strict):
    """The `{kind, source}` shape NL-gen writes into
    `expected_behavior.provenance` (see `nl_gen/eval_persistence.py`).

    `kind="derived"` → `source` is a verbatim phrase from the user's
    `known_past_misses`. `kind="inferred"` → `source` is a named
    domain pattern the eval generator pulled in.
    """

    kind: Literal["derived", "inferred"]
    source: str


class EvalCaseSummary(_Strict):
    """One row on the workflow Eval cases page.

    Flattened from the `eval_cases` row: `case_id` / `target_label_field` /
    `expected_value` / `rationale` come from the `expected_behavior` JSONB
    (see `nl_gen/eval_persistence.py`). `sim_seed` / `n_steps` /
    `target_step_index` from `input`.

    `expected_behavior_provenance` surfaces the `{kind, source}` substructure
    so the new-workflow Step 2 review page (PLAN 8.4.11) can render the
    "derived from <user phrase>" caption per row. `category` is a coarse
    bucket derived server-side from `provenance.kind` so the UI can colour
    pills consistently without re-deriving the rule:

      derived  → 'past-miss'  (verbatim user-flagged miss)
      inferred → 'inferred'   (named domain pattern; regression / edge case)

    Both fields are `None` for hand-authored cases (no NL-gen provenance)
    and for legacy rows that pre-date the `expected_behavior.provenance`
    convention.
    """

    id: UUID
    case_id: str
    provenance: str
    rationale: str | None
    target_label_field: str | None
    expected_value: Any
    sim_seed: int | None
    n_steps: int | None
    target_step_index: int | None
    is_test_fold: bool
    cluster_id: UUID | None
    created_at: datetime
    expected_behavior_provenance: EvalCaseProvenance | None = None
    category: Literal["past-miss", "inferred"] | None = None


class EvalCaseList(_Strict):
    workflow_id: str
    items: list[EvalCaseSummary]
    total: int


class RunIterationResponse(_Strict):
    """Response from `POST /api/workflows/{id}/iterations/run`."""

    iteration_id: UUID
    iteration_index: int
    state: str
    val_score: float | None = None
    n_cases: int
    n_failed: int
    proposed_skill_id: str | None
    proposed_skill_version_id: UUID | None
    proposed_instruction: str | None
    proposal_id: UUID | None


class GenerateEvalCasesResponse(_Strict):
    """Response from `POST /api/workflows/{id}/eval-cases/generate`."""

    workflow_id: str
    generated: int
    train_count: int
    test_count: int


class TryItRequest(BaseModel):
    """Body for `POST /api/workflows/{id}/try` (PLAN 8.5.2).

    Exactly one of `eval_case_id` or `free_form_input` is accepted.
    First-cut supports `eval_case_id` only; `free_form_input` returns
    400 with a "not yet supported" message — the agent-solver path
    requires a full `GeneratedEvalCase` with a sim trajectory, which
    free-form text can't synthesise without inventing a synthetic case.
    """

    model_config = ConfigDict(extra="forbid")

    eval_case_id: UUID | None = None
    free_form_input: str | None = Field(default=None, max_length=4096)
    model: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Optional model override. Defaults to DEFAULT_MODEL in "
            "eval_runner.agent_solver (claude-haiku-4-5 today). Local "
            "LLM ids accepted; cost estimate falls through to 0.0 for "
            "unknown models."
        ),
    )


class TryItResponse(_Strict):
    """Response from `POST /api/workflows/{id}/try`.

    Mirrors `eval_runner.try_runner.TryItResult`. `trace` is a minimal
    `[tool_call_start, tool_call_result]` pair around the predict_one
    call; structure matches `packages/trace-format/AgentEvent` so the
    web's trace renderer can consume it unchanged.
    """

    case_id: str
    expected_value: Any
    actual_value: Any
    rationale: str
    passed: bool
    model: str
    duration_ms: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    trace: list[dict[str, Any]]


class WorkflowAnatomy(_Strict):
    """Full workflow detail backing the W7 slice 11 (7.1.12) anatomy pane.

    `spec` is the frozen-schema JSONB blob written by the NL-gen
    pipeline (`apps/kernel/src/ownevo_kernel/nl_gen/spec.py:WorkflowSpec`).
    The anatomy pane reads `spec.tools[*]`, `spec.reviewer`,
    `spec.environment`, and renders them alongside skills (separately
    fetched via `/api/workflows/{wf_id}/skills`). The DTO leaves the
    spec opaque (`dict[str, Any]`) so spec-version bumps don't break
    the API contract; the web app does its own field-by-field reads
    with sensible empty-state fallbacks.

    `simulation_plan` and `metric_definition` are the other two NL-gen
    artifacts persisted on the workflow row (jsonb columns from
    migration `0005_workflow_sim_metric.sql`). Both are null on rows
    that pre-date the generator pipeline. The new-workflow Step 2
    review page (PLAN 8.4.11) renders the metric definition (with its
    `provenance.source` provenance) so the reviewer sees the formula
    NL-gen derived from their description. `simulation_plan` is
    surfaced for completeness but the review UI doesn't render the
    raw step_code today — the rich `WorkflowSpec.tools / personas /
    env_generators / data_sources` arrays carry the user-facing
    surface, and SimulationPlan itself has no provenance layer.
    """

    id: str
    description: str
    mode: str
    kind: str | None = None  # 'benchmark' | null (production)
    spec: dict[str, Any]
    simulation_plan: dict[str, Any] | None = None
    metric_definition: dict[str, Any] | None = None
    # Vertical template id the workflow was created from, or None for
    # free-form authoring. Kebab-case slug matching an entry in
    # `apps/web/.../workflows/new/templates.ts`.
    created_from_template: str | None = None
    # Design-agent discovery transcript + ambiguity report (PLAN 9.1.4).
    # Persisted as JSONB on `workflows.design_agent_log` when the
    # operator ran the discovery interview before generation; null
    # otherwise. The web Audit tab renders this chronologically alongside
    # the matching audit_entries rows (`design-agent-negotiation` +
    # `design-agent-ambiguity` kinds).
    design_agent_log: dict[str, Any] | None = None
    # Per-workflow agent-model slug (`provider:model`). Stored as text
    # on `workflows.agent_model_id`; validated against the runtime-enabled
    # provider+model allowlist on PATCH. Phase 2 will thread this through
    # the iteration runner so the loop dispatches to the chosen provider.
    agent_model_id: str = "anthropic:claude-sonnet-4-6"


class EvalCaseCreate(_Strict):
    """Manual add payload for `POST /api/workflows/{id}/eval-cases`.

    Minimal surface — the operator types a case_id, expected bool, and
    optional rationale; the kernel fills in target_label_field +
    sim_seed/n_steps/target_step_index defaults so the gate can score
    the case via the standard replay path. Manual cases carry
    provenance='hand-authored' (D4: this is the human-seeded slot).
    """

    case_id: str = Field(min_length=1, max_length=128)
    expected_value: bool
    target_label_field: str = Field(min_length=1, max_length=128)
    rationale: str = Field(default="", max_length=1024)
    is_test_fold: bool = False
    sim_seed: int = Field(default=0, ge=0)
    n_steps: int = Field(default=1, ge=1)
    target_step_index: int = Field(default=0, ge=0)


class WorkflowUpdate(_Strict):
    """Patch payload for `PATCH /api/workflows/{id}`.

    Only fields the operator can safely change post-create. The NL-gen
    artifacts (`spec`, `simulation_plan`, `metric_definition`) are NOT
    editable from this endpoint — they regenerate via the dedicated
    generate endpoints so the cross-checks (workflow_spec_id agreement
    + meta-eval) stay enforced.
    """

    description: str = Field(min_length=10, max_length=4096)


class WorkflowAgentModelUpdate(_Strict):
    """Patch payload for `PATCH /api/workflows/{id}/agent-model`.

    Slug is `provider:model` — e.g. `anthropic:claude-sonnet-4-6`,
    `local:qwen/qwen3.6-35b-a3b`. Validated against the runtime-enabled
    provider+model allowlist in `kernel/llm/providers.py`.
    """

    agent_model_id: str = Field(min_length=3, max_length=256)


class ProviderModels(_Strict):
    """One entry in the `GET /api/models` response.

    `id` is the slug prefix. `label` is the human display string. `models`
    are the operator-enabled models for this provider.
    """

    id: str
    label: str
    models: list[str]


class ModelCatalog(_Strict):
    """Response for `GET /api/models`.

    Listed in the order declared in `kernel/llm/providers.PROVIDERS`.
    The web form renders each entry as an `<optgroup label={label}>`.
    """

    providers: list[ProviderModels]


class WorkflowDeleteResponse(_Strict):
    """Audit-trail receipt for a workflow hard-delete.

    Returns the row counts deleted per related table so the UI can show
    a meaningful confirmation ("removed 2 iterations, 24 traces, 3
    proposals, 1 skill version"). Audit entries are never touched (D2
    WORM); they keep their original `related_id` pointing at the
    now-deleted row, dangling but immutable.
    """

    id: str
    iterations: int
    proposals: int
    approvals: int
    traces: int
    eval_cases: int
    failure_clusters: int
    learnings: int
    skill_versions: int
    skills: int
    meta_evals: int


class IterationCaseRow(_Strict):
    """One eval case's outcome on one iteration.

    Sourced from the per-case `traces` row written by the iteration
    runner. The trace's `metric_outputs` JSONB carries the predicted /
    expected / passed flags inline; `case_id` is the workflow-local
    eval case identifier (matches `eval_cases` rows on case_id).
    `rationale` is the agent's per-case explanation (the second
    argument to the predict_label tool); None for legacy traces from
    before the rationale plumbing landed.
    """

    case_id: str
    predicted: bool | None
    expected: bool | None
    passed: bool | None
    is_test_fold: bool
    rationale: str | None = None
    trace_id: UUID
    started_at: datetime
    ended_at: datetime | None


class CaseOutputRow(_Strict):
    """One iteration_case_outputs row joined with its eval_case input.

    Drives the operator-shell TableView primitive (PLAN 8.4.10). Today
    `output_json` carries `{case_id, predicted, expected, rationale,
    is_test_fold}` — a thin shape mirroring what trace metric_outputs
    already holds. Once the agent solver gains a workflow-specific
    `submit_case_output` tool the same field carries recommendation
    tables / confidence scores / alerts and the TableView binding's
    column paths resolve directly against it.
    """

    eval_case_id: UUID
    case_id: str | None  # kebab-case id pulled out of eval_cases.expected_behavior
    output_json: dict[str, Any]
    expected_behavior: dict[str, Any]
    input: dict[str, Any]
    passed: bool
    is_test_fold: bool
    created_at: datetime
    # The trace row written by the iteration runner for this case on
    # this iteration, used by the operator-shell TableView to link
    # each row to /workspaces/{wsId}/traces/{trace_id} for the full
    # event-stream inspector. NULL only when the persistence path
    # raced or the case_id didn't resolve cleanly.
    trace_id: UUID | None = None
    # Domain-shaped output the agent emitted for this case (forecast
    # curve, redline pair, recommendation table, etc.). Operate-tab
    # renderer reads this and dispatches to the workflow-declared
    # primitives. NULL when the agent didn't emit one — Operate falls
    # back to its "no production output captured yet" empty state.
    output_payload: dict[str, Any] | None = None


class CaseOutputList(_Strict):
    """The latest iteration's per-case agent output, oldest-first.

    `iteration_index` echoes which iteration the rows came from so the
    caller can detect "asked for latest, got iteration #N." `items` is
    empty when no iteration has run yet — the operator shell renders
    the "Coming soon" banner in that state.
    """

    workflow_id: str
    iteration_index: int | None
    iteration_id: UUID | None
    items: list[CaseOutputRow]


class IterationDetailFull(_Strict):
    """One iteration with its full per-case outcome roster.

    Drives the lift-chart click-through (PLAN 8.4.8). Distinct from the
    legacy `IterationDetail` summary (id + state + score columns) —
    that one stays as-is for the per-iteration summary surface; this
    one carries the full case-level signal `traces` rows now hold.

    The `cases` list is ordered failed-first so the operator's eye
    lands on what regressed. `cluster_label` carries the dominant
    failure cluster's label when one anchored the iteration; None for
    clean runs.
    """

    workflow_id: str
    iteration_id: UUID
    iteration_index: int
    state: str
    val_score: float | None
    best_ever_score_before: float | None
    best_ever_score_after: float | None
    n_cases: int
    n_passed: int
    n_failed: int
    cluster_id: UUID | None
    cluster_label: str | None
    parent_skill_version_id: UUID | None
    proposed_skill_version_id: UUID | None
    proposal_id: UUID | None
    started_at: datetime
    ended_at: datetime | None
    cases: list[IterationCaseRow]


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

    `latest_proposal_id` (W7 slice 7 / 7.1.4) is the most recent
    proposal whose iteration was triggered by this cluster
    (`iterations.cluster_id`). Null when no iteration has yet been
    spawned against the cluster — the Failures card stays
    non-interactive in that case.
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
    latest_proposal_id: UUID | None
    # The iteration whose evaluation produced the traces this cluster
    # was built from. Resolved by picking any sample trace and reading
    # its `traces.iteration_id`. Null only when sample traces predate
    # the Tier-1 trace-persistence change (legacy clusters) or weren't
    # produced by an iteration (production traces).
    spawning_iteration_index: int | None = None
    spawning_iteration_id: UUID | None = None
    # Per-cluster source mix. Derived from `traces.iteration_id IS NULL`
    # (production) vs `IS NOT NULL` (eval). A cluster reads as
    # production-only, eval-only, or mixed depending on which side of
    # the join its sample traces fall on. Sample traces with no matching
    # row in `traces` (legacy clusters predating Tier-1 trace persistence)
    # do not contribute to either count.
    prod_count: int = 0
    eval_count: int = 0


class FailureClusterList(_Strict):
    workflow_id: str
    items: list[FailureClusterSummary]


# 9.2.3 — non-skill artifact proposal create requests. One model per
# kind so the body schema documents the artifact shape explicitly.
# The endpoint mounts `/api/workflows/{wfId}/proposals/{kind}` and
# returns the created proposal as a `ProposalSummary`.

class MetricProposalCreate(BaseModel):
    """Body for `POST /api/workflows/{wfId}/proposals/metric`.

    `proposed_metric` is the new `MetricDefinitionShape` — same shape
    as the workflow's existing `metric_definition` JSONB. Required to
    carry a `name` so the diff renderer can label the change.
    """

    model_config = ConfigDict(extra="forbid")

    plain_language_summary: str = Field(..., min_length=1, max_length=500)
    proposed_metric: dict[str, Any]
    rationale: str | None = Field(default=None, max_length=2000)


class FailureListItem(_Strict):
    """One row in the flat-list view of failures (cluster-list toggle).

    Each row is one sample trace from a cluster's `sample_trace_ids`,
    decorated with the cluster's label/severity so a reviewer can scan
    individual failures across clusters in a single sortable table.
    """

    trace_id: UUID
    cluster_id: UUID
    cluster_label: str
    severity: str  # 'high' | 'medium' | 'low'
    source: str  # 'production' | 'eval'
    started_at: datetime | None
    # Eval-case binding when source='eval'; null for production rows
    # (production traces aren't attached to an eval case).
    eval_case_id: UUID | None
    iteration_index: int | None


class FailureList(_Strict):
    workflow_id: str
    items: list[FailureListItem]


# ---------------------------------------------------------------------------
# Audit trail (W7 slice 4)
# ---------------------------------------------------------------------------


class AuditEntryRow(_Strict):
    """Workspace-level audit entry for the trail view.

    Same shape as `AuditEntry` (proposal-detail), repeated here so the
    list endpoint contract is independent of proposal-side changes.

    Intentionally excludes parent_hash/entry_hash — hash chain fields are
    internal to chain verification (POST /api/audit/verify) and not exposed
    on the list endpoint.
    """

    id: UUID
    seq: int
    kind: str
    actor: str
    related_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime


class AuditList(_Strict):
    items: list[AuditEntryRow]
    total: int  # total count in audit_entries (not just returned items)
    truncated: bool  # true when total > items length (limit applied)


class AuditVerifyResponse(_Strict):
    """Result of running the chain-integrity check.

    `valid` covers seq contiguity + no duplicates (structural integrity).
    `hash_chain_valid` covers SHA-256 content hashes + parent-linkage
    for entries that have hash data (written after 0009_audit_hash_chain).
    Pre-epoch entries (NULL entry_hash) are counted in `total_entries`
    but skipped by hash verification — they are structurally valid, just
    from before the hash chain was activated.
    """

    valid: bool
    total_entries: int
    min_seq: int | None  # null when total_entries == 0
    max_seq: int | None
    missing_seqs: list[int]  # capped at 100 in the API for payload safety
    duplicate_seqs: list[int]  # likewise
    canonical_export_bytes: int  # size of `to_canonical_json(...)` output
    checked_at: datetime
    # Hash chain fields (0009_audit_hash_chain.sql)
    hash_chain_valid: bool  # True if every hashed entry's hash recomputes correctly
    hash_chain_entries: int  # Count of entries that carry hash data
    first_broken_seq: int | None  # First seq where the chain breaks; None if valid


# ---------------------------------------------------------------------------
# Skills (W7 slice 9 + 10 — 7.1.10 / 7.1.11)
# ---------------------------------------------------------------------------


class SkillVersionSummary(_Strict):
    """One row in a skill's version history pane.

    `diff_summary` is the human-readable change description the agent or
    nl-gen pipeline writes when proposing the version. Null on bootstrap.
    """

    id: UUID
    version_seq: int
    parent_version_id: UUID | None
    diff_summary: str | None
    created_by: str
    created_at: datetime


class SkillSummary(_Strict):
    """Card-shaped skill row for the per-workflow skills list.

    `head_version_seq` is the active version's sequence number for the
    Library / anatomy pane "v7" pill. Null if no version exists yet.
    """

    id: str
    kind: str  # 'python' | 'instruction' | 'composite'
    workflow_id: str | None
    capability_tags: list[str]
    head_version_id: UUID | None
    head_version_seq: int | None
    head_created_at: datetime | None


class SkillList(_Strict):
    items: list[SkillSummary]


class SkillRelatedEvalCase(_Strict):
    """Sparse eval-case row surfaced on the skill detail page.

    For instruction skills: provenance='retention-violation' rows on the
    skill's workflow. For code skills: any provenance cluster-derived /
    rejected-feedback that was linked to the iteration which promoted a
    proposal on this skill. Both reduce to "what test cases is this
    skill on the hook for" — the page renders them under
    "Related eval cases".
    """

    id: UUID
    workflow_id: str | None
    provenance: str
    expected_behavior: dict[str, Any] | None
    is_test_fold: bool
    created_at: datetime


class SkillDetail(_Strict):
    """Per-skill detail page DTO.

    Combines `skills`, `skill_versions` (head row inlined + history list),
    capability tags, the workflow the skill is bound to, and the most
    recent related eval cases. The web app branches on `kind` to choose
    between the prompt-variant renderer (W7 slice 9) and the code-variant
    renderer (W7 slice 10).

    `head_content` is the active version's text. `parent_content` is the
    immediately-prior version, used by the inline diff in the code
    renderer. `retention_block` is the parsed YAML frontmatter for
    instruction skills; null otherwise.
    """

    id: str
    kind: str
    workflow_id: str | None
    workflow_description: str | None
    capability_tags: list[str]
    head_version_id: UUID | None
    head_version_seq: int | None
    head_content: str | None
    head_retention_block: dict[str, Any] | None
    head_diff_summary: str | None
    head_created_at: datetime | None
    head_created_by: str | None
    parent_content: str | None
    parent_version_seq: int | None
    # Production pointer (separate from head_version_id, which tracks the
    # best gate-validated version). Null until the operator deploys an
    # approved proposal; advanced/reverted by /api/proposals/{id}/deploy
    # and /rollback. The UI uses these to show "Deployed: vN" and to gate
    # the visibility of the Deploy button (only one deployed proposal per
    # skill at a time).
    deployed_version_id: UUID | None
    deployed_version_seq: int | None
    deployable_proposal_id: UUID | None  # approved-awaiting-deploy proposal, if any
    deployable_proposal_version_seq: int | None
    deployed_proposal_id: UUID | None  # the proposal currently deployed (rollback target)
    versions: list[SkillVersionSummary]
    related_eval_cases: list[SkillRelatedEvalCase]


# ---------------------------------------------------------------------------
# Traces (W7 slice 8 — 7.1.9)
# ---------------------------------------------------------------------------


class TraceSummary(_Strict):
    """Row in the per-workflow trace list view.

    `event_count` and `kind_counts` are computed from the JSONB events
    array — the list view shows them as quick triage signals (which
    traces are tool-heavy vs reasoning-heavy, did any monitor signal
    fire). `iteration_index` is non-null when the trace was produced
    inside the gate loop; null for production / standalone traces.
    """

    id: UUID
    workflow_id: str | None
    iteration_id: UUID | None
    iteration_index: int | None
    skill_version_id: UUID | None
    started_at: datetime
    ended_at: datetime | None
    event_count: int
    kind_counts: dict[str, int]


class TraceList(_Strict):
    workflow_id: str
    items: list[TraceSummary]


class TraceDetail(_Strict):
    """Full trace with the AgentEvent stream.

    `events` is passed through verbatim — list of dicts with the
    discriminated `type` field that the trace-format SPEC defines. The
    web app renders per-event variants client-side rather than the API
    typing the union, keeping this DTO frozen-shape if SPEC bumps.
    """

    id: UUID
    workflow_id: str | None
    iteration_id: UUID | None
    iteration_index: int | None
    skill_version_id: UUID | None
    skill_id: str | None
    skill_version_seq: int | None
    started_at: datetime
    ended_at: datetime | None
    metric_outputs: dict[str, Any] | None
    token_usage: dict[str, Any] | None
    events: list[dict[str, Any]]


__all__ = [
    "ApprovalDetail",
    "ApproveResponse",
    "AuditEntry",
    "AuditEntryRow",
    "AuditList",
    "AuditVerifyResponse",
    "DecideRequest",
    "FailureClusterList",
    "FailureClusterSummary",
    "FailureList",
    "FailureListItem",
    "MetricProposalCreate",
    "GateResultCases",
    "HealthResponse",
    "IterationDetail",
    "IterationList",
    "IterationPoint",
    "ProposalDetail",
    "ProposalList",
    "ProposalSummary",
    "SkillDetail",
    "SkillList",
    "SkillRelatedEvalCase",
    "SkillSummary",
    "SkillVersionSummary",
    "TraceDetail",
    "TraceList",
    "TraceSummary",
    "EvalCaseCreate",
    "IterationCaseRow",
    "IterationDetailFull",
    "WorkflowAnatomy",
    "WorkflowDeleteResponse",
    "WorkflowDetail",
    "WorkflowList",
    "WorkflowSummary",
    "WorkflowUpdate",
]
