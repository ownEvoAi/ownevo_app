"""Run one improvement-loop iteration against an NL-gen'd workflow.

PLAN row 8.4.5. Reads the workflow's persisted spec / sim_plan /
metric_def + eval_cases from the DB, drives one cycle of
`nl_gen.loop.run_nl_gen_demo_loop` (agent solver over the case set,
failure clustering, instruction proposer), and persists the result as
an iteration row + a new skill version + a proposal row + audit entry.

Workflow-agnostic: any workflow with a populated spec / simulation_plan
/ metric_definition row plus seeded eval cases can run an iteration.
Legacy code-driven workflows (m5-demand-prediction, tau3-retail-v1) still
have their own dedicated runners and do NOT go through this path.

The runner is intentionally one-cycle-at-a-time so the UI button maps
1:1 to a kernel call. Multi-cycle / batched runs stay in
`scripts/nl_gen_demo_loop.py`.
"""

from __future__ import annotations

import asyncio
import json
import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncpg

from .audit.writer import append_audit_entry
from .clustering.persistence import insert_cluster
from .clustering.types import ClusterSummary
from .eval_cases.registry import list_eval_cases
from .eval_runner.runner import EvalCaseOutcome
from .llm.router import RouterError, build_chat_client
from .nl_gen.eval_case_set import EvalCaseSet, GeneratedEvalCase
from .nl_gen.failure_clustering import NLGenFailureSnapshot
from .nl_gen.instruction_proposer import (
    InstructionEditValidationError,
    NoInstructionEditToolUseError,
)
from .nl_gen.loop import CycleOutcome, run_nl_gen_demo_loop
from .nl_gen.metric_def import MetricDefinition
from .nl_gen.sim_plan import SimulationPlan
from .nl_gen.spec import Provenance, WorkflowSpec
from .skills.registry import register_skill
from .types import AuditKind, EvalCase, IterationState, ProposalState

if TYPE_CHECKING:  # pragma: no cover
    from anthropic import AsyncAnthropic

    from .sim_tier import MockSimConfig


_INSTRUCTION_SKILL_KIND = "instruction"
_ITERATION_ACTOR = "nl-gen-iteration-runner"


class IterationRunnerError(RuntimeError):
    """Recoverable failure surfaced to the API layer as a 4xx/5xx."""


class WorkflowNotIterableError(IterationRunnerError):
    """Workflow row is missing spec / simulation_plan / metric_definition
    or has no eval cases. The caller should regenerate the missing piece
    via the gen / eval-cases endpoints before retrying."""


@dataclass(frozen=True)
class IterationOutcome:
    """What the API returns to the caller."""

    iteration_id: UUID
    iteration_index: int
    state: str  # IterationState value
    val_score: float
    n_cases: int
    n_failed: int
    proposed_skill_id: str | None
    proposed_skill_version_id: UUID | None
    proposed_instruction: str | None
    proposal_id: UUID | None


def _instruction_skill_id(workflow_id: str) -> str:
    """Deterministic skill id for the per-workflow instruction skill.

    One instruction-skill per workflow; each iteration that emits an
    edit becomes a new `skill_versions` row chained off the previous head.
    """
    return f"{workflow_id}.instruction"


def _build_skill_file(*, workflow_id: str, body: str) -> str:
    """Render the `skills/format.py` YAML-frontmatter file the registry
    expects for an instruction skill."""
    skill_id = _instruction_skill_id(workflow_id)
    yaml_lines = [
        "---",
        f"id: {skill_id}",
        f"kind: {_INSTRUCTION_SKILL_KIND}",
        f"created_by: {_ITERATION_ACTOR}",
        "capability_tags: []",
        "retention:",
        "  remembers: []",
        "  refetches: []",
        "  stateless: true",
        "---",
        "",
    ]
    return "\n".join(yaml_lines) + body.strip() + "\n"


