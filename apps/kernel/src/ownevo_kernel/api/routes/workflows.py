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

import asyncio
import json
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ...audit import append_audit_entry
from ...design_agent.log import load_design_agent_log
from ...llm import is_model_allowed
from ...nl_gen.design_brief_context import (
    EVAL_CASE_DIMENSIONS,
    METRIC_DIMENSIONS,
    SIM_PLAN_DIMENSIONS,
    format_dimensions_block,
)
from ...nl_gen.eval_generator import generate_eval_case_set
from ...nl_gen.eval_persistence import persist_eval_case_set
from ...nl_gen.input_pool import build_input_pool_block
from ...nl_gen.metric_generator import generate_metric_definition
from ...nl_gen.sim_generator import generate_simulation_plan
from ...tenant_session import WorkspaceBindError, WorkspaceMembershipError, acquire_workspace_conn
from ...types import AuditKind
from .._anthropic_client import build_async_anthropic
from ..deps import ConnDep, DemoModeCheck, PoolDep, PrincipalDep, is_demo_mode
from ..jsonb import decode_jsonb_obj
from ..models import (
    CaseOutputList,
    CaseOutputRow,
    DescriptionProposalCreate,
    EvalCaseCreate,
    EvalCaseList,
    EvalCaseProvenance,
    EvalCaseSummary,
    FailureClusterList,
    FailureClusterSummary,
    FailureList,
    FailureListItem,
    GenerateEvalCasesResponse,
    IterationCaseRow,
    IterationDetailFull,
    IterationList,
    IterationPoint,
    MetricProposalCreate,
    ProposalSummary,
    RunIterationResponse,
    SimProposalCreate,
    TryItRequest,
    TryItResponse,
    UIViewProposalCreate,
    WorkflowAgentModelUpdate,
    WorkflowAnatomy,
    WorkflowDeleteResponse,
    WorkflowList,
    WorkflowSummary,
    WorkflowUpdate,
)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# Approved proposal states — STATE_MACHINES.md.
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

    Under ``DEMO_MODE=true`` the response excludes benchmark workflows
    (``kind='benchmark'``) so the public demo never exposes M5 / τ³
    surfaces. Production deploys return the full list.
    """
    rows = await conn.fetch(
        """
        SELECT
            w.id,
            w.description,
            w.mode::text                                AS mode,
            w.kind                                      AS kind,
            (
                SELECT COUNT(*)::int
                FROM iterations i
                WHERE i.workflow_id = w.id
                  AND i.state <> 'running'
            )                                           AS iteration_count,
            (
                SELECT COUNT(*)::int
                FROM iterations i
                WHERE i.workflow_id = w.id
                  AND i.state = 'running'
            )                                           AS running_iteration_count,
            (
                SELECT MIN(i.started_at)
                FROM iterations i
                WHERE i.workflow_id = w.id
                  AND i.state = 'running'
            )                                           AS oldest_running_started_at,
            (
                SELECT MAX(i.best_ever_score_after)
                FROM iterations i
                WHERE i.workflow_id = w.id
                  AND i.state <> 'running'
            )                                           AS best_ever_score,
            (
                SELECT MAX(p.state_updated_at)
                FROM proposals p
                JOIN iterations i ON i.id = p.iteration_id
                WHERE i.workflow_id = w.id
                  AND p.state = ANY($1::proposal_state[])
            )                                           AS last_improved_at,
            (
                SELECT COUNT(*)::int
                FROM proposals p
                JOIN iterations i ON i.id = p.iteration_id
                WHERE i.workflow_id = w.id
                  AND p.state = ANY($2::proposal_state[])
            )                                           AS pending_proposals_count
        FROM workflows w
        ORDER BY w.created_at ASC, w.id ASC
        """,
        list(_APPROVED_STATES),
        list(_PENDING_STATES),
    )

    if is_demo_mode():
        rows = [r for r in rows if r["kind"] != "benchmark"]
    items = [_row_to_summary(r) for r in rows]
    return WorkflowList(items=items, total=len(items))


@router.get("/{workflow_id}", response_model=WorkflowAnatomy)
async def get_workflow(workflow_id: str, conn: ConnDep) -> WorkflowAnatomy:
    """Workflow detail with the full NL-gen `spec` JSONB.

    Drives the W7 slice 11 (7.1.12) Agent-anatomy pane. Returns the
    raw spec dict; the web app branches on which spec fields are
    populated rather than the API typing the spec union (the spec
    schema is frozen at `nl_gen/spec.py` v1.0 — bumps will require
    web parity but not an API DTO change).
    """
    row = await conn.fetchrow(
        """
        SELECT id, description, mode::text AS mode, kind, spec,
               simulation_plan, metric_definition,
               created_from_template, design_agent_log,
               agent_model_id, origin
        FROM workflows
        WHERE id = $1
        """,
        workflow_id,
    )
    if row is None:
        # Static message — never reflect the user-supplied path param.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    if is_demo_mode() and row["kind"] == "benchmark":
        # Benchmark workflows are hidden from the public demo surface.
        # Same opaque 404 as a missing row — never leak the kind.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    spec = decode_jsonb_obj(row["spec"]) or {}
    return WorkflowAnatomy(
        id=row["id"],
        description=row["description"],
        mode=row["mode"],
        kind=row["kind"],
        spec=spec,
        simulation_plan=decode_jsonb_obj(row["simulation_plan"]),
        metric_definition=decode_jsonb_obj(row["metric_definition"]),
        agent_model_id=row["agent_model_id"],
        created_from_template=row["created_from_template"],
        design_agent_log=decode_jsonb_obj(row["design_agent_log"]),
        origin=row["origin"],
    )


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
        # Static message — never reflect the user-supplied path param,
        # which has no length cap and could be exploited as an echo
        # surface for arbitrary user-controlled strings.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
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
                  AND p.state = ANY($2::proposal_state[])
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


@router.get(
    "/{workflow_id}/iterations/{iteration_index}",
    response_model=IterationDetailFull,
)
async def get_iteration_detail(
    workflow_id: str,
    iteration_index: int,
    conn: ConnDep,
) -> IterationDetailFull:
    """One iteration with the per-case outcome roster.

    The lift-chart click-through lands here. The per-case rows come
    from the `traces` table — iteration_runner writes one trace per
    eval case with `metric_outputs = {case_id, predicted, expected,
    passed, is_test_fold}`. Failed cases sort first so the operator
    sees what regressed at the top.
    """
    iter_row = await conn.fetchrow(
        """
        SELECT
            i.id,
            i.workflow_id,
            i.iteration_index,
            i.state::text                                   AS state,
            i.val_score,
            i.best_ever_score_before,
            i.best_ever_score_after,
            i.cluster_id,
            i.parent_skill_version_id,
            i.proposed_skill_version_id,
            i.started_at,
            i.ended_at,
            fc.label                                        AS cluster_label,
            (
                SELECT p.id
                FROM proposals p
                WHERE p.iteration_id = i.id
                ORDER BY p.created_at DESC
                LIMIT 1
            )                                                AS proposal_id
        FROM iterations i
        LEFT JOIN failure_clusters fc ON fc.id = i.cluster_id
        WHERE i.workflow_id = $1 AND i.iteration_index = $2
        """,
        workflow_id,
        iteration_index,
    )
    if iter_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Iteration not found",
        )

    case_rows = await conn.fetch(
        """
        SELECT
            t.id                                            AS trace_id,
            t.started_at,
            t.ended_at,
            t.metric_outputs
        FROM traces t
        WHERE t.iteration_id = $1
        ORDER BY t.started_at ASC, t.id ASC
        """,
        iter_row["id"],
    )

    cases: list[IterationCaseRow] = []
    for r in case_rows:
        outputs = decode_jsonb_obj(r["metric_outputs"]) or {}
        rationale = outputs.get("rationale")
        cases.append(
            IterationCaseRow(
                case_id=str(outputs.get("case_id") or r["trace_id"]),
                predicted=_to_bool(outputs.get("predicted")),
                expected=_to_bool(outputs.get("expected")),
                passed=_to_bool(outputs.get("passed")),
                is_test_fold=bool(outputs.get("is_test_fold", False)),
                rationale=rationale if isinstance(rationale, str) else None,
                trace_id=r["trace_id"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
            )
        )

    # Failed-first ordering: failed cases (passed=False) before passes,
    # with unknown (None) sandwiched. Within each group keep started_at
    # order so the operator's eye scans deterministically.
    def sort_key(c: IterationCaseRow) -> tuple[int, str]:
        rank = 0 if c.passed is False else (1 if c.passed is None else 2)
        return (rank, c.case_id)

    cases.sort(key=sort_key)

    n_passed = sum(1 for c in cases if c.passed is True)
    n_failed = sum(1 for c in cases if c.passed is False)

    return IterationDetailFull(
        workflow_id=iter_row["workflow_id"],
        iteration_id=iter_row["id"],
        iteration_index=iter_row["iteration_index"],
        state=iter_row["state"],
        val_score=(
            float(iter_row["val_score"]) if iter_row["val_score"] is not None else None
        ),
        best_ever_score_before=(
            float(iter_row["best_ever_score_before"])
            if iter_row["best_ever_score_before"] is not None
            else None
        ),
        best_ever_score_after=(
            float(iter_row["best_ever_score_after"])
            if iter_row["best_ever_score_after"] is not None
            else None
        ),
        n_cases=len(cases),
        n_passed=n_passed,
        n_failed=n_failed,
        cluster_id=iter_row["cluster_id"],
        cluster_label=iter_row["cluster_label"],
        parent_skill_version_id=iter_row["parent_skill_version_id"],
        proposed_skill_version_id=iter_row["proposed_skill_version_id"],
        proposal_id=iter_row["proposal_id"],
        started_at=iter_row["started_at"],
        ended_at=iter_row["ended_at"],
        cases=cases,
    )


def _to_bool(v: object) -> bool | None:
    """Trace metric_outputs may come back as JSON bool, None, or string
    on legacy rows. Coerce defensively."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


