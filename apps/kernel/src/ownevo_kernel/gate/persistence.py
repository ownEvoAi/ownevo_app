"""DB-write wrapper around `run_gate` (W2.2 follow-up).

`run_gate` is a pure function — its decision is byte-for-byte derivable
from the runner output. `persist_gate_run` is what the agent loop
actually calls in production: it threads the gate decision into the
substrate's iteration / proposal / audit_entries tables so the lift
chart, audit log, and approval queue all have something to read.

Lifecycle (single transaction):

  1. Lock the workflow row (`SELECT … FOR UPDATE`) — serializes
     concurrent runs so the `iteration_index = MAX(existing)+1`
     allocation doesn't race against the
     `UNIQUE(workflow_id, iteration_index)` constraint.
  2. INSERT `iterations` row in `running` state.
  3. INSERT `proposals` row in `in-gate` state, linked to the iteration.
  4. Append `audit_entries` `gate-run-started`.
  5. Run the gate (`run_gate(runner, …)`).
  6. UPDATE the iteration with the gate's decision (state +
     val_score + best_ever_score_after + sandbox_error_class +
     ended_at).
  7. UPDATE the proposal with the matching state (gate-passed /
     gate-failed) + the gate's val_score as `eval_score` + the gate's
     rationale as `eval_rationale`.
  8. Append `audit_entries` `gate-run-completed` with the full gate
     evidence (rationale, val_score, failed_prior_task_ids,
     promotable_task_ids).

Promotable eval cases are **not** auto-written. The gate doesn't know
what `input` / `expected_behavior` to seed for a new case — that's
cluster-derived. Callers consume `gate_result.promotable_task_ids`
and call `add_eval_case` separately (the cluster→eval-case lift is
W3 work).

Long-running gates and the single-transaction posture
-----------------------------------------------------
Postgres holds the connection's MVCC snapshot for the whole
`async with conn.transaction()` block. For sandboxed M5 (~5s for the
synthetic fixture, ~minutes for full catalog) this is fine. For
multi-turn agent benchmarks (τ³) where one run can be tens of
minutes, callers can split this into two transactions by calling
`begin_iteration` + `finalize_iteration` directly (sketched as a
TODO; not in scope until W4 unattended replay demands it).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from ..audit import append_audit_entry
from ..benchmark import BenchmarkRunner
from ..types import (
    AuditKind,
    Iteration,
    IterationState,
    Proposal,
    ProposalState,
    SandboxErrorClass,
)
from .result import GateDecision, GateResult
from .runner import run_gate

# The W2.2 gate's decision values are wire-compatible with
# IterationState (deliberate — see GateDecision docstring). The
# wrapper still uses an explicit map so a future divergence in the two
# enums (e.g., a gate-only "in-progress" state) doesn't silently
# mistranslate. SANDBOX_ERROR maps via shared string value.
_DECISION_TO_ITERATION_STATE: dict[GateDecision, IterationState] = {
    GateDecision.PASS: IterationState.GATE_PASS,
    GateDecision.FAIL_REGRESSION: IterationState.GATE_BLOCKED_REGRESSION,
    GateDecision.FAIL_NO_IMPROVEMENT: IterationState.GATE_BLOCKED_NO_IMPROVEMENT,
    GateDecision.SANDBOX_ERROR: IterationState.SANDBOX_ERROR,
}

_DECISION_TO_PROPOSAL_STATE: dict[GateDecision, ProposalState] = {
    GateDecision.PASS: ProposalState.GATE_PASSED,
    # Logical rejections → `rejected` (agent caused this; counts toward
    # "3 failures on the same hypothesis → abandon" per STATE_MACHINES.md).
    GateDecision.FAIL_REGRESSION: ProposalState.REJECTED,
    GateDecision.FAIL_NO_IMPROVEMENT: ProposalState.REJECTED,
    # Technical failure → `gate-failed` (sandbox, not the agent). The
    # state machine designates `gate-failed` as the retry-allowing state
    # for infrastructure errors, distinct from `rejected` (logical).
    GateDecision.SANDBOX_ERROR: ProposalState.GATE_FAILED,
}


@dataclass(frozen=True)
class PersistedGateRun:
    """Result of `persist_gate_run` — the gate's decision plus the rows
    it wrote.

    Callers that need to drive the approval queue read `proposal.id`
    and `gate_result.passed`. Callers that need to extend the eval
    suite read `gate_result.promotable_task_ids` and call
    `add_eval_case` themselves.
    """

    gate_result: GateResult
    iteration: Iteration
    proposal: Proposal
    audit_started_id: UUID
    audit_completed_id: UUID


async def persist_gate_run(
    conn: asyncpg.Connection,
    runner: BenchmarkRunner,
    *,
    workflow_id: str,
    skill_id: str,
    proposed_content: str,
    plain_language_summary: str,
    actor: str,
    parent_version_id: UUID | None = None,
    proposed_skill_version_id: UUID | None = None,
    cluster_id: UUID | None = None,
    deployment_id: UUID | None = None,
    expected_impact: dict[str, Any] | None = None,
    prior_eval_task_ids: Sequence[str] = (),
    best_ever_score: float | None = None,
    regression_tolerance: float = 0.0,
    improvement_epsilon: float = 0.0,
) -> PersistedGateRun:
    """Run the gate and persist iteration + proposal + audit_entries atomically.

    Args:
        conn: an asyncpg connection. The whole call runs inside one
            transaction — caller should NOT wrap this in another
            `async with conn.transaction()` block.
        runner: any `BenchmarkRunner`; passed straight through to
            `run_gate`.
        workflow_id: the workflow this iteration belongs to. Must
            already exist in the `workflows` table.
        skill_id: the skill being mutated by this proposal.
        proposed_content: the proposed skill body. Stored verbatim on
            the proposal row.
        plain_language_summary: human-readable description of the
            change. Required by the proposals table; used by the
            approval UI.
        actor: who initiated this run (e.g., `agent:claude-opus-4-7`,
            `human:reviewer`). Goes on both audit entries.
        parent_version_id: the head skill version this proposal
            replaces. Optional — None on the bootstrap iteration.
        proposed_skill_version_id: id of the skill_version row for
            `proposed_content`, when caller pre-registered it. The
            wrapper does not register skill versions itself — that's
            the caller's responsibility (typically via
            `register_skill` before calling persist_gate_run, OR
            after the gate passes and the proposal is approved).
        cluster_id: which failure cluster triggered this iteration.
            Optional.
        deployment_id: which deployment config drove this iteration.
            Optional.
        expected_impact: agent-supplied JSON describing
            `{improves: [...eval_case_ids], regresses: [...]}`.
            Optional; surfaces on the proposal row for the UI.
        best_ever_score: used as the bootstrap fallback when no prior
            iterations exist for this workflow. For subsequent iterations the
            authoritative value is derived from ``MAX(best_ever_score_after)``
            inside the locked transaction, overriding this parameter, so
            concurrent callers always gate against the latest committed score.
        prior_eval_task_ids / regression_tolerance / improvement_epsilon:
            passed straight through to `run_gate`.

    Returns:
        `PersistedGateRun` with the gate result, the inserted /
        updated rows (refetched as Pydantic models), and the audit
        entry IDs.

    Raises:
        Whatever asyncpg raises if the workflow row doesn't exist or
        the connection is broken. Programming errors in `run_gate`
        (ValueError on bad threshold, etc.) propagate; the
        transaction rolls back, leaving no partial state.
    """
    if not (0.0 <= regression_tolerance <= 1.0):
        raise ValueError(
            f"regression_tolerance must be in [0,1]; got {regression_tolerance}",
        )
    if not (math.isfinite(improvement_epsilon) and improvement_epsilon >= 0.0):
        raise ValueError(
            f"improvement_epsilon must be finite and >= 0; got {improvement_epsilon}",
        )
    if best_ever_score is not None and not (0.0 <= best_ever_score <= 1.0):
        raise ValueError(
            f"best_ever_score must be in [0,1] or None; got {best_ever_score} "
            "(relax this guard if a future runner produces val_score > 1.0)",
        )
    # Serialize expected_impact before opening the transaction so a
    # non-serializable value raises TypeError without any DB writes.
    try:
        expected_impact_json = (
            json.dumps(expected_impact) if expected_impact is not None else None
        )
    except TypeError as exc:
        raise TypeError(
            f"expected_impact is not JSON-serializable: {exc}",
        ) from exc

    async with conn.transaction():
        # 1. Lock workflow row — serializes concurrent runs so the
        # iteration_index allocation below is collision-free.
        # 30-second lock_timeout prevents a stuck gate run from
        # holding the lock indefinitely and starving the connection pool.
        await conn.execute("SET LOCAL lock_timeout = '30s'")
        workflow_locked = await conn.fetchrow(
            "SELECT id FROM workflows WHERE id = $1 FOR UPDATE",
            workflow_id,
        )
        if workflow_locked is None:
            raise ValueError(
                f"workflow_id {workflow_id!r} does not exist; create it before "
                "running the gate persist wrapper",
            )

        next_idx = await conn.fetchval(
            "SELECT COALESCE(MAX(iteration_index), -1) + 1 "
            "FROM iterations WHERE workflow_id = $1",
            workflow_id,
        )

        # Derive the authoritative best_ever_score from the DB under lock so
        # concurrent callers always gate against the latest committed score, not
        # a snapshot that could be stale. Fall back to the caller-provided value
        # for the bootstrap iteration (no prior rows yet).
        db_best_ever = await conn.fetchval(
            "SELECT MAX(best_ever_score_after) FROM iterations "
            "WHERE workflow_id = $1 AND best_ever_score_after IS NOT NULL",
            workflow_id,
        )
        effective_best_ever = (
            _to_float(db_best_ever) if db_best_ever is not None else best_ever_score
        )

        # 2. Insert iteration in running state.
        iteration_row = await conn.fetchrow(
            """
            INSERT INTO iterations (
                workflow_id, iteration_index, state,
                proposed_skill_version_id, parent_skill_version_id,
                best_ever_score_before,
                cluster_id, deployment_id
            )
            VALUES ($1, $2, 'running'::iteration_state, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            workflow_id,
            next_idx,
            proposed_skill_version_id,
            parent_version_id,
            effective_best_ever,
            cluster_id,
            deployment_id,
        )
        iteration_id: UUID = iteration_row["id"]

        # 3. Insert proposal in in-gate state, linked to the iteration.
        proposal_row = await conn.fetchrow(
            """
            INSERT INTO proposals (
                iteration_id, skill_id, parent_version_id,
                proposed_content, plain_language_summary,
                expected_impact, state
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'in-gate'::proposal_state)
            RETURNING *
            """,
            iteration_id,
            skill_id,
            parent_version_id,
            proposed_content,
            plain_language_summary,
            expected_impact_json,
        )
        proposal_id: UUID = proposal_row["id"]

        # 4. Audit gate-run-started.
        started_entry = await append_audit_entry(
            conn,
            kind=AuditKind.GATE_RUN_STARTED,
            payload={
                "iteration_id": str(iteration_id),
                "iteration_index": next_idx,
                "workflow_id": workflow_id,
                "proposal_id": str(proposal_id),
                "skill_id": skill_id,
                "prior_eval_task_ids": list(prior_eval_task_ids),
                "best_ever_score": effective_best_ever,
                "regression_tolerance": regression_tolerance,
                "improvement_epsilon": improvement_epsilon,
            },
            actor=actor,
            related_id=iteration_id,
        )

        # 5. Run the gate. Any exception triggers transaction rollback —
        # no partial iteration / proposal rows survive.
        # NOTE(W4): connection held open for full benchmark duration.
        # For synthetic M5 (~5s) this is fine. For τ³ (tens of minutes),
        # split into begin_iteration + finalize_iteration. See module docstring.
        gate_result = await run_gate(
            runner,
            prior_eval_task_ids=prior_eval_task_ids,
            best_ever_score=effective_best_ever,
            regression_tolerance=regression_tolerance,
            improvement_epsilon=improvement_epsilon,
        )

        # 6. Finalize iteration with gate decision.
        iteration_state = _DECISION_TO_ITERATION_STATE[gate_result.decision]
        sandbox_error_class: SandboxErrorClass | None = None
        if gate_result.decision == GateDecision.SANDBOX_ERROR:
            sandbox_error_class = _infer_sandbox_error_class(gate_result)

        iteration_row = await conn.fetchrow(
            """
            UPDATE iterations
            SET state                  = $2::iteration_state,
                sandbox_error_class    = $3::sandbox_error_class,
                val_score              = $4,
                best_ever_score_after  = $5,
                ended_at               = now()
            WHERE id = $1
            RETURNING *
            """,
            iteration_id,
            iteration_state.value,
            sandbox_error_class.value if sandbox_error_class else None,
            gate_result.val_score,
            gate_result.best_ever_score_after,
        )

        # 7. Finalize proposal: state + eval_score + eval_rationale
        # capture what the gate observed so the approval UI can render
        # the rejection/admission rationale without re-running.
        proposal_state = _DECISION_TO_PROPOSAL_STATE[gate_result.decision]
        # eval_score is constrained to [0,1] by the proposal table CHECK.
        # The gate's val_score is in (0,1] for sandbox runners (M5 reward
        # = exp(-RMSSE)) but a future runner could exceed 1; clamp
        # defensively rather than raise.
        clamped_eval_score = (
            None if gate_result.val_score is None
            else max(0.0, min(1.0, gate_result.val_score))
        )
        proposal_row = await conn.fetchrow(
            """
            UPDATE proposals
            SET state             = $2::proposal_state,
                eval_score        = $3,
                eval_rationale    = $4,
                state_updated_at  = now()
            WHERE id = $1
            RETURNING *
            """,
            proposal_id,
            proposal_state.value,
            clamped_eval_score,
            gate_result.rationale,
        )

        # 8. Audit gate-run-completed.
        completed_entry = await append_audit_entry(
            conn,
            kind=AuditKind.GATE_RUN_COMPLETED,
            payload={
                "iteration_id": str(iteration_id),
                "proposal_id": str(proposal_id),
                "decision": gate_result.decision.value,
                "rationale": gate_result.rationale,
                "val_score": gate_result.val_score,
                "best_ever_score_before": gate_result.best_ever_score_before,
                "best_ever_score_after": gate_result.best_ever_score_after,
                "failed_prior_task_ids": list(gate_result.failed_prior_task_ids),
                "promotable_task_ids": list(gate_result.promotable_task_ids),
            },
            actor=actor,
            related_id=iteration_id,
        )

    return PersistedGateRun(
        gate_result=gate_result,
        iteration=_iteration_from_row(iteration_row),
        proposal=_proposal_from_row(proposal_row),
        audit_started_id=started_entry.id,
        audit_completed_id=completed_entry.id,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _infer_sandbox_error_class(result: GateResult) -> SandboxErrorClass | None:
    """The gate doesn't carry per-task error_class through; infer from
    the rationale string. The schema column is nullable, so an
    inability-to-infer falls back to None — the iteration_state
    `sandbox-error` is sufficient on its own.

    Future shape: thread `error_class` through `BenchmarkResult` so
    this string-sniffing goes away (W4 follow-up; not load-bearing for
    today's gate trust contract since the gate already short-circuits
    correctly on any None reward)."""
    rationale = (result.rationale or "").lower()
    if SandboxErrorClass.TIMEOUT.value.lower() in rationale:
        return SandboxErrorClass.TIMEOUT
    if SandboxErrorClass.OOM.value.lower() in rationale:
        return SandboxErrorClass.OOM
    if SandboxErrorClass.CRASH.value.lower() in rationale:
        return SandboxErrorClass.CRASH
    return None


def _iteration_from_row(row: asyncpg.Record) -> Iteration:
    """Hydrate the Pydantic model from a `RETURNING *` row.

    Postgres returns the iteration_state as a string under
    text-representation; Pydantic coerces it via the StrEnum.
    `started_at` and `ended_at` come back as `datetime`."""
    return Iteration(
        id=row["id"],
        workflow_id=row["workflow_id"],
        iteration_index=row["iteration_index"],
        proposed_skill_version_id=row["proposed_skill_version_id"],
        parent_skill_version_id=row["parent_skill_version_id"],
        state=IterationState(row["state"]),
        sandbox_error_class=(
            SandboxErrorClass(row["sandbox_error_class"])
            if row["sandbox_error_class"] is not None
            else None
        ),
        val_score=_to_float(row["val_score"]),
        best_ever_score_before=_to_float(row["best_ever_score_before"]),
        best_ever_score_after=_to_float(row["best_ever_score_after"]),
        cluster_id=row["cluster_id"],
        deployment_id=row["deployment_id"],
        token_budget_used=row["token_budget_used"],
        token_budget_total=row["token_budget_total"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )


def _proposal_from_row(row: asyncpg.Record) -> Proposal:
    expected_impact_raw = row["expected_impact"]
    if isinstance(expected_impact_raw, str):
        # asyncpg returns jsonb as str unless a codec is set — be
        # defensive across both possibilities so the test suite + W4
        # callers don't have to think about it.
        expected_impact_raw = json.loads(expected_impact_raw)
    return Proposal(
        id=row["id"],
        iteration_id=row["iteration_id"],
        skill_id=row["skill_id"],
        parent_version_id=row["parent_version_id"],
        proposed_content=row["proposed_content"],
        plain_language_summary=row["plain_language_summary"],
        expected_impact=expected_impact_raw,
        state=ProposalState(row["state"]),
        eval_score=_to_float(row["eval_score"]),
        eval_rationale=row["eval_rationale"],
        created_at=row["created_at"],
        state_updated_at=row["state_updated_at"],
    )


def _to_float(value: Any) -> float | None:
    """Coerce a numeric-typed asyncpg value (Decimal) to float | None."""
    if value is None:
        return None
    return float(value)


__all__ = [
    "PersistedGateRun",
    "persist_gate_run",
]