def _eval_cases_to_set(
    workflow_spec_id: str,
    cases: list[EvalCase],
) -> EvalCaseSet:
    """Reconstruct an `EvalCaseSet` from DB rows.

    The persistence layer (`nl_gen/eval_persistence.py`) splits each case
    across `input` (sim_seed, n_steps, target_step_index) and
    `expected_behavior` (case_id, target_label_field, expected_value,
    rationale, provenance). Undo the split here so the downstream
    agent_solver / loop see the original shape.
    """
    generated: list[GeneratedEvalCase] = []
    for c in cases:
        inp = c.input or {}
        eb = c.expected_behavior or {}
        prov = eb.get("provenance") or {}
        generated.append(
            GeneratedEvalCase(
                case_id=str(eb.get("case_id") or c.id),
                provenance=Provenance(
                    kind=prov.get("kind", "inferred"),
                    source=prov.get("source", "iteration-runner-fallback"),
                ),
                sim_seed=int(inp.get("sim_seed", 0)),
                n_steps=int(inp.get("n_steps", 1)),
                target_step_index=int(inp.get("target_step_index", 0)),
                target_label_field=str(eb.get("target_label_field") or "label"),
                expected_value=bool(eb.get("expected_value", False)),
                rationale=str(eb.get("rationale") or "(no rationale recorded)"),
                is_test_fold=c.is_test_fold,
            )
        )
    return EvalCaseSet(
        workflow_spec_id=workflow_spec_id,
        simulation_plan_workflow_id=workflow_spec_id,
        cases=generated,
    )


async def _load_artifacts(
    conn: asyncpg.Connection,
    workflow_id: str,
) -> tuple[WorkflowSpec, SimulationPlan, MetricDefinition, EvalCaseSet]:
    """Pull the four NL-gen artifacts the loop needs.

    Raises `WorkflowNotIterableError` when any piece is missing — the API
    layer maps that to a 409 so the UI can point the user at the gen /
    eval-cases generation buttons.
    """
    row = await conn.fetchrow(
        """
        SELECT id, spec, simulation_plan, metric_definition
        FROM workflows
        WHERE id = $1
        """,
        workflow_id,
    )
    if row is None:
        raise WorkflowNotIterableError(f"workflow {workflow_id!r} not found")

    spec_dict = _coerce_jsonb(row["spec"])
    sim_dict = _coerce_jsonb(row["simulation_plan"])
    metric_dict = _coerce_jsonb(row["metric_definition"])

    if not spec_dict:
        raise WorkflowNotIterableError(
            f"workflow {workflow_id!r} has no spec — generate one first."
        )
    if not sim_dict:
        raise WorkflowNotIterableError(
            f"workflow {workflow_id!r} has no simulation_plan — re-run "
            "POST /api/nl-gen/generate to populate it."
        )
    if not metric_dict:
        raise WorkflowNotIterableError(
            f"workflow {workflow_id!r} has no metric_definition — re-run "
            "POST /api/nl-gen/generate to populate it."
        )

    spec = WorkflowSpec.model_validate(spec_dict)
    sim_plan = SimulationPlan.model_validate(sim_dict)
    metric = MetricDefinition.model_validate(metric_dict)

    cases = await list_eval_cases(conn, workflow_id=workflow_id)
    if not cases:
        raise WorkflowNotIterableError(
            f"workflow {workflow_id!r} has no eval cases — generate them "
            "from the Eval cases tab first."
        )
    case_set = _eval_cases_to_set(spec.id, cases)
    return spec, sim_plan, metric, case_set