@router.get(
    "/{workflow_id}/failure_clusters",
    response_model=FailureClusterList,
)
async def list_failure_clusters(
    workflow_id: str,
    conn: ConnDep,
) -> FailureClusterList:
    """Return the active failure clusters for a workflow.

    Sorted by severity (high → low) then `cluster_size DESC` so the most
    impactful cluster lands at the top of the Failures view. The
    `centroid` column is excluded — 384 floats per row aren't useful to
    the UI and the JSON payload is much smaller without them.
    """
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not workflow_exists:
        # Static message — never reflect the user-supplied path param,
        # which has no length cap and could be exploited as an echo
        # surface for arbitrary user-controlled strings.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    # `latest_proposal_id` ( slice 7 / 7.1.4): correlated subquery
    # picks the newest proposal whose iteration was spawned against
    # the cluster. The Failures-card click-through opens that proposal.
    # Null when the loop hasn't yet produced one for the cluster.
    rows = await conn.fetch(
        """
        SELECT
            fc.id,
            fc.workflow_id,
            fc.label,
            fc.severity,
            fc.cluster_size,
            fc.label_eval_score,
            fc.quality_score,
            fc.sample_trace_ids,
            fc.created_at,
            (
                SELECT p.id
                FROM proposals p
                JOIN iterations i ON i.id = p.iteration_id
                WHERE i.cluster_id = fc.id
                ORDER BY p.created_at DESC
                LIMIT 1
            )                                           AS latest_proposal_id
        FROM failure_clusters fc
        WHERE fc.workflow_id = $1
        ORDER BY
            CASE fc.severity
                WHEN 'high'   THEN 0
                WHEN 'medium' THEN 1
                WHEN 'low'    THEN 2
                ELSE 3
            END,
            fc.cluster_size DESC,
            fc.created_at DESC
        """,
        workflow_id,
    )

    # Resolve spawning_iteration + source mix per cluster in a single
    # follow-up query. Source is derived from `traces.iteration_id`:
    # `IS NULL` = production trace, `IS NOT NULL` = eval (an iteration
    # run produced it). Done in Python (not a correlated subquery)
    # because asyncpg's ANY($1::uuid[]) handling of NULL/empty arrays is
    # brittle and a single batched lookup is straightforward.
    all_sample_ids: list[UUID] = []
    for r in rows:
        for tid in r["sample_trace_ids"] or []:
            all_sample_ids.append(tid)
    spawning_by_cluster: dict[UUID, tuple[int, UUID]] = {}
    # eval_traces: trace_id → (iteration_index, iteration_id); only
    # populated for sample traces with non-null iteration_id.
    eval_traces: dict[UUID, tuple[int, UUID]] = {}
    # prod_traces: set of sample trace ids whose `iteration_id IS NULL`.
    prod_traces: set[UUID] = set()
    if all_sample_ids:
        trace_rows = await conn.fetch(
            """
            SELECT
                t.id            AS trace_id,
                t.iteration_id  AS iter_id,
                i.iteration_index
            FROM traces t
            LEFT JOIN iterations i ON i.id = t.iteration_id
            WHERE t.id = ANY($1::uuid[])
            """,
            all_sample_ids,
        )
        for row in trace_rows:
            if row["iter_id"] is None:
                prod_traces.add(row["trace_id"])
            else:
                eval_traces[row["trace_id"]] = (
                    row["iteration_index"],
                    row["iter_id"],
                )
        for r in rows:
            best: tuple[int, UUID] | None = None
            for tid in r["sample_trace_ids"] or []:
                resolved = eval_traces.get(tid)
                if resolved is None:
                    continue
                if best is None or resolved[0] < best[0]:
                    best = resolved
            if best is not None:
                spawning_by_cluster[r["id"]] = best

    # Per-cluster prod/eval counts. Sample ids absent from both sets
    # (legacy clusters predating Tier-1 trace persistence) don't
    # contribute to either count.
    source_counts: dict[UUID, tuple[int, int]] = {}
    for r in rows:
        prod = 0
        evl = 0
        for tid in r["sample_trace_ids"] or []:
            if tid in prod_traces:
                prod += 1
            elif tid in eval_traces:
                evl += 1
        source_counts[r["id"]] = (prod, evl)

    items = [
        FailureClusterSummary(
            id=r["id"],
            workflow_id=r["workflow_id"],
            label=r["label"],
            severity=r["severity"],
            cluster_size=r["cluster_size"],
            label_eval_score=(
                float(r["label_eval_score"])
                if r["label_eval_score"] is not None
                else None
            ),
            quality_score=(
                float(r["quality_score"]) if r["quality_score"] is not None else None
            ),
            sample_trace_ids=list(r["sample_trace_ids"] or []),
            created_at=r["created_at"],
            latest_proposal_id=r["latest_proposal_id"],
            spawning_iteration_index=(
                spawning_by_cluster[r["id"]][0]
                if r["id"] in spawning_by_cluster
                else None
            ),
            spawning_iteration_id=(
                spawning_by_cluster[r["id"]][1]
                if r["id"] in spawning_by_cluster
                else None
            ),
            prod_count=source_counts.get(r["id"], (0, 0))[0],
            eval_count=source_counts.get(r["id"], (0, 0))[1],
        )
        for r in rows
    ]
    return FailureClusterList(workflow_id=workflow_id, items=items)


class ClusterProductionFailuresResponse(BaseModel):
    """Result of an on-demand production-failure clustering run.

    `clustered_failures` is the number of failing production traces that
    fed the run; `clusters_created` is how many `failure_clusters` rows
    the run persisted (may be lower — some failures land in noise or get
    gated out as low-quality clusters). `cluster_ids` lets the caller
    deep-link straight to the new clusters.
    """

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    clustered_failures: int
    clusters_created: int
    cluster_ids: list[UUID]


