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

from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, status

from ..deps import ConnDep
from ..jsonb import decode_jsonb_obj
from ..models import (
    CaseOutputList,
    CaseOutputRow,
    EvalCaseCreate,
    EvalCaseList,
    EvalCaseSummary,
    FailureClusterList,
    FailureClusterSummary,
    GenerateEvalCasesResponse,
    IterationCaseRow,
    IterationDetailFull,
    IterationList,
    IterationPoint,
    RunIterationResponse,
    WorkflowAnatomy,
    WorkflowDeleteResponse,
    WorkflowList,
    WorkflowSummary,
    WorkflowUpdate,
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
        SELECT id, description, mode::text AS mode, kind, spec
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
    spec = decode_jsonb_obj(row["spec"]) or {}
    return WorkflowAnatomy(
        id=row["id"],
        description=row["description"],
        mode=row["mode"],
        kind=row["kind"],
        spec=spec,
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

    # `latest_proposal_id` (W7 slice 7 / 7.1.4): correlated subquery
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

    # Resolve spawning_iteration per cluster in a single follow-up
    # query: pick the earliest iteration_index whose traces overlap
    # any of the cluster's sample_trace_ids. Done in Python (not as
    # a correlated subquery) because asyncpg's ANY($1::uuid[]) handling
    # of NULL/empty arrays is brittle and a single batched lookup
    # over the union of all sample ids is straightforward.
    all_sample_ids: list[UUID] = []
    for r in rows:
        for tid in r["sample_trace_ids"] or []:
            all_sample_ids.append(tid)
    spawning_by_cluster: dict[UUID, tuple[int, UUID]] = {}
    if all_sample_ids:
        trace_iter_rows = await conn.fetch(
            """
            SELECT t.id AS trace_id, i.id AS iter_id, i.iteration_index
            FROM traces t
            JOIN iterations i ON i.id = t.iteration_id
            WHERE t.id = ANY($1::uuid[])
              AND t.iteration_id IS NOT NULL
            """,
            all_sample_ids,
        )
        trace_to_iter: dict[UUID, tuple[int, UUID]] = {
            row["trace_id"]: (row["iteration_index"], row["iter_id"])
            for row in trace_iter_rows
        }
        for r in rows:
            best: tuple[int, UUID] | None = None
            for tid in r["sample_trace_ids"] or []:
                resolved = trace_to_iter.get(tid)
                if resolved is None:
                    continue
                if best is None or resolved[0] < best[0]:
                    best = resolved
            if best is not None:
                spawning_by_cluster[r["id"]] = best

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
        )
        for r in rows
    ]
    return FailureClusterList(workflow_id=workflow_id, items=items)


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
    from ..jsonb import decode_jsonb_obj

    cases = await list_eval_cases(conn, workflow_id=workflow_id)
    items: list[EvalCaseSummary] = []
    for c in cases:
        eb = c.expected_behavior or {}
        inp = c.input or {}
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
            )
        )
    # decode_jsonb_obj imported above is a no-op here — kept available for
    # any future per-field decode the registry doesn't do.
    _ = decode_jsonb_obj
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
    binds the response to `Primitive.TableView.binding.source =
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
            )
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
            ico.passed,
            ico.created_at,
            ec.input        AS input,
            ec.expected_behavior AS expected_behavior,
            ec.is_test_fold AS is_test_fold,
            ec.expected_behavior->>'case_id' AS case_id
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
            "provenance": {
                "kind": "inferred",
                "source": "hand-authored",
            },
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
) -> None:
    """Remove one eval case from the suite.

    Case ids are UUIDs from the `eval_cases` table — accepted as `str`
    here so FastAPI's path parsing doesn't reject mid-format. We resolve
    the row by (workflow_id, id) so cross-workflow deletion is impossible
    even if a UUID is reused (it can't be — UUIDs — but the constraint
    is defensive). Returns 404 if the case doesn't exist or doesn't
    belong to the workflow.
    """
    from uuid import UUID

    try:
        case_uuid = UUID(case_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eval case not found",
        ) from exc

    tag = await conn.execute(
        "DELETE FROM eval_cases WHERE id = $1 AND workflow_id = $2",
        case_uuid,
        workflow_id,
    )
    parts = tag.split()
    deleted = len(parts) >= 2 and parts[-1].isdigit() and int(parts[-1]) > 0
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

    from ...nl_gen.eval_generator import generate_eval_case_set
    from ...nl_gen.eval_persistence import persist_eval_case_set
    from ...nl_gen.metric_generator import generate_metric_definition
    from ...nl_gen.sim_generator import generate_simulation_plan
    from ...nl_gen.spec import WorkflowSpec
    from ..jsonb import decode_jsonb_obj

    row = await conn.fetchrow(
        """
        SELECT id, spec,
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

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    try:
        sim_plan = await generate_simulation_plan(client, workflow_spec)
        case_set = await generate_eval_case_set(client, workflow_spec, sim_plan)
        # Backfill metric_definition too when the workflow was created
        # without one — historical rows (PR #85-era nl-gen) sometimes
        # landed with simulation_plan/metric_definition NULL and the
        # iteration runner refuses to run without both.
        metric_def = None
        if not row["has_metric"]:
            metric_def = await generate_metric_definition(client, workflow_spec)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM-side failure during eval-case generation: {exc}",
        ) from exc

    inserted = await persist_eval_case_set(conn, case_set, workflow_id=workflow_id)
    # Persist the freshly generated sim_plan (always — the in-memory
    # generation just happened, so the DB column should match) and
    # metric_definition (only when one didn't exist before).
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
    conn: ConnDep,
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

    Errors:
      * **404** — workflow not found
      * **409** — workflow missing spec / sim_plan / metric / eval cases
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

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    try:
        outcome = await run_one_iteration_for_workflow(
            conn,
            workflow_id=workflow_id,
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
        RETURNING id, description, mode::text AS mode, kind, spec
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
    )


@router.delete("/{workflow_id}", response_model=WorkflowDeleteResponse)
async def delete_workflow(
    workflow_id: str,
    conn: ConnDep,
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