def _coerce_jsonb(value: object) -> dict | None:
    """asyncpg may return JSONB as dict or as raw JSON string depending on
    the codec wired on the pool. Accept both."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        return json.loads(value)
    raise TypeError(f"unexpected JSONB payload type: {type(value).__name__}")


async def _current_head_instruction(
    conn: asyncpg.Connection,
    workflow_id: str,
) -> str | None:
    """The cumulative instruction the agent will run with as iteration_before.

    None for the bootstrap case (no instruction skill exists yet for this
    workflow). Returns the head version's body (without the YAML
    frontmatter) so the loop receives a plain instruction string.
    """
    skill_id = _instruction_skill_id(workflow_id)
    row = await conn.fetchrow(
        """
        SELECT sv.content
        FROM skills s
        JOIN skill_versions sv ON sv.id = s.head_version_id
        WHERE s.id = $1
        """,
        skill_id,
    )
    if row is None:
        return None
    content = row["content"]
    # Strip the YAML frontmatter block — keep just the body.
    if content.startswith("---"):
        # `---\n…\n---\n<body>` — split on the second `---\n`.
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n").rstrip() or None
    return content.strip() or None


async def _next_iteration_index(
    conn: asyncpg.Connection,
    workflow_id: str,
) -> int:
    row = await conn.fetchval(
        "SELECT COALESCE(MAX(iteration_index), -1) + 1 FROM iterations WHERE workflow_id = $1",
        workflow_id,
    )
    return int(row)


async def _pending_steering_for_workflow(
    conn: asyncpg.Connection,
    workflow_id: str,
) -> str | None:
    """Steering text from the most recent `changes-requested` proposal.

    When a domain expert clicks "Request changes" on a gate-passed
    proposal, the comment lands on `approvals.comment` and the proposal
    transitions to `changes-requested`. The next iteration on this
    workflow injects that comment into the loop's
    `initial_instruction` so the agent + proposer see the redirect.

    Returns the most recent steering comment across all proposals in
    this workflow, or None if no steering is pending. We don't filter
    by "newer than last iteration" — the latest steering always wins,
    and once the iteration completes the operator can request changes
    again on the new proposal.
    """
    row = await conn.fetchval(
        """
        SELECT a.comment
        FROM approvals a
        JOIN proposals p ON p.id = a.proposal_id
        JOIN iterations i ON i.id = p.iteration_id
        WHERE i.workflow_id = $1
          AND a.decision = 'request-changes'
          AND a.comment IS NOT NULL
        ORDER BY a.decided_at DESC
        LIMIT 1
        """,
        workflow_id,
    )
    if row is None:
        return None
    text = str(row).strip()
    return text or None


async def _load_mock_sim_config(
    conn: asyncpg.Connection,
    workflow_id: str,
) -> MockSimConfig | None:
    """Read sim_tier + mock_sim_config off the workflows row.

    Returns a parsed `MockSimConfig` when `sim_tier='mock'`. Returns
    `None` for any other tier (or when the workflow has no row, which
    a downstream lookup will fail on first). The migration 0018 CHECK
    constraint guarantees that `sim_tier='mock'` rows have non-NULL
    `mock_sim_config`, so a missing payload at this layer is a
    schema-drift bug worth raising.

    Lazy-imports MockSimConfig so the iteration_runner module stays
    importable without pulling pydantic into every consumer (matches
    the chat_handle pattern further down).
    """
    from .sim_tier import MockSimConfig

    row = await conn.fetchrow(
        "SELECT sim_tier, mock_sim_config FROM workflows WHERE id = $1",
        workflow_id,
    )
    if row is None or row["sim_tier"] != "mock":
        return None
    raw = _coerce_jsonb(row["mock_sim_config"])
    if raw is None:
        raise WorkflowNotIterableError(
            f"workflow {workflow_id!r} has sim_tier='mock' but "
            "mock_sim_config is NULL — the migration 0018 CHECK "
            "constraint should have caught this; investigate.",
        )
    return MockSimConfig.model_validate(raw)


async def run_one_iteration_for_workflow(
    pool: asyncpg.Pool,
    *,
    workflow_id: str,
    client: AsyncAnthropic,
) -> IterationOutcome:
    """Run one cycle and persist its outcome.

    Accepts a pool rather than a bare connection so the DB connection is
    not held during the 30-90s LLM window. Three phases:

    1. Pre-LLM (brief): load artifacts, insert 'running' iteration row.
       Connection acquired and released before LLM calls begin.
    2. LLM calls: agent solver + proposer. No DB connection held.
    3. Persistence (brief): write traces, clusters, skill version, proposal,
       update iteration state — all inside one transaction.

    On any failure in phase 2 or 3 the iteration row is updated to
    'sandbox-error' via a fresh connection (outside any rolled-back
    transaction) before re-raising.
    """
    # --- Phase 1: pre-LLM setup (connection released before LLM starts) ---
    async with pool.acquire() as conn:
        spec, sim_plan, metric, case_set = await _load_artifacts(conn, workflow_id)
        instruction_before = await _current_head_instruction(conn, workflow_id)
        parent_skill_version_id = await _current_head_version_id(conn, workflow_id)
        iteration_index = await _next_iteration_index(conn, workflow_id)
        agent_model_slug = await conn.fetchval(
            "SELECT agent_model_id FROM workflows WHERE id = $1",
            workflow_id,
        )
        pending_steering = await _pending_steering_for_workflow(conn, workflow_id)
        mock_config = await _load_mock_sim_config(conn, workflow_id)

        started_at = datetime.now(UTC)

        # Insert the iteration row immediately in 'running' state so the UI
        # can show progress even while the LLM calls are mid-flight.
        iteration_id: UUID = await conn.fetchval(
            """
            INSERT INTO iterations (
                workflow_id, iteration_index, state,
                parent_skill_version_id, started_at
            )
            VALUES ($1, $2, 'running'::iteration_state, $3, $4)
            RETURNING id
            """,
            workflow_id,
            iteration_index,
            parent_skill_version_id,
            started_at,
        )

        await append_audit_entry(
            conn,
            kind=AuditKind.GATE_RUN_STARTED,
            actor=_ITERATION_ACTOR,
            related_id=iteration_id,
            payload={
                "workflow_id": workflow_id,
                "iteration_index": iteration_index,
                "n_cases": len(case_set.cases),
                "parent_skill_version_id": (
                    str(parent_skill_version_id) if parent_skill_version_id else None
                ),
            },
        )
    # connection released back to pool here

    # --- Phase 2: LLM calls (no DB connection held) ---
    # n_cycles=2: cycle 0 runs agent + proposes an instruction edit,
    # cycle 1 runs the agent against the new instruction. We only persist
    # cycle 0's outcome — cycle 1 exists so that `is_last` doesn't
    # suppress the proposer call. The agent run on cycle 1 is wasted
    # work but ~doubles the latency, not 10×.
    #
    # If the proposer flakes (the InstructionEdit schema is strict and
    # the LLM occasionally emits a malformed edit), fall back to a
    # one-cycle run so we still capture the agent's score. The iteration
    # lands as no-improvement rather than 502'ing.
    # Resolve the workflow's per-workflow model choice into agent-side
    # overrides for run_nl_gen_demo_loop. The proposer always runs on
    # the env-Anthropic `client` (NL-gen is Anthropic-only today); the
    # picker only swaps the agent solver. A disabled provider or
    # missing API key surfaces as RouterError → 409 to the API layer.
    # Mock-tier short-circuits both `agent_model` (no LLM dispatch) and
    # `agent_openai_client` (no client at all). The mock_config +
    # mock_iteration_index pair drives the MockAgentSolver substitution
    # inside `run_nl_gen_demo_loop`.
    agent_overrides: dict[str, Any] = {
        "agent_model": None,
        "agent_openai_client": None,
        "mock_config": mock_config,
        "mock_iteration_index": iteration_index if mock_config is not None else None,
    }
    if pending_steering:
        # Surface the steering as a labelled bullet so the agent's
        # per-workflow-instruction block reads cleanly and the proposer
        # downstream can pattern-match on "Domain expert steering".
        agent_overrides["initial_instruction"] = (
            f"Domain expert steering (from a Request changes decision): "
            f"{pending_steering}"
        )
    chat_handle = None
    if agent_model_slug:
        try:
            chat_handle = build_chat_client(agent_model_slug)
        except RouterError as exc:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE iterations
                    SET state = 'sandbox-error'::iteration_state,
                        ended_at = now()
                    WHERE id = $1
                    """,
                    iteration_id,
                )
            raise WorkflowNotIterableError(str(exc)) from exc
        agent_overrides["agent_model"] = chat_handle.model
        if chat_handle.openai_client is not None:
            agent_overrides["agent_openai_client"] = chat_handle.openai_client

    cycle: CycleOutcome
    try:
        try:
            report = await run_nl_gen_demo_loop(
                spec=spec,
                plan=sim_plan,
                case_set=case_set,
                metric=metric,
                client=client,
                n_cycles=2,
                **agent_overrides,
            )
            if not report.cycles:
                raise IterationRunnerError("loop produced no cycles")
            cycle = report.cycles[0]
        except (InstructionEditValidationError, NoInstructionEditToolUseError):
            # Proposer flaked — re-run with n_cycles=1 to get the score
            # without invoking the proposer at all.
            try:
                fallback_report = await run_nl_gen_demo_loop(
                    spec=spec,
                    plan=sim_plan,
                    case_set=case_set,
                    metric=metric,
                    client=client,
                    n_cycles=1,
                    **agent_overrides,
                )
            except (Exception, asyncio.CancelledError):
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE iterations
                        SET state = 'sandbox-error'::iteration_state,
                            ended_at = now()
                        WHERE id = $1
                        """,
                        iteration_id,
                    )
                raise
            if not fallback_report.cycles:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE iterations
                        SET state = 'sandbox-error'::iteration_state,
                            ended_at = now()
                        WHERE id = $1
                        """,
                        iteration_id,
                    )
                raise IterationRunnerError("fallback loop produced no cycles") from None
            cycle = fallback_report.cycles[0]
        except (Exception, asyncio.CancelledError):
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE iterations
                    SET state = 'sandbox-error'::iteration_state,
                        ended_at = now()
                    WHERE id = $1
                    """,
                    iteration_id,
                )
            raise
    finally:
        if chat_handle is not None:
            await chat_handle.aclose()
    val_score = cycle.metric_value
    n_failed = cycle.n_failures
    cycle_ended_at = datetime.now(UTC)

    # --- Phase 3: persistence (fresh connection, single transaction) ---
    # All persistence runs inside one transaction so a partial write
    # never leaves the iteration in an inconsistent state. On any
    # failure (including asyncio.CancelledError from a dropped HTTP
    # connection) the transaction rolls back and we mark the iteration
    # row sandbox-error in a separate autocommit statement.
    final_state: str = IterationState.GATE_BLOCKED_NO_IMPROVEMENT.value
    dominant_cluster_id: UUID | None = None
    proposed_skill_id: str | None = None
    proposed_skill_version_id: UUID | None = None
    proposal_id: UUID | None = None
    new_instruction: str | None = cycle.instruction_after
    persisted_clusters: list = []

    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                case_id_to_trace_id = await _persist_traces(
                    conn,
                    workflow_id=workflow_id,
                    iteration_id=iteration_id,
                    skill_version_id=parent_skill_version_id,
                    outcomes=cycle.outcomes,
                    started_at=started_at,
                    ended_at=cycle_ended_at,
                )

                await _persist_case_outputs(
                    conn,
                    iteration_id=iteration_id,
                    workflow_id=workflow_id,
                    outcomes=cycle.outcomes,
                )

                if cycle.clustering_result is not None and cycle.clusters:
                    for summary in cycle.clusters:
                        sample_ids = _sample_trace_ids_for_cluster(
                            summary, cycle.snapshots, case_id_to_trace_id
                        )
                        persisted = await insert_cluster(
                            conn,
                            workflow_id=workflow_id,
                            summary=summary,
                            sample_trace_ids=sample_ids,
                        )
                        persisted_clusters.append(persisted)
                        await append_audit_entry(
                            conn,
                            kind=AuditKind.CLUSTER_CREATED,
                            actor=_ITERATION_ACTOR,
                            related_id=persisted.id,
                            payload={
                                "workflow_id": workflow_id,
                                "iteration_id": str(iteration_id),
                                "label": summary.label,
                                "severity": summary.severity,
                                "cluster_size": len(summary.member_indices),
                                "sample_trace_ids": [str(t) for t in sample_ids],
                            },
                        )

                    if persisted_clusters:
                        dominant = max(
                            persisted_clusters,
                            key=lambda p: (len(p.summary.member_indices), p.summary.label),
                        )
                        dominant_cluster_id = dominant.id

                if new_instruction and new_instruction.strip() and new_instruction != instruction_before:
                    skill_file = _build_skill_file(workflow_id=workflow_id, body=new_instruction)
                    register_result = await register_skill(
                        conn,
                        skill_file,
                        created_by=f"nl-gen-iteration:{iteration_id}",
                        diff_summary=(
                            f"Iteration {iteration_index} on {workflow_id}: "
                            f"val_score {val_score:.3f}, {n_failed}/{len(case_set.cases)} failed"
                        ),
                    )
                    proposed_skill_id = register_result.skill_id
                    proposed_skill_version_id = register_result.version_id

                    await conn.execute(
                        "UPDATE skills SET workflow_id = $1 WHERE id = $2 AND workflow_id IS NULL",
                        workflow_id,
                        register_result.skill_id,
                    )

                    parent_version_id = await _parent_version_id(
                        conn, skill_id=register_result.skill_id, version_id=register_result.version_id
                    )

                    await append_audit_entry(
                        conn,
                        kind=AuditKind.SKILL_VERSION_CREATED,
                        actor=_ITERATION_ACTOR,
                        related_id=register_result.version_id,
                        payload={
                            "workflow_id": workflow_id,
                            "iteration_id": str(iteration_id),
                            "skill_id": register_result.skill_id,
                            "parent_version_id": (
                                str(parent_version_id) if parent_version_id else None
                            ),
                        },
                    )

                    proposal_id = await conn.fetchval(
                        """
                        INSERT INTO proposals (
                            iteration_id, skill_id, parent_version_id,
                            proposed_content, plain_language_summary,
                            state, eval_score
                        )
                        VALUES ($1, $2, $3, $4, $5, $6::proposal_state, $7)
                        RETURNING id
                        """,
                        iteration_id,
                        register_result.skill_id,
                        parent_version_id,
                        skill_file,
                        cycle.instruction_edit.appended_text
                        if cycle.instruction_edit
                        else "Iteration produced an instruction edit (no summary)",
                        ProposalState.GATE_PASSED.value,
                        float(val_score),
                    )

                    await append_audit_entry(
                        conn,
                        kind=AuditKind.PROPOSAL_CREATED,
                        actor=_ITERATION_ACTOR,
                        related_id=proposal_id,
                        payload={
                            "workflow_id": workflow_id,
                            "iteration_id": str(iteration_id),
                            "skill_id": register_result.skill_id,
                            "skill_version_id": str(register_result.version_id),
                            "val_score": float(val_score),
                            "n_failed": n_failed,
                            "n_cases": len(case_set.cases),
                        },
                    )

                final_state = (
                    IterationState.GATE_PASS.value
                    if proposal_id is not None
                    else IterationState.GATE_BLOCKED_NO_IMPROVEMENT.value
                )

                best_before = await conn.fetchval(
                    """
                    SELECT MAX(best_ever_score_after)
                    FROM iterations
                    WHERE workflow_id = $1
                      AND state <> 'running'::iteration_state
                      AND id <> $2
                    """,
                    workflow_id,
                    iteration_id,
                )
                best_before_float = float(best_before) if best_before is not None else None
                best_after = (
                    max(best_before_float, float(val_score))
                    if best_before_float is not None
                    else float(val_score)
                )

                await conn.execute(
                    """
                    UPDATE iterations
                    SET state = $2::iteration_state,
                        val_score = $3,
                        proposed_skill_version_id = $4,
                        best_ever_score_before = $5,
                        best_ever_score_after = $6,
                        cluster_id = $7,
                        ended_at = now()
                    WHERE id = $1
                    """,
                    iteration_id,
                    final_state,
                    float(val_score),
                    proposed_skill_version_id,
                    best_before_float,
                    best_after,
                    dominant_cluster_id,
                )

                await append_audit_entry(
                    conn,
                    kind=AuditKind.GATE_RUN_COMPLETED,
                    actor=_ITERATION_ACTOR,
                    related_id=iteration_id,
                    payload={
                        "workflow_id": workflow_id,
                        "iteration_index": iteration_index,
                        "val_score": float(val_score),
                        "n_cases": len(case_set.cases),
                        "n_failed": n_failed,
                        "state": final_state,
                        "n_clusters": len(persisted_clusters),
                        "dominant_cluster_id": (
                            str(dominant_cluster_id) if dominant_cluster_id else None
                        ),
                        "proposal_id": str(proposal_id) if proposal_id else None,
                        "proposed_skill_version_id": (
                            str(proposed_skill_version_id) if proposed_skill_version_id else None
                        ),
                    },
                )
        except (Exception, asyncio.CancelledError):
            await conn.execute(
                """
                UPDATE iterations
                SET state = 'sandbox-error'::iteration_state,
                    ended_at = now()
                WHERE id = $1
                """,
                iteration_id,
            )
            raise

    return IterationOutcome(
        iteration_id=iteration_id,
        iteration_index=iteration_index,
        state=final_state,
        val_score=float(val_score),
        n_cases=len(case_set.cases),
        n_failed=n_failed,
        proposed_skill_id=proposed_skill_id,
        proposed_skill_version_id=proposed_skill_version_id,
        proposed_instruction=new_instruction if new_instruction else None,
        proposal_id=proposal_id,
    )


async def _current_head_version_id(
    conn: asyncpg.Connection,
    workflow_id: str,
) -> UUID | None:
    """The skill_version_id currently deployed for this workflow's
    instruction skill — used as `parent_skill_version_id` on the iteration
    row and `skill_version_id` on per-case traces (so the trace inspector
    can show which version produced each prediction)."""
    return await conn.fetchval(
        """
        SELECT s.head_version_id
        FROM skills s
        WHERE s.id = $1
        """,
        _instruction_skill_id(workflow_id),
    )


def _trace_events_for_outcome(
    *,
    trace_id: UUID,
    iteration_id: UUID,
    outcome: EvalCaseOutcome,
    started_at: datetime,
    ended_at: datetime,
) -> list[dict[str, Any]]:
    """Build a minimal AgentEvent stream for one eval-case prediction.

    Matches `packages/trace-format/SPEC.md` v1.0: tool_call_start +
    tool_call_result for the forced `predict_label` tool. Real agent
    runs emit reasoning_delta / content_delta as well, but the iteration
    runner doesn't capture those today — TODO if we want richer traces.
    """
    base = {
        "trace_id": str(trace_id),
        "iteration_id": str(iteration_id),
        "parent_span_id": None,
    }
    call_id = f"call-{outcome.case_id}"
    return [
        {
            **base,
            "event_id": str(_uuid.uuid4()),
            "timestamp": started_at.isoformat(),
            "type": "tool_call_start",
            "call_id": call_id,
            "name": "predict_label",
            "args": {"case_id": outcome.case_id},
        },
        {
            **base,
            "event_id": str(_uuid.uuid4()),
            "timestamp": ended_at.isoformat(),
            "type": "tool_call_result",
            "call_id": call_id,
            "name": "predict_label",
            "status": "ok",
            "output": {
                "case_id": outcome.case_id,
                "predicted": bool(outcome.actual_value),
                "expected": bool(outcome.expected_value),
                "passed": bool(outcome.passed),
                "is_test_fold": bool(outcome.is_test_fold),
                "rationale": outcome.rationale,
            },
            "duration_ms": max(0, int((ended_at - started_at).total_seconds() * 1000)),
            "error": None,
            "error_class": None,
        },
    ]


async def _persist_case_outputs(
    conn: asyncpg.Connection,
    *,
    iteration_id: UUID,
    workflow_id: str,
    outcomes: tuple[EvalCaseOutcome, ...],
) -> None:
    """One row per case in `iteration_case_outputs` (PLAN row 8.4.9).

    `outcome.case_id` is a kebab-case string the eval-case rows carry
    inside their `input` jsonb. Resolve once via ANY() to keep this a
    single round-trip, then INSERT one row per case. The `output_json`
    shape mirrors what the trace's `metric_outputs` carries today;
    PLAN 8.4.10 widens it once the agent solver emits a workflow-
    specific `submit_case_output` tool.

    Idempotent under retry — `ON CONFLICT (iteration_id, eval_case_id)
    DO UPDATE`. If a case_id doesn't resolve to an `eval_cases` row
    (e.g. seed-data drift during dev), the row is skipped rather than
    failing the iteration — the trace + metric_outputs still capture
    the outcome.
    """
    if not outcomes:
        return
    case_ids = [o.case_id for o in outcomes]
    # case_id lives in `expected_behavior->>'case_id'` — A4.1's NL-gen
    # persistence routes the kebab-case identifier into expected_behavior
    # (see nl_gen/eval_persistence.py); `input` carries sim_seed / n_steps
    # / target_step_index instead. Mirror the same lookup the eval-cases
    # API endpoint uses (workflows.py:526).
    rows = await conn.fetch(
        """
        SELECT id, expected_behavior->>'case_id' AS case_id
        FROM eval_cases
        WHERE workflow_id = $1
          AND expected_behavior->>'case_id' = ANY($2::text[])
        """,
        workflow_id,
        case_ids,
    )
    case_id_to_uuid: dict[str, UUID] = {r["case_id"]: r["id"] for r in rows}
    for outcome in outcomes:
        eval_case_uuid = case_id_to_uuid.get(outcome.case_id)
        if eval_case_uuid is None:
            continue
        output_json = {
            "case_id": outcome.case_id,
            "predicted": _json_safe(outcome.actual_value),
            "expected": bool(outcome.expected_value),
            "rationale": outcome.rationale,
            "is_test_fold": bool(outcome.is_test_fold),
        }
        # Domain-shaped artifact for the Operate tab. Kept in its own
        # column rather than nested inside output_json so:
        #   * the gate-frame fields above stay schema-stable for the
        #     eval-prediction TableView on Overview,
        #   * the Operate resolver can do a tight `output_payload IS NOT
        #     NULL` check without scanning JSONB,
        #   * NULL clearly means "agent didn't emit one" instead of
        #     fighting with `output_json` keys that happen to be missing.
        payload_clean: dict[str, Any] | None = None
        if outcome.output_payload is not None:
            payload_clean = _json_safe(outcome.output_payload)
            if not isinstance(payload_clean, dict) or not payload_clean:
                payload_clean = None
        payload_arg = json.dumps(payload_clean) if payload_clean else None
        await conn.execute(
            """
            INSERT INTO iteration_case_outputs (
                iteration_id, eval_case_id, output_json, passed, output_payload
            )
            VALUES ($1, $2, $3::jsonb, $4, $5::jsonb)
            ON CONFLICT (iteration_id, eval_case_id) DO UPDATE
                SET output_json = EXCLUDED.output_json,
                    passed = EXCLUDED.passed,
                    output_payload = EXCLUDED.output_payload
            """,
            iteration_id,
            eval_case_uuid,
            json.dumps(output_json),
            bool(outcome.passed),
            payload_arg,
        )


def _json_safe(value: Any) -> Any:
    """Coerce arbitrary agent output into something json.dumps accepts.

    `EvalCaseOutcome.actual_value` is typed `Any` — today it's bool, but
    once the agent solver emits structured `submit_case_output` args
    (PLAN 8.4.10) it'll be dict / list / mixed. Built-in JSON types pass
    through; anything else falls back to `str()` so the row still writes
    rather than crashing the iteration.
    """
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


async def _persist_traces(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    iteration_id: UUID,
    skill_version_id: UUID | None,
    outcomes: tuple[EvalCaseOutcome, ...],
    started_at: datetime,
    ended_at: datetime,
) -> dict[str, UUID]:
    """One traces row per case. Returns case_id → trace_id so the
    cluster-persistence step can fill `sample_trace_ids`."""
    case_id_to_trace_id: dict[str, UUID] = {}
    for outcome in outcomes:
        trace_id = _uuid.uuid4()
        events = _trace_events_for_outcome(
            trace_id=trace_id,
            iteration_id=iteration_id,
            outcome=outcome,
            started_at=started_at,
            ended_at=ended_at,
        )
        await conn.execute(
            """
            INSERT INTO traces (
                id, workflow_id, iteration_id, skill_version_id,
                events, started_at, ended_at, metric_outputs
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8::jsonb)
            """,
            trace_id,
            workflow_id,
            iteration_id,
            skill_version_id,
            json.dumps(events),
            started_at,
            ended_at,
            json.dumps(
                {
                    "case_id": outcome.case_id,
                    "predicted": bool(outcome.actual_value),
                    "expected": bool(outcome.expected_value),
                    "passed": bool(outcome.passed),
                    "is_test_fold": bool(outcome.is_test_fold),
                    "rationale": outcome.rationale,
                }
            ),
        )
        case_id_to_trace_id[outcome.case_id] = trace_id
    return case_id_to_trace_id


def _sample_trace_ids_for_cluster(
    cluster: ClusterSummary,
    snapshots: tuple[NLGenFailureSnapshot, ...],
    case_id_to_trace_id: dict[str, UUID],
    limit: int = 5,
) -> list[UUID]:
    """Resolve the cluster's member snapshot positions back to trace_ids.

    `member_indices` are positions into `snapshots`; each snapshot
    carries a `case_id`; each case_id has a trace_id from the per-case
    persistence step.
    """
    out: list[UUID] = []
    for idx in cluster.member_indices[:limit]:
        if idx >= len(snapshots):
            continue
        case_id = snapshots[idx].case_id
        trace_id = case_id_to_trace_id.get(case_id)
        if trace_id is not None:
            out.append(trace_id)
    return out


async def _parent_version_id(
    conn: asyncpg.Connection,
    *,
    skill_id: str,
    version_id: UUID,
) -> UUID | None:
    """The skill_version row just inserted carries `parent_version_id`;
    pull it back out so the proposal references the same parent."""
    return await conn.fetchval(
        "SELECT parent_version_id FROM skill_versions WHERE id = $1 AND skill_id = $2",
        version_id,
        skill_id,
    )


__all__ = [
    "IterationOutcome",
    "IterationRunnerError",
    "WorkflowNotIterableError",
    "run_one_iteration_for_workflow",
]