class PushEvalCasesCopilotStudioRequest(BaseModel):
    """Push a workflow's eval cases into Copilot Studio as a test set.

    `agent_id` identifies the customer's deployed Copilot Studio agent the
    test set runs against. It is supplied per-request rather than read off
    the workflow: nothing auto-populates it yet (Copilot Studio has no OTel
    trace egress, so the trace-ingest funnel that would tag it is not built
    yet). `cluster_id` narrows the push to one failure cluster's
    cases; `test_fold_only` pushes only held-out (test-fold) cases.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=256)
    test_set_name: str | None = Field(default=None, max_length=256)
    cluster_id: UUID | None = None
    test_fold_only: bool = False
    pushed_by: str = Field(default="human", min_length=1, max_length=128)
    # Safety cap before the MSFT API call. The Power Platform Evaluation API's
    # limit per test set is unpublished (shapes are preview/unpinned); 500 is
    # conservative. Use cluster_id to push a targeted subset when a workflow
    # has more cases than the cap.
    max_cases: int = Field(default=500, ge=1, le=2000)


class PushEvalCasesCopilotStudioResponse(BaseModel):
    """Result of creating a Copilot Studio test set from eval cases.

    `test_set_id` is the id Power Platform assigned the created test set
    (may be empty if the Evaluation API response omitted it); `case_count`
    is how many eval cases were pushed.
    """

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    test_set_id: str
    case_count: int


@router.post(
    "/{workflow_id}/cluster-production-failures",
    response_model=ClusterProductionFailuresResponse,
)
async def cluster_production_failures_route(
    workflow_id: str,
    conn: ConnDep,
    _demo: DemoModeCheck,
) -> ClusterProductionFailuresResponse:
    """Cluster the workflow's production-trace tool failures on demand.

    Reads production traces (iteration_id IS NULL) whose events carry a
    `tool_call_result status='error'`, runs the embed → reduce → cluster
    → label pipeline, and persists surviving clusters into
    `failure_clusters` — making OTLP-ingested agent failures show up on
    the Failures tab next to eval-derived clusters.

    Heavy by design (sentence-transformers + UMAP + HDBSCAN + an LLM
    label call), so it's an explicit POST rather than something that
    runs on every ingest. Blocked under DEMO_MODE.
    """
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    # Construct the real (heavy) pipeline implementations. Imported
    # lazily so the module import doesn't pull the `clustering` /
    # `agent` extras on every API boot — only this route needs them.
    try:
        from ...clustering.default_impl import (
            AnthropicLabeler,
            HDBSCANClusterer,
            SentenceTransformerEmbedder,
            UMAPReducer,
        )
        from ...clustering.from_traces import cluster_production_failures
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Clustering dependencies are not installed on this kernel "
                "(install the `clustering` + `agent` extras)."
            ),
        ) from exc

    persisted = await cluster_production_failures(
        conn,
        workflow_id,
        embedder=SentenceTransformerEmbedder(),
        reducer=UMAPReducer(),
        clusterer=HDBSCANClusterer(),
        labeler=AnthropicLabeler(),
    )

    cluster_ids = [p.id for p in persisted]
    return ClusterProductionFailuresResponse(
        workflow_id=workflow_id,
        clustered_failures=sum(len(p.summary.member_indices) for p in persisted),
        clusters_created=len(persisted),
        cluster_ids=cluster_ids,
    )


@router.post(
    "/{workflow_id}/push-eval-cases-copilot-studio",
    response_model=PushEvalCasesCopilotStudioResponse,
    status_code=status.HTTP_200_OK,
)
async def push_eval_cases_copilot_studio(
    workflow_id: str,
    body: PushEvalCasesCopilotStudioRequest,
    conn: ConnDep,
    _demo: DemoModeCheck,
) -> PushEvalCasesCopilotStudioResponse:
    """Push a workflow's eval cases into Copilot Studio as a test set.

    Turns the workflow's (or one failure cluster's) eval cases into a
    Power Platform Evaluation API test set against the customer's deployed
    agent — the only enterprise platform with an external eval-push API.

    Preconditions (422 unless met): the workflow exists and is
    `origin='copilot_studio'`, and it has at least one matching eval case.
    A configured Copilot Studio credential is required (404 when absent,
    503 when the master encryption key is unset, 500 when the stored
    credential is malformed or cannot be decrypted). Adapter failures map
    to their HTTP status (401 auth, 404 not-found, 429 throttled, 502 other).

    Success writes a hash-chained `eval-cases-pushed-copilot-studio` audit
    entry recording the created test-set id + case count.

    Note: the eval-case run/poll lifecycle is not invoked here — those
    Evaluation API shapes are preview and unpinned (see the adapter's
    `evaluation_api.py` / MAPPING.md). This creates the test set only.
    """
    row = await conn.fetchrow(
        "SELECT origin FROM workflows WHERE id = $1",
        workflow_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    if row["origin"] != "copilot_studio":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Workflow is not Copilot Studio-originated",
        )

    from ...eval_cases.registry import list_eval_cases

    cases = await list_eval_cases(
        conn,
        workflow_id=workflow_id,
        cluster_id=body.cluster_id,
        is_test_fold=True if body.test_fold_only else None,
        limit=body.max_cases,
    )
    if not cases:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No matching eval cases to push",
        )

    # The Evaluation API takes cases already shaped as {input, expected_output};
    # ownEvo stores the expected side as `expected_behavior`.
    test_cases = [
        {"input": c.input, "expected_output": c.expected_behavior} for c in cases
    ]
    test_set_name = (body.test_set_name or f"ownEvo · {workflow_id}")[:256]

    from .integrations import load_copilot_credential_or_raise

    cred = await load_copilot_credential_or_raise(conn)

    from ...middleware.copilot_studio import (
        CopilotStudioAuthError,
        CopilotStudioError,
        CopilotStudioNotFoundError,
        CopilotStudioRateLimitError,
        create_test_set,
    )

    try:
        result = await create_test_set(
            cred,
            agent_id=body.agent_id,
            name=test_set_name,
            cases=test_cases,
        )
    except CopilotStudioAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)[:200]) from exc
    except CopilotStudioNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)[:200]) from exc
    except CopilotStudioRateLimitError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)[:200]) from exc
    except CopilotStudioError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)[:200]) from exc

    await append_audit_entry(
        conn,
        kind=AuditKind.EVAL_CASES_PUSHED_COPILOT_STUDIO,
        payload={
            "workflow_id": workflow_id,
            "agent_id": body.agent_id,
            "test_set_id": result.test_set_id,
            "test_set_name": test_set_name,
            "case_count": result.case_count,
            "cluster_id": str(body.cluster_id) if body.cluster_id else None,
        },
        actor=body.pushed_by,
        # related_id is a uuid column; workflow ids are text, so the
        # workflow id lives in the payload and the cluster id (when the
        # push was cluster-scoped) is the uuid anchor.
        related_id=body.cluster_id,
    )

    return PushEvalCasesCopilotStudioResponse(
        workflow_id=workflow_id,
        test_set_id=result.test_set_id,
        case_count=result.case_count,
    )


@router.get(
    "/{workflow_id}/failures",
    response_model=FailureList,
)
async def list_workflow_failures(
    workflow_id: str,
    conn: ConnDep,
    source: str | None = None,
    limit: int = 500,
) -> FailureList:
    """Return one row per failed sample trace across all active clusters.

    Powers the list/cluster toggle on the Failures view. `source` may
    be `production`, `eval`, or omitted (returns all). Each row is a
    `(trace, cluster)` pairing decorated with timestamp + severity so
    a reviewer can sort across clusters in a single table.

    `limit` caps the result set (default 500; max enforced at 2000).

    For eval rows the trace is bound to the iteration that produced it,
    and (when resolvable) the eval case the iteration was running
    against. Production rows have no iteration_id / eval_case_id.
    """
    limit = min(max(limit, 1), 2000)
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    if source not in (None, "production", "eval"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source must be 'production', 'eval', or omitted",
        )

    # One query joins each active cluster's sample_trace_ids through
    # `traces` and (for eval rows) into iterations. The unnest preserves
    # the cluster→trace mapping so we can render label + severity per
    # row without a second pass.
    rows = await conn.fetch(
        """
        SELECT
            t.id            AS trace_id,
            fc.id           AS cluster_id,
            fc.label        AS cluster_label,
            fc.severity     AS severity,
            CASE WHEN t.iteration_id IS NULL THEN 'production' ELSE 'eval' END
                            AS source,
            t.started_at    AS started_at,
            i.iteration_index,
            -- Best-effort eval_case binding: most recent eval_case
            -- attached to this cluster. Null for production rows or
            -- clusters with no eval cases yet.
            (
                SELECT ec.id FROM eval_cases ec
                WHERE ec.cluster_id = fc.id
                ORDER BY ec.created_at DESC
                LIMIT 1
            )               AS eval_case_id
        FROM failure_clusters fc
        CROSS JOIN LATERAL unnest(COALESCE(fc.sample_trace_ids, '{}'::uuid[]))
            AS sample_id
        JOIN traces t ON t.id = sample_id
        LEFT JOIN iterations i ON i.id = t.iteration_id
        WHERE fc.workflow_id = $1
          AND ($2::text IS NULL OR
               (CASE WHEN t.iteration_id IS NULL THEN 'production' ELSE 'eval' END) = $2)
        ORDER BY t.started_at DESC NULLS LAST,
                 CASE fc.severity
                     WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3
                 END ASC
        LIMIT $3
        """,
        workflow_id,
        source,
        limit,
    )

    items = [
        FailureListItem(
            trace_id=r["trace_id"],
            cluster_id=r["cluster_id"],
            cluster_label=r["cluster_label"],
            severity=r["severity"],
            source=r["source"],
            started_at=r["started_at"],
            eval_case_id=r["eval_case_id"] if r["source"] == "eval" else None,
            iteration_index=r["iteration_index"],
        )
        for r in rows
    ]
    return FailureList(workflow_id=workflow_id, items=items)


@router.get(
    "/{workflow_id}/eval-cases",
    response_model=EvalCaseList,
)
async def list_workflow_eval_cases(
    workflow_id: str,
    conn: ConnDep,
) -> EvalCaseList:
    """Return every eval case attached to a workflow.

    Flattens `input` / `expected_behavior` JSONB into the response shape
    (see `EvalCaseSummary`) so the UI doesn't have to repeat the
    split-payload convention from `nl_gen/eval_persistence.py`.
    """
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    from ...eval_cases.registry import list_eval_cases

    cases = await list_eval_cases(conn, workflow_id=workflow_id)
    items: list[EvalCaseSummary] = []
    for c in cases:
        eb = c.expected_behavior or {}
        inp = c.input or {}
        prov_raw = eb.get("provenance") if isinstance(eb, dict) else None
        eb_prov: EvalCaseProvenance | None = None
        category: str | None = None
        if isinstance(prov_raw, dict):
            kind = prov_raw.get("kind")
            source = prov_raw.get("source")
            if isinstance(kind, str) and isinstance(source, str):
                # 'derived' = verbatim user-flagged past miss; 'inferred'
                # = a named domain pattern (regression / edge case bucket).
                if kind == "derived":
                    eb_prov = EvalCaseProvenance(kind=kind, source=source)
                    category = "past-miss"
                elif kind == "inferred":
                    eb_prov = EvalCaseProvenance(kind=kind, source=source)
                    category = "inferred"
                # Unknown kind: leave both None (consistent with hand-authored)
        items.append(
            EvalCaseSummary(
                id=c.id,
                case_id=str(eb.get("case_id") or c.id),
                provenance=c.provenance,
                rationale=eb.get("rationale"),
                target_label_field=eb.get("target_label_field"),
                expected_value=eb.get("expected_value"),
                sim_seed=inp.get("sim_seed"),
                n_steps=inp.get("n_steps"),
                target_step_index=inp.get("target_step_index"),
                is_test_fold=c.is_test_fold,
                cluster_id=c.cluster_id,
                created_at=c.created_at,
                expected_behavior_provenance=eb_prov,
                category=category,
            )
        )
    return EvalCaseList(workflow_id=workflow_id, items=items, total=len(items))


@router.get(
    "/{workflow_id}/case-outputs",
    response_model=CaseOutputList,
)
async def list_workflow_case_outputs(
    workflow_id: str,
    conn: ConnDep,
    iteration: str = "latest",
) -> CaseOutputList:
    """Per-case agent output for one iteration of a workflow (PLAN 8.4.9).

    `iteration` accepts `"latest"` (default — newest by iteration_index)
    or a numeric `iteration_index` like `"5"`. The TableView resolver
    on the operator shell calls this with the default; PLAN 8.4.10
    binds the response to `View.TableView.binding.source =
    'case-output'`.

    Returns an empty `items` list (with `iteration_index = None`) when
    no iteration has run yet — the UI renders the "Coming soon" banner
    in that state instead of a 404.
    """
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1", workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    if iteration == "latest":
        iter_row = await conn.fetchrow(
            """
            SELECT id, iteration_index
            FROM iterations
            WHERE workflow_id = $1
              AND state <> 'running'::iteration_state
            ORDER BY iteration_index DESC
            LIMIT 1
            """,
            workflow_id,
        )
    else:
        try:
            iter_idx = int(iteration)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="iteration must be 'latest' or an integer index",
            ) from None
        iter_row = await conn.fetchrow(
            """
            SELECT id, iteration_index
            FROM iterations
            WHERE workflow_id = $1 AND iteration_index = $2
            """,
            workflow_id,
            iter_idx,
        )

    if iter_row is None:
        # No iteration matches — empty roster, not 404. The operator
        # shell distinguishes "haven't run yet" (iteration_index=None,
        # items=[]) from "ran, no outputs" (iteration_index=N, items=[]).
        return CaseOutputList(
            workflow_id=workflow_id,
            iteration_index=None,
            iteration_id=None,
            items=[],
        )

    rows = await conn.fetch(
        """
        SELECT
            ico.eval_case_id,
            ico.output_json,
            ico.output_payload,
            ico.passed,
            ico.created_at,
            ec.input        AS input,
            ec.expected_behavior AS expected_behavior,
            ec.is_test_fold AS is_test_fold,
            ec.expected_behavior->>'case_id' AS case_id,
            -- Left-join on traces by matching the iteration row plus
            -- the case_id embedded in the trace's metric_outputs jsonb
            -- (set by `_persist_traces`). One trace per (iter, case);
            -- the LATERAL keeps the join cardinality at 1.
            (
                SELECT t.id
                FROM traces t
                WHERE t.iteration_id = ico.iteration_id
                  AND t.metric_outputs->>'case_id' = ec.expected_behavior->>'case_id'
                LIMIT 1
            ) AS trace_id
        FROM iteration_case_outputs ico
        JOIN eval_cases ec ON ec.id = ico.eval_case_id
        WHERE ico.iteration_id = $1
        ORDER BY ico.created_at ASC, ico.eval_case_id ASC
        """,
        iter_row["id"],
    )
    items = [
        CaseOutputRow(
            eval_case_id=r["eval_case_id"],
            case_id=r["case_id"],
            output_json=decode_jsonb_obj(r["output_json"]) or {},
            input=decode_jsonb_obj(r["input"]) or {},
            expected_behavior=decode_jsonb_obj(r["expected_behavior"]) or {},
            passed=bool(r["passed"]),
            is_test_fold=bool(r["is_test_fold"]),
            created_at=r["created_at"],
            trace_id=r["trace_id"],
            output_payload=decode_jsonb_obj(r["output_payload"]),
        )
        for r in rows
    ]
    return CaseOutputList(
        workflow_id=workflow_id,
        iteration_index=iter_row["iteration_index"],
        iteration_id=iter_row["id"],
        items=items,
    )


@router.post(
    "/{workflow_id}/eval-cases",
    response_model=EvalCaseSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_case(
    workflow_id: str,
    payload: EvalCaseCreate,
    conn: ConnDep,
) -> EvalCaseSummary:
    """Manually add one eval case to a workflow's regression suite.

    Hand-authored cases carry `provenance='hand-authored'` (D4 — the
    enum's seeded-by-humans slot). The expected_behavior JSON shape
    matches what `eval_persistence` writes for NL-gen cases so the
    regression gate's replay path picks them up uniformly.
    """
    from ...eval_cases.registry import add_eval_case

    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1", workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    case = await add_eval_case(
        conn,
        provenance="hand-authored",
        workflow_id=workflow_id,
        input={
            "sim_seed": payload.sim_seed,
            "n_steps": payload.n_steps,
            "target_step_index": payload.target_step_index,
        },
        expected_behavior={
            "case_id": payload.case_id,
            "target_label_field": payload.target_label_field,
            "expected_value": payload.expected_value,
            "rationale": payload.rationale or "(manually added)",
        },
        is_test_fold=payload.is_test_fold,
    )
    return EvalCaseSummary(
        id=case.id,
        case_id=payload.case_id,
        provenance=case.provenance,
        rationale=payload.rationale or "(manually added)",
        target_label_field=payload.target_label_field,
        expected_value=payload.expected_value,
        sim_seed=payload.sim_seed,
        n_steps=payload.n_steps,
        target_step_index=payload.target_step_index,
        is_test_fold=payload.is_test_fold,
        cluster_id=case.cluster_id,
        created_at=case.created_at,
    )


@router.delete(
    "/{workflow_id}/eval-cases/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_eval_case(
    workflow_id: str,
    case_id: str,
    conn: ConnDep,
    _: DemoModeCheck,
) -> None:
    """Remove one eval case from the suite.

    Case ids are UUIDs from the `eval_cases` table — accepted as `str`
    here so FastAPI's path parsing doesn't reject mid-format. We resolve
    the row by (workflow_id, id) so cross-workflow deletion is impossible
    even if a UUID is reused (it can't be — UUIDs — but the constraint
    is defensive). Returns 404 if the case doesn't exist or doesn't
    belong to the workflow.
    """
    try:
        case_uuid = UUID(case_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="case_id must be a valid UUID",
        ) from exc

    deleted = await _count_exec(
        conn,
        "DELETE FROM eval_cases WHERE id = $1 AND workflow_id = $2",
        case_uuid,
        workflow_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eval case not found",
        )


@router.post(
    "/{workflow_id}/eval-cases/generate",
    response_model=GenerateEvalCasesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_workflow_eval_cases(
    workflow_id: str,
    conn: ConnDep,
    _: DemoModeCheck,
) -> GenerateEvalCasesResponse:
    """Generate + persist eval cases for an existing workflow.

    Runs `generate_simulation_plan` then `generate_eval_case_set` against
    the workflow's persisted spec (~2 LLM calls, 30-45s wall-clock), then
    inserts the cases via `persist_eval_case_set`. The endpoint is
    idempotent at the eval_cases-row level — re-running generates a fresh
    case set and appends; the UI surfaces the cumulative list.

    Errors:
      * **404** — workflow_id not found
      * **502** — LLM did not emit a sim plan or eval set
      * **503** — `ANTHROPIC_API_KEY` is not set
    """
    import os

    from ...nl_gen.spec import WorkflowSpec

    row = await conn.fetchrow(
        """
        SELECT id, spec, design_agent_log,
               simulation_plan IS NOT NULL    AS has_sim_plan,
               metric_definition IS NOT NULL  AS has_metric
        FROM workflows
        WHERE id = $1
        """,
        workflow_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    spec_dict = decode_jsonb_obj(row["spec"]) or {}
    if not spec_dict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Workflow has no spec yet — generate one via "
                "POST /api/nl-gen/generate before generating eval cases."
            ),
        )

    workflow_spec = WorkflowSpec.model_validate(spec_dict)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ANTHROPIC_API_KEY is not set in the kernel environment; "
                "eval-case generation requires it to call the LLM."
            ),
        )

    client = build_async_anthropic(api_key)
    # Reuse the persisted design-agent transcript from /nl-gen/generate
    # (when the operator ran the discovery interview) so eval cases
    # honour the seed-case nominations + metric direction the operator
    # already committed to. NULL when discovery was skipped.
    design_log = load_design_agent_log(row["design_agent_log"])
    sim_brief = format_dimensions_block(design_log, SIM_PLAN_DIMENSIONS)
    eval_brief = format_dimensions_block(design_log, EVAL_CASE_DIMENSIONS)
    metric_brief = format_dimensions_block(design_log, METRIC_DIMENSIONS)
    try:
        sim_plan = await generate_simulation_plan(
            client, workflow_spec, design_brief_block=sim_brief
        )
        input_pool_block = await build_input_pool_block(conn, workflow_spec)
        case_set = await generate_eval_case_set(
            client,
            workflow_spec,
            sim_plan,
            design_brief_block=eval_brief,
            input_pool_block=input_pool_block,
        )
        # Guard: the model can invent an input_source id that wasn't in the
        # pool block. Cross-check and null-out any id that isn't a declared
        # data source so the replay harness never receives a phantom source.
        declared_source_ids = {
            ds.id for ds in workflow_spec.environment.data_sources
        }
        cleaned: list = []
        changed = False
        for generated_case in case_set.cases:
            if (
                generated_case.input_source is not None
                and generated_case.input_source not in declared_source_ids
            ):
                _log.warning(
                    "eval-case generator invented input_source %r not in %r; "
                    "clearing to None",
                    generated_case.input_source,
                    declared_source_ids,
                )
                generated_case = generated_case.model_copy(
                    update={"input_source": None}
                )
                changed = True
            cleaned.append(generated_case)
        if changed:
            case_set = case_set.model_copy(update={"cases": cleaned})
        # Backfill metric_definition too when the workflow was created
        # without one — historical rows (PR #85-era nl-gen) sometimes
        # landed with simulation_plan/metric_definition NULL and the
        # iteration runner refuses to run without both.
        metric_def = None
        if not row["has_metric"]:
            metric_def = await generate_metric_definition(
                client, workflow_spec, design_brief_block=metric_brief
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM-side failure during eval-case generation: {exc}",
        ) from exc

    async with conn.transaction():
        inserted = await persist_eval_case_set(conn, case_set, workflow_id=workflow_id)
        # Persist the freshly generated sim_plan (always) and metric_definition
        # (only when one didn't exist before) in the same transaction as the
        # eval cases so the workflow row and cases are never partially committed.
        if metric_def is not None:
            await conn.execute(
                """
                UPDATE workflows
                SET simulation_plan = $2::jsonb,
                    metric_definition = $3::jsonb
                WHERE id = $1
                """,
                workflow_id,
                sim_plan.model_dump_json(),
                metric_def.model_dump_json(),
            )
        else:
            await conn.execute(
                """
                UPDATE workflows
                SET simulation_plan = $2::jsonb
                WHERE id = $1
                """,
                workflow_id,
                sim_plan.model_dump_json(),
            )
    train_count = sum(1 for r in inserted if not r.is_test_fold)
    test_count = len(inserted) - train_count
    return GenerateEvalCasesResponse(
        workflow_id=workflow_id,
        generated=len(inserted),
        train_count=train_count,
        test_count=test_count,
    )


@router.post(
    "/{workflow_id}/iterations/run",
    response_model=RunIterationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def run_workflow_iteration(
    workflow_id: str,
    pool: PoolDep,
    principal: PrincipalDep,
    _: DemoModeCheck,
) -> RunIterationResponse:
    """Run one improvement-loop iteration synchronously, persist the result.

    Loads the workflow's spec / sim_plan / metric / eval-cases, drives one
    cycle of `run_nl_gen_demo_loop` (~30-90s wall-clock — one agent run
    over the case set + failure clustering + one proposer call), and
    writes:
      * one `iterations` row in `gate-pass` state (or
        `gate-blocked-no-improvement` if the proposer didn't emit an edit)
      * one new `skill_versions` row carrying the proposed instruction
      * one `proposals` row in `gate-passed` state, ready for human review
        in the existing /proposals UI

    Uses `PoolDep` rather than `ConnDep` so the pool connection is not held
    during the 30-90s LLM window. The runner acquires connections only for
    the short pre-LLM setup and post-LLM persistence phases.

    Errors:
      * **404** — workflow not found
      * **409** — workflow missing spec / sim_plan / metric / eval cases,
                  OR another iteration is already running for this workflow
      * **502** — LLM-side failure mid-iteration
      * **503** — `ANTHROPIC_API_KEY` is not set
    """
    import os

    from ...iteration_runner import (
        IterationRunnerError,
        WorkflowNotIterableError,
        run_one_iteration_for_workflow,
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ANTHROPIC_API_KEY is not set in the kernel environment; "
                "the iteration runner needs it to call the LLM."
            ),
        )

    # Concurrent-run guard: reject immediately if another iteration is
    # already in-flight. One running iteration per workflow at a time;
    # the 30-90s LLM window makes queuing impractical for the MVP.
    async with acquire_workspace_conn(
        pool, principal.workspace_id, user_id=principal.user_id
    ) as conn:
        workflow_exists = await conn.fetchval(
            "SELECT 1 FROM workflows WHERE id = $1", workflow_id
        )
        if not workflow_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found",
            )
        running = await conn.fetchval(
            """
            SELECT 1 FROM iterations
            WHERE workflow_id = $1 AND state = 'running'::iteration_state
            LIMIT 1
            """,
            workflow_id,
        )
        if running:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "An iteration is already running for this workflow. "
                    "Wait for it to finish before starting a new one."
                ),
            )

    client = build_async_anthropic(api_key)
    try:
        outcome = await run_one_iteration_for_workflow(
            pool,
            workflow_id=workflow_id,
            workspace_id=principal.workspace_id,
            client=client,
        )
    except WorkflowNotIterableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except IterationRunnerError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM-side failure mid-iteration: {exc}",
        ) from exc

    return RunIterationResponse(
        iteration_id=outcome.iteration_id,
        iteration_index=outcome.iteration_index,
        state=outcome.state,
        val_score=outcome.val_score,
        n_cases=outcome.n_cases,
        n_failed=outcome.n_failed,
        proposed_skill_id=outcome.proposed_skill_id,
        proposed_skill_version_id=outcome.proposed_skill_version_id,
        proposed_instruction=outcome.proposed_instruction,
        proposal_id=outcome.proposal_id,
    )


# ---------------------------------------------------------------------
# POST /api/workflows/{id}/try ( — Try-it sandbox tab)
# ---------------------------------------------------------------------

# Per-workflow concurrency guard. One try at a time per workflow
# protects the dev box from N concurrent LLM calls fan-out + makes the
# UI's "running" state honest. Maps to 409 when busy. asyncio.Lock is
# process-local — fine for single-replica dev / Fly demo; would need
# Redis advisory locks for a multi-replica cluster.
_TRY_LOCKS: dict[str, asyncio.Lock] = {}


def _try_lock_for(workflow_id: str) -> asyncio.Lock:
    lock = _TRY_LOCKS.get(workflow_id)
    if lock is None:
        lock = asyncio.Lock()
        _TRY_LOCKS[workflow_id] = lock
    return lock


# 60s hard timeout per try. predict_one is a single forced-tool-use
# Anthropic call; p95 well under 30s on haiku / sonnet. Local LLMs can
# blow this — frontend already shows the limit.
_TRY_TIMEOUT_SECONDS = 60.0


@router.post(
    "/{workflow_id}/try",
    response_model=TryItResponse,
    status_code=status.HTTP_200_OK,
)
async def try_workflow_one_case(
    workflow_id: str,
    payload: TryItRequest,
    pool: PoolDep,
    principal: PrincipalDep,
    _: DemoModeCheck,
) -> TryItResponse:
    """Execute one eval case against the workflow's current spec.

    Backs the Try-it tab on the new-workflow Step 2 review page. Lets a
    reviewer dry-run the just-generated agent before committing to a
    full iteration. **Writes nothing** to `iterations`, `proposals`,
    `failure_clusters`, or `audit_entries` — the DB is read-only on
    this code path.

    Errors:
      * **400** — `free_form_input` set (deferred), or neither
                  `eval_case_id` nor `free_form_input` provided
      * **404** — workflow or eval case not found
      * **409** — another try already in-flight for this workflow
      * **502** — LLM-side failure
      * **503** — `ANTHROPIC_API_KEY` not set in the kernel env
      * **504** — try exceeded the 60s hard timeout
    """
    import os

    from ...eval_runner.agent_solver import (
        DEFAULT_MAX_TOKENS,
        DEFAULT_MODEL,
        AgentSolverError,
    )
    from ...eval_runner.try_runner import (
        EvalCaseNotFoundError,
        WorkflowNotReadyError,
        try_one_eval_case,
    )

    if payload.free_form_input is not None and payload.eval_case_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "free_form_input is not yet supported; pick an eval "
                "case generated by NL-gen instead."
            ),
        )
    if payload.eval_case_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="eval_case_id is required.",
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ANTHROPIC_API_KEY is not set in the kernel environment; "
                "the Try-it surface needs it to call the LLM."
            ),
        )

    lock = _try_lock_for(workflow_id)
    if lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A Try-it run is already in-flight for this workflow. "
                "Wait for it to finish before starting another."
            ),
        )

    client = build_async_anthropic(api_key)
    model = payload.model or DEFAULT_MODEL

    async with lock:
        try:
            result = await asyncio.wait_for(
                try_one_eval_case(
                    pool,
                    workflow_id=workflow_id,
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    eval_case_id=payload.eval_case_id,
                    client=client,
                    model=model,
                    max_tokens=DEFAULT_MAX_TOKENS,
                ),
                timeout=_TRY_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=(
                    f"Try-it exceeded the {int(_TRY_TIMEOUT_SECONDS)}s "
                    "timeout. The agent took too long to respond — try "
                    "a smaller model or a simpler case."
                ),
            ) from exc
        except WorkflowNotReadyError as exc:
            # Spec / sim_plan / metric missing — semantically 409 (the
            # workflow exists but isn't iterable yet). Matches the
            # convention used by /iterations/run for the same condition.
            msg = str(exc)
            code = (
                status.HTTP_404_NOT_FOUND
                if "not found" in msg
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(status_code=code, detail=msg) from exc
        except EvalCaseNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except AgentSolverError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Agent call failed: {exc}",
            ) from exc
        except (WorkspaceMembershipError, WorkspaceBindError) as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not a member of this workspace",
            ) from exc

    return TryItResponse(
        case_id=result.case_id,
        expected_value=result.expected_value,
        actual_value=result.actual_value,
        rationale=result.rationale,
        passed=result.passed,
        model=result.model,
        duration_ms=result.duration_ms,
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        trace=result.trace,
    )


# 9.2.3 — non-skill artifact proposal create endpoint. Currently only
# `kind='metric'` is wired; description / sim / ui-view follow
# the same shape. The proposal anchors to the workflow's latest
# iteration (so the audit chain has a context to thread through) and
# the gate's ordering-inversion check (re-scoring prior iterations
# against the new metric) lands in a follow-up.
@router.post(
    "/{workflow_id}/proposals/metric",
    response_model=ProposalSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_metric_proposal(
    workflow_id: str,
    body: MetricProposalCreate,
    conn: ConnDep,
    _: DemoModeCheck,
) -> ProposalSummary:
    """Create a kind='metric' proposal staged against the workflow.

    The body's `proposed_metric` is the new MetricDefinitionShape JSONB.
    Saved as `proposed_payload`, with `skill_id` and
    `parent_version_id` null (metric proposals don't have a skill
    version to fork).

    404 when the workflow doesn't exist; 422 when no iteration has
    been run yet (the proposal needs an iteration to anchor its audit
    context); 422 when the proposed metric is missing the required
    `name` field.
    """
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    if not isinstance(body.proposed_metric.get("name"), str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="proposed_metric.name must be a non-empty string",
        )

    iter_id = await conn.fetchval(
        "SELECT id FROM iterations WHERE workflow_id = $1 "
        "ORDER BY iteration_index DESC LIMIT 1",
        workflow_id,
    )
    if iter_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Workflow has no iterations yet. Run a baseline iteration "
                "before proposing a metric edit so the proposal has audit "
                "context."
            ),
        )


    async with conn.transaction():
        proposal_row = await conn.fetchrow(
            """
            INSERT INTO proposals (
                iteration_id, skill_id, parent_version_id,
                proposed_content, proposed_payload, plain_language_summary,
                kind, state
            )
            VALUES (
                $1, NULL, NULL,
                '', $2::jsonb, $3,
                'metric'::proposal_kind, 'gate-passed'::proposal_state
            )
            RETURNING id, created_at, state_updated_at
            """,
            iter_id,
            json.dumps(body.proposed_metric),
            body.plain_language_summary,
        )

        await append_audit_entry(
            conn,
            kind=AuditKind.PROPOSAL_CREATED,
            payload={
                "workflow_id": workflow_id,
                "proposal_id": str(proposal_row["id"]),
                "kind": "metric",
                "rationale": body.rationale,
            },
            actor="api:create_metric_proposal",
            related_id=proposal_row["id"],
        )

    iter_index = await conn.fetchval(
        "SELECT iteration_index FROM iterations WHERE id = $1",
        iter_id,
    )
    description = await conn.fetchval(
        "SELECT description FROM workflows WHERE id = $1",
        workflow_id,
    )
    return ProposalSummary(
        id=proposal_row["id"],
        iteration_id=iter_id,
        iteration_index=iter_index,
        skill_id=None,
        kind="metric",
        workflow_id=workflow_id,
        workflow_description=description or "",
        state="gate-passed",
        plain_language_summary=body.plain_language_summary,
        eval_score=None,
        eval_rationale=None,
        expected_impact=None,
        created_at=proposal_row["created_at"],
        state_updated_at=proposal_row["state_updated_at"],
    )


# 9.2.3 — create kind='sim' proposal. Proposed sim plan replaces the
# workflow's existing tools / personas / data_sources / env_generators
# on approval. Diff renderer reads added/removed by name.
@router.post(
    "/{workflow_id}/proposals/sim",
    response_model=ProposalSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_sim_proposal(
    workflow_id: str,
    body: SimProposalCreate,
    conn: ConnDep,
    _: DemoModeCheck,
) -> ProposalSummary:
    """Create a kind='sim' proposal staged against the workflow."""
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    iter_id = await conn.fetchval(
        "SELECT id FROM iterations WHERE workflow_id = $1 "
        "ORDER BY iteration_index DESC LIMIT 1",
        workflow_id,
    )
    if iter_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Workflow has no iterations yet. Run a baseline iteration "
                "before proposing a sim edit so the proposal has audit "
                "context."
            ),
        )

    # Require at least one of the sim-shaped sections so the proposal
    # has something to diff against. An empty payload would create a
    # no-signal queue row.
    sim_keys = ("tools", "personas", "data_sources", "env_generators")
    if not any(k in body.proposed_spec for k in sim_keys):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "proposed_spec must include at least one of: "
                + ", ".join(sim_keys)
            ),
        )


    async with conn.transaction():
        proposal_row = await conn.fetchrow(
            """
            INSERT INTO proposals (
                iteration_id, skill_id, parent_version_id,
                proposed_content, proposed_payload, plain_language_summary,
                kind, state
            )
            VALUES (
                $1, NULL, NULL,
                '', $2::jsonb, $3,
                'sim'::proposal_kind, 'gate-passed'::proposal_state
            )
            RETURNING id, created_at, state_updated_at
            """,
            iter_id,
            json.dumps(body.proposed_spec),
            body.plain_language_summary,
        )

        await append_audit_entry(
            conn,
            kind=AuditKind.PROPOSAL_CREATED,
            payload={
                "workflow_id": workflow_id,
                "proposal_id": str(proposal_row["id"]),
                "kind": "sim",
                "rationale": body.rationale,
                "section_keys": [k for k in sim_keys if k in body.proposed_spec],
            },
            actor="api:create_sim_proposal",
            related_id=proposal_row["id"],
        )

    iter_index = await conn.fetchval(
        "SELECT iteration_index FROM iterations WHERE id = $1",
        iter_id,
    )
    description = await conn.fetchval(
        "SELECT description FROM workflows WHERE id = $1",
        workflow_id,
    )
    return ProposalSummary(
        id=proposal_row["id"],
        iteration_id=iter_id,
        iteration_index=iter_index,
        skill_id=None,
        kind="sim",
        workflow_id=workflow_id,
        workflow_description=description or "",
        state="gate-passed",
        plain_language_summary=body.plain_language_summary,
        eval_score=None,
        eval_rationale=None,
        expected_impact=None,
        created_at=proposal_row["created_at"],
        state_updated_at=proposal_row["state_updated_at"],
    )


# 9.2.3 — create kind='description' proposal. The inline-edit on
# Overview / Spec is the "quick edit" path (direct PATCH, cosmetic);
# this endpoint is the "substantive change" path that flows through
# the proposal review + audit chain just like a skill edit does.
@router.post(
    "/{workflow_id}/proposals/description",
    response_model=ProposalSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_description_proposal(
    workflow_id: str,
    body: DescriptionProposalCreate,
    conn: ConnDep,
    _: DemoModeCheck,
) -> ProposalSummary:
    """Create a kind='description' proposal staged against the workflow."""
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    iter_id = await conn.fetchval(
        "SELECT id FROM iterations WHERE workflow_id = $1 "
        "ORDER BY iteration_index DESC LIMIT 1",
        workflow_id,
    )
    if iter_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Workflow has no iterations yet. Run a baseline iteration "
                "before proposing a description edit so the proposal has "
                "audit context."
            ),
        )

    # Reject no-op proposals — the inline-edit's direct PATCH already
    # handles cosmetic changes; a proposal whose proposed text equals
    # the current text would create a no-signal queue row.
    current_description = await conn.fetchval(
        "SELECT description FROM workflows WHERE id = $1",
        workflow_id,
    )
    if (current_description or "").strip() == body.proposed_description.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Proposed description matches the current description. "
                "Use the inline Quick edit for cosmetic tweaks; the "
                "proposal flow is for substantive rewrites."
            ),
        )


    async with conn.transaction():
        proposal_row = await conn.fetchrow(
            """
            INSERT INTO proposals (
                iteration_id, skill_id, parent_version_id,
                proposed_content, proposed_payload, plain_language_summary,
                kind, state
            )
            VALUES (
                $1, NULL, NULL,
                '', $2::jsonb, $3,
                'description'::proposal_kind, 'gate-passed'::proposal_state
            )
            RETURNING id, created_at, state_updated_at
            """,
            iter_id,
            json.dumps(
                {
                    "description": body.proposed_description,
                    "previous_description": current_description or "",
                }
            ),
            body.plain_language_summary,
        )

        await append_audit_entry(
            conn,
            kind=AuditKind.PROPOSAL_CREATED,
            payload={
                "workflow_id": workflow_id,
                "proposal_id": str(proposal_row["id"]),
                "kind": "description",
                "rationale": body.rationale,
                "char_delta": len(body.proposed_description)
                - len(current_description or ""),
            },
            actor="api:create_description_proposal",
            related_id=proposal_row["id"],
        )

    iter_index = await conn.fetchval(
        "SELECT iteration_index FROM iterations WHERE id = $1",
        iter_id,
    )
    return ProposalSummary(
        id=proposal_row["id"],
        iteration_id=iter_id,
        iteration_index=iter_index,
        skill_id=None,
        kind="description",
        workflow_id=workflow_id,
        workflow_description=current_description or "",
        state="gate-passed",
        plain_language_summary=body.plain_language_summary,
        eval_score=None,
        eval_rationale=None,
        expected_impact=None,
        created_at=proposal_row["created_at"],
        state_updated_at=proposal_row["state_updated_at"],
    )


# 9.2.3 — create kind='ui-view' proposal. Parallel to the metric
# flow: anchor to the workflow's latest iteration, persist the new
# view list in `proposed_payload`, return a ProposalSummary so
# the web client can route to the proposal-detail page.
@router.post(
    "/{workflow_id}/proposals/ui-view",
    response_model=ProposalSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_ui_view_proposal(
    workflow_id: str,
    body: UIViewProposalCreate,
    conn: ConnDep,
    _: DemoModeCheck,
) -> ProposalSummary:
    """Create a kind='ui-view' proposal staged against the workflow.

    `proposed_views` is the new operate-tab view list; every
    entry must carry a `type` string. 422 when any entry is missing
    `type` so the diff renderer + post-approval write have a clean
    invariant to rely on.
    """
    workflow_exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not workflow_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    for i, prim in enumerate(body.proposed_views):
        t = prim.get("type")
        if not isinstance(t, str) or not t.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"proposed_views[{i}] is missing a non-empty "
                    "`type` field"
                ),
            )

    iter_id = await conn.fetchval(
        "SELECT id FROM iterations WHERE workflow_id = $1 "
        "ORDER BY iteration_index DESC LIMIT 1",
        workflow_id,
    )
    if iter_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Workflow has no iterations yet. Run a baseline iteration "
                "before proposing a UI-view edit so the proposal has "
                "audit context."
            ),
        )


    async with conn.transaction():
        proposal_row = await conn.fetchrow(
            """
            INSERT INTO proposals (
                iteration_id, skill_id, parent_version_id,
                proposed_content, proposed_payload, plain_language_summary,
                kind, state
            )
            VALUES (
                $1, NULL, NULL,
                '', $2::jsonb, $3,
                'ui-view'::proposal_kind, 'gate-passed'::proposal_state
            )
            RETURNING id, created_at, state_updated_at
            """,
            iter_id,
            json.dumps({"views": body.proposed_views}),
            body.plain_language_summary,
        )

        await append_audit_entry(
            conn,
            kind=AuditKind.PROPOSAL_CREATED,
            payload={
                "workflow_id": workflow_id,
                "proposal_id": str(proposal_row["id"]),
                "kind": "ui-view",
                "rationale": body.rationale,
                "view_types": [p["type"] for p in body.proposed_views],
            },
            actor="api:create_ui_view_proposal",
            related_id=proposal_row["id"],
        )

    iter_index = await conn.fetchval(
        "SELECT iteration_index FROM iterations WHERE id = $1",
        iter_id,
    )
    description = await conn.fetchval(
        "SELECT description FROM workflows WHERE id = $1",
        workflow_id,
    )
    return ProposalSummary(
        id=proposal_row["id"],
        iteration_id=iter_id,
        iteration_index=iter_index,
        skill_id=None,
        kind="ui-view",
        workflow_id=workflow_id,
        workflow_description=description or "",
        state="gate-passed",
        plain_language_summary=body.plain_language_summary,
        eval_score=None,
        eval_rationale=None,
        expected_impact=None,
        created_at=proposal_row["created_at"],
        state_updated_at=proposal_row["state_updated_at"],
    )


@router.patch("/{workflow_id}", response_model=WorkflowAnatomy)
async def update_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    conn: ConnDep,
) -> WorkflowAnatomy:
    """Edit the workflow description.

    The NL-gen `spec` / `simulation_plan` / `metric_definition` are NOT
    touched here — they regenerate through the dedicated generate
    endpoints so their cross-checks stay enforced. Editing the
    description is intentionally side-effect-free: the agent's
    instruction skills and eval cases continue running against the
    original generated artifacts.
    """
    row = await conn.fetchrow(
        """
        UPDATE workflows
        SET description = $2
        WHERE id = $1
        RETURNING id, description, mode::text AS mode, kind, spec,
                  simulation_plan, metric_definition,
                  created_from_template, agent_model_id
        """,
        workflow_id,
        payload.description.strip(),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    spec = decode_jsonb_obj(row["spec"]) or {}
    return WorkflowAnatomy(
        id=row["id"],
        description=row["description"],
        mode=row["mode"],
        kind=row["kind"],
        spec=spec,
        simulation_plan=decode_jsonb_obj(row["simulation_plan"]),
        metric_definition=decode_jsonb_obj(row["metric_definition"]),
        created_from_template=row["created_from_template"],
        agent_model_id=row["agent_model_id"],
    )


@router.patch(
    "/{workflow_id}/agent-model",
    response_model=WorkflowAnatomy,
)
async def update_workflow_agent_model(
    workflow_id: str,
    payload: WorkflowAgentModelUpdate,
    conn: ConnDep,
    _: DemoModeCheck,
) -> WorkflowAnatomy:
    """Switch the agent model for one workflow.

    The slug is `provider:model` (e.g. `anthropic:claude-sonnet-4-6`).
    Validated against the runtime-enabled allowlist defined by
    `OWNEVO_PROVIDER_*_ENABLED` + `OWNEVO_PROVIDER_*_MODELS` env vars.
    A 422 response means the operator hasn't enabled that pair via
    `.env`; rejected slugs never reach the DB.

    On success, the change is recorded as a hash-chained audit entry
    of kind `workflow-agent-model-changed`. Phase 2 will wire the
    chosen slug through the iteration runner; today the column persists
    + audits but the loop still uses `OWNEVO_LLM_MODEL`.
    """
    new_slug = payload.agent_model_id.strip()
    if not is_model_allowed(new_slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Model not available. Contact your administrator to enable it.",
        )

    async with conn.transaction():
        row = await conn.fetchrow(
            """
            UPDATE workflows
            SET agent_model_id = $2
            WHERE id = $1
            RETURNING id, description, mode::text AS mode, kind, spec,
                      simulation_plan, metric_definition,
                      created_from_template, design_agent_log,
                      agent_model_id
            """,
            workflow_id,
            new_slug,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found",
            )
        await append_audit_entry(
            conn,
            kind=AuditKind.WORKFLOW_AGENT_MODEL_CHANGED,
            payload={
                "workflow_id": row["id"],
                "agent_model_id": row["agent_model_id"],
            },
            actor="api:patch-agent-model",
        )

    spec = decode_jsonb_obj(row["spec"]) or {}
    return WorkflowAnatomy(
        id=row["id"],
        description=row["description"],
        mode=row["mode"],
        kind=row["kind"],
        spec=spec,
        simulation_plan=decode_jsonb_obj(row["simulation_plan"]),
        metric_definition=decode_jsonb_obj(row["metric_definition"]),
        created_from_template=row["created_from_template"],
        design_agent_log=decode_jsonb_obj(row["design_agent_log"]),
        agent_model_id=row["agent_model_id"],
    )


@router.delete("/{workflow_id}", response_model=WorkflowDeleteResponse)
async def delete_workflow(
    workflow_id: str,
    conn: ConnDep,
    _: DemoModeCheck,
) -> WorkflowDeleteResponse:
    """Hard-delete a workflow and every domain row tied to it.

    Cascades manually in FK-safe order inside one transaction:
    approvals → learnings → proposals → traces → meta_evals →
    iterations → eval_cases → failure_clusters → skill_versions /
    skill_deployments / skills → workflow.

    Audit entries are intentionally NOT deleted — `audit_entries` is
    append-only WORM at the DB level (D2). Existing `related_id`s
    pointing at the deleted rows become dangling pointers; the
    workspace-level audit view still shows them, but the workflow
    audit filter (which joins back through iterations / proposals /
    clusters) drops them silently. That mirrors the customer-export
    contract: history of the deletion's *consequences* survives even
    after the workflow itself is gone.
    """
    exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1",
        workflow_id,
    )
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    counts: dict[str, int] = {}
    async with conn.transaction():
        # Resolve iteration ids once — used by approvals / learnings / proposals.
        iter_ids: list = [
            r["id"]
            for r in await conn.fetch(
                "SELECT id FROM iterations WHERE workflow_id = $1",
                workflow_id,
            )
        ]

        counts["approvals"] = await _count_exec(
            conn,
            """
            DELETE FROM approvals
            WHERE proposal_id IN (
                SELECT id FROM proposals WHERE iteration_id = ANY($1::uuid[])
            )
            """,
            iter_ids,
        )
        counts["learnings"] = await _count_exec(
            conn,
            "DELETE FROM learnings WHERE iteration_id = ANY($1::uuid[])",
            iter_ids,
        )
        counts["proposals"] = await _count_exec(
            conn,
            "DELETE FROM proposals WHERE iteration_id = ANY($1::uuid[])",
            iter_ids,
        )
        counts["traces"] = await _count_exec(
            conn,
            "DELETE FROM traces WHERE workflow_id = $1",
            workflow_id,
        )
        counts["meta_evals"] = await _count_exec(
            conn,
            "DELETE FROM meta_evals WHERE workflow_id = $1",
            workflow_id,
        )
        counts["iterations"] = await _count_exec(
            conn,
            "DELETE FROM iterations WHERE workflow_id = $1",
            workflow_id,
        )
        counts["eval_cases"] = await _count_exec(
            conn,
            "DELETE FROM eval_cases WHERE workflow_id = $1",
            workflow_id,
        )
        counts["failure_clusters"] = await _count_exec(
            conn,
            "DELETE FROM failure_clusters WHERE workflow_id = $1",
            workflow_id,
        )

        # skills.head_version_id -> skill_versions.id and
        # skill_versions.skill_id -> skills.id form a cycle. Break it by
        # NULLing head_version_id before deleting versions, then deleting
        # versions, then deleting the skills themselves.
        skill_ids: list = [
            r["id"]
            for r in await conn.fetch(
                "SELECT id FROM skills WHERE workflow_id = $1",
                workflow_id,
            )
        ]
        if skill_ids:
            await conn.execute(
                "UPDATE skills SET head_version_id = NULL, deployed_version_id = NULL "
                "WHERE id = ANY($1::text[])",
                skill_ids,
            )
            await conn.execute(
                "UPDATE workflows SET sim_skill_id = NULL WHERE id = $1",
                workflow_id,
            )
            await conn.execute(
                "DELETE FROM skill_deployments WHERE skill_id = ANY($1::text[])",
                skill_ids,
            )
        counts["skill_versions"] = await _count_exec(
            conn,
            "DELETE FROM skill_versions WHERE skill_id = ANY($1::text[])",
            skill_ids,
        )
        counts["skills"] = await _count_exec(
            conn,
            "DELETE FROM skills WHERE id = ANY($1::text[])",
            skill_ids,
        )

        await conn.execute("DELETE FROM workflows WHERE id = $1", workflow_id)

    return WorkflowDeleteResponse(id=workflow_id, **counts)


async def _count_exec(conn: asyncpg.Connection, sql: str, *args) -> int:
    """Run a DELETE and return the row count. asyncpg's `execute` returns
    a tag string like `"DELETE 17"` — parse the int off the end."""
    tag = await conn.execute(sql, *args)
    parts = tag.split()
    if len(parts) >= 2 and parts[-1].isdigit():
        return int(parts[-1])
    return 0


def _row_to_summary(row: asyncpg.Record) -> WorkflowSummary:
    return WorkflowSummary(
        id=row["id"],
        description=row["description"],
        mode=row["mode"],
        kind=row.get("kind"),
        iteration_count=row["iteration_count"],
        running_iteration_count=row.get("running_iteration_count", 0) or 0,
        oldest_running_started_at=row.get("oldest_running_started_at"),
        best_ever_score=(
            float(row["best_ever_score"]) if row["best_ever_score"] is not None else None
        ),
        last_improved_at=row["last_improved_at"],
        pending_proposals_count=row["pending_proposals_count"],
    )
