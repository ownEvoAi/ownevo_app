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

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import asyncpg

from .eval_cases.registry import list_eval_cases
from .nl_gen.eval_case_set import EvalCaseSet, GeneratedEvalCase
from .nl_gen.instruction_proposer import (
    InstructionEditValidationError,
    NoInstructionEditToolUseError,
)
from .nl_gen.loop import CycleOutcome, run_nl_gen_demo_loop
from .nl_gen.metric_def import MetricDefinition
from .nl_gen.sim_plan import SimulationPlan
from .nl_gen.spec import Provenance, WorkflowSpec
from .skills.registry import register_skill
from .types import EvalCase, IterationState, ProposalState

if TYPE_CHECKING:  # pragma: no cover
    from anthropic import AsyncAnthropic


_INSTRUCTION_SKILL_KIND = "instruction"


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
    frontmatter = {
        "id": _instruction_skill_id(workflow_id),
        "kind": _INSTRUCTION_SKILL_KIND,
        "created_by": "nl-gen-iteration-runner",
        "capability_tags": [],
        "retention": {"remembers": [], "refetches": [], "stateless": True},
    }
    yaml_lines = [
        "---",
        f"id: {frontmatter['id']}",
        f"kind: {frontmatter['kind']}",
        f"created_by: {frontmatter['created_by']}",
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


async def run_one_iteration_for_workflow(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    client: AsyncAnthropic,
) -> IterationOutcome:
    """Run one cycle and persist its outcome.

    Caller-supplied `conn` should be an exclusive connection from the
    pool — the runner takes a transaction for the persistence step at
    the end. The agent + proposer LLM calls run outside the transaction.
    """
    spec, sim_plan, metric, case_set = await _load_artifacts(conn, workflow_id)
    instruction_before = await _current_head_instruction(conn, workflow_id)
    iteration_index = await _next_iteration_index(conn, workflow_id)

    started_at = datetime.now(UTC)

    # Insert the iteration row immediately in 'running' state so the UI
    # can show progress even while the LLM calls are mid-flight.
    iteration_id: UUID = await conn.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, started_at)
        VALUES ($1, $2, 'running'::iteration_state, $3)
        RETURNING id
        """,
        workflow_id,
        iteration_index,
        started_at,
    )

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
    cycle: CycleOutcome
    try:
        report = await run_nl_gen_demo_loop(
            spec=spec,
            plan=sim_plan,
            case_set=case_set,
            metric=metric,
            client=client,
            n_cycles=2,
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
            )
        except Exception:
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
    except Exception:
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
    val_score = cycle.metric_value
    n_failed = cycle.n_failures

    # Persist the new instruction as a skill version + open proposal.
    proposed_skill_id: str | None = None
    proposed_skill_version_id: UUID | None = None
    proposal_id: UUID | None = None
    new_instruction = cycle.instruction_after

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

        # Wire the workflow → skill ownership on the first iteration so
        # the skills library page shows it.
        await conn.execute(
            "UPDATE skills SET workflow_id = $1 WHERE id = $2 AND workflow_id IS NULL",
            workflow_id,
            register_result.skill_id,
        )

        parent_version_id = await _parent_version_id(
            conn, skill_id=register_result.skill_id, version_id=register_result.version_id
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

    final_state = (
        IterationState.GATE_PASS.value
        if proposal_id is not None
        else IterationState.GATE_BLOCKED_NO_IMPROVEMENT.value
    )

    await conn.execute(
        """
        UPDATE iterations
        SET state = $2::iteration_state,
            val_score = $3,
            proposed_skill_version_id = $4,
            ended_at = now()
        WHERE id = $1
        """,
        iteration_id,
        final_state,
        float(val_score),
        proposed_skill_version_id,
    )

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
