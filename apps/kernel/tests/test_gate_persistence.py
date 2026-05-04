"""DB-backed integration tests for `persist_gate_run` (W2.2 follow-up).

Pins the substrate write contract: each `GateDecision` must produce
the right rows in `iterations` + `proposals` + `audit_entries`, and a
crash anywhere in the call must roll back cleanly (no orphan rows).

Uses `SyntheticBenchmarkRunner` so the gate's decision is dictated by
the test's chosen task scores — no Docker, no LLM. The whole call is
in-process except for the DB.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import asyncpg
import pytest
from ownevo_kernel.benchmark import SyntheticBenchmarkRunner, SyntheticTask
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.gate import GateDecision, persist_gate_run
from ownevo_kernel.types import (
    AuditKind,
    IterationState,
    ProposalState,
    SandboxErrorClass,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures — workflows + runners
# ---------------------------------------------------------------------------


async def _seed_workflow(conn: asyncpg.Connection, workflow_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, 'test workflow', '{}'::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        workflow_id,
    )


async def _seed_skill(conn: asyncpg.Connection, skill_id: str) -> None:
    """Seed a `skills` row so `proposals.skill_id` FK resolves.

    Tests want the wrapper exercised in isolation; we don't bother
    walking through `register_skill` for every case. `kind=python` is
    arbitrary — only the FK is being validated here."""
    await conn.execute(
        """
        INSERT INTO skills (id, kind)
        VALUES ($1, 'python'::skill_kind)
        ON CONFLICT (id) DO NOTHING
        """,
        skill_id,
    )


async def _seed(conn: asyncpg.Connection, *, workflow_id: str, skill_id: str) -> None:
    await _seed_workflow(conn, workflow_id)
    await _seed_skill(conn, skill_id)


def _doubler_runner(skill: Callable[[Any], Any]) -> SyntheticBenchmarkRunner:
    """Three tasks asking for `x * 2`. Skill maps input → output; the
    runner gives 1.0 if equal to expected, 0.0 otherwise."""
    return SyntheticBenchmarkRunner(
        tasks=(
            SyntheticTask(id="t1", input=1, expected=2),
            SyntheticTask(id="t2", input=2, expected=4),
            SyntheticTask(id="t3", input=3, expected=6),
        ),
        skill=skill,
    )


# ---------------------------------------------------------------------------
# Decision-path coverage
# ---------------------------------------------------------------------------


async def test_pass_writes_iteration_proposal_and_two_audits(db: asyncpg.Connection):
    """PASS: iterations.state='gate-pass'; proposals.state='gate-passed'
    + eval_score = val_score; two audit_entries (started + completed)."""
    workflow_id = "wf-pass"
    skill_id = "m5.baseline.v1.feature_engineer"
    await _seed(db, workflow_id=workflow_id, skill_id=skill_id)

    runner = _doubler_runner(lambda x: x * 2)
    persisted = await persist_gate_run(
        db,
        runner,
        workflow_id=workflow_id,
        skill_id=skill_id,
        proposed_content="def engineer(x): return x * 2\n",
        plain_language_summary="Day-1 baseline",
        actor="test:pass-path",
    )

    assert persisted.gate_result.passed
    assert persisted.gate_result.decision == GateDecision.PASS
    assert persisted.gate_result.val_score == pytest.approx(1.0)

    # Iteration row.
    it = persisted.iteration
    assert it.workflow_id == workflow_id
    assert it.iteration_index == 0
    assert it.state == IterationState.GATE_PASS
    assert it.val_score == pytest.approx(1.0)
    assert it.best_ever_score_after == pytest.approx(1.0)
    assert it.best_ever_score_before is None
    assert it.sandbox_error_class is None
    assert it.ended_at is not None

    # Proposal row.
    p = persisted.proposal
    assert p.iteration_id == it.id
    assert p.skill_id == skill_id
    assert p.state == ProposalState.GATE_PASSED
    assert p.eval_score == pytest.approx(1.0)
    assert "Gate passed" in (p.eval_rationale or "")

    # Two audit entries written, both linked via related_id and ordered
    # by seq. seq is bigserial — strictly increasing.
    audit_rows = await db.fetch(
        "SELECT seq, kind::text AS kind, payload, related_id FROM audit_entries "
        "ORDER BY seq",
    )
    started_kinds = [r["kind"] for r in audit_rows]
    assert started_kinds == [
        AuditKind.GATE_RUN_STARTED.value,
        AuditKind.GATE_RUN_COMPLETED.value,
    ]
    assert all(r["related_id"] == it.id for r in audit_rows)
    # Completed payload carries the gate evidence the UI / replay needs
    # to render the rationale without re-running the gate.
    completed_payload = audit_rows[1]["payload"]
    if isinstance(completed_payload, str):
        import json
        completed_payload = json.loads(completed_payload)
    assert completed_payload["decision"] == GateDecision.PASS.value
    assert completed_payload["val_score"] == pytest.approx(1.0)


async def test_fail_regression_blocks_advance_and_records_failed_priors(
    db: asyncpg.Connection,
):
    """FAIL_REGRESSION: prior task fails → state=gate-blocked-regression;
    best_ever_score_after preserved (does NOT advance); proposal state
    = gate-failed; payload lists failed_prior_task_ids."""
    workflow_id = "wf-regression"
    await _seed(db, workflow_id=workflow_id, skill_id="m5.skill")

    # Skill regresses on t1 (returns wrong value), passes t2/t3.
    def regressing(x):
        return -1 if x == 1 else x * 2

    runner = _doubler_runner(regressing)
    persisted = await persist_gate_run(
        db,
        runner,
        workflow_id=workflow_id,
        skill_id="m5.skill",
        proposed_content="...",
        plain_language_summary="Regresses on t1",
        actor="test:regression",
        prior_eval_task_ids=["t1", "t2"],
        best_ever_score=0.6,
    )

    assert persisted.gate_result.decision == GateDecision.FAIL_REGRESSION
    assert "t1" in persisted.gate_result.failed_prior_task_ids

    it = persisted.iteration
    assert it.state == IterationState.GATE_BLOCKED_REGRESSION
    # Best-ever does NOT advance.
    assert it.best_ever_score_before == pytest.approx(0.6)
    assert it.best_ever_score_after == pytest.approx(0.6)

    p = persisted.proposal
    assert p.state == ProposalState.GATE_FAILED
    assert "regressed" in (p.eval_rationale or "").lower()


async def test_fail_no_improvement_keeps_best_ever(db: asyncpg.Connection):
    """FAIL_NO_IMPROVEMENT: val_score doesn't beat best_ever →
    state=gate-blocked-no-improvement; best_ever preserved."""
    workflow_id = "wf-no-improvement"
    await _seed(db, workflow_id=workflow_id, skill_id="m5.skill")

    # Perfect skill but best_ever already at 1.0 — no headroom.
    runner = _doubler_runner(lambda x: x * 2)
    persisted = await persist_gate_run(
        db,
        runner,
        workflow_id=workflow_id,
        skill_id="m5.skill",
        proposed_content="...",
        plain_language_summary="Same as before",
        actor="test:no-improvement",
        best_ever_score=1.0,
    )

    assert persisted.gate_result.decision == GateDecision.FAIL_NO_IMPROVEMENT
    assert persisted.iteration.state == IterationState.GATE_BLOCKED_NO_IMPROVEMENT
    assert persisted.iteration.best_ever_score_after == pytest.approx(1.0)
    assert persisted.proposal.state == ProposalState.GATE_FAILED


async def test_sandbox_error_marks_iteration_and_keeps_proposal_in_gate(
    db: asyncpg.Connection,
):
    """SANDBOX_ERROR: any None reward → state=sandbox-error; proposal
    stays in `in-gate` (the agent didn't cause this; retry path is
    correct). best_ever_score_after preserved."""
    workflow_id = "wf-sandbox-error"
    await _seed(db, workflow_id=workflow_id, skill_id="m5.skill")

    # SyntheticBenchmarkRunner doesn't return None on its own — we feed
    # it a runner that drops a None reward via task list manipulation.
    # Easiest path: build a runner whose internal scoring returns None
    # for one task by overriding `_score_one`.
    base = _doubler_runner(lambda x: x * 2)

    class _NoneRunner(SyntheticBenchmarkRunner):
        async def run(self, task_ids=None):  # type: ignore[override]
            res = await super().run(task_ids)
            # Mutate the result rewards to drop in a None — same shape
            # the real M5 sandbox produces on Timeout/OOM/Crash.
            from ownevo_kernel.benchmark.types import BenchmarkResult
            new_rewards = dict(res.rewards)
            new_rewards["t2"] = None
            return BenchmarkResult(rewards=new_rewards)

    runner = _NoneRunner(tasks=base.tasks, skill=base.skill)
    persisted = await persist_gate_run(
        db,
        runner,
        workflow_id=workflow_id,
        skill_id="m5.skill",
        proposed_content="...",
        plain_language_summary="Skill that triggers a sandbox crash",
        actor="test:sandbox-error",
        best_ever_score=0.5,
    )

    assert persisted.gate_result.decision == GateDecision.SANDBOX_ERROR
    it = persisted.iteration
    assert it.state == IterationState.SANDBOX_ERROR
    # best_ever preserved on sandbox error per D3.
    assert it.best_ever_score_after == pytest.approx(0.5)
    assert it.val_score is None

    # Proposal stays in in-gate so a retry path is valid; it's
    # specifically NOT gate-failed (the agent didn't cause this).
    assert persisted.proposal.state == ProposalState.IN_GATE


async def test_sandbox_error_class_inferred_from_runner_exception(
    db: asyncpg.Connection,
):
    """If the runner raises a Timeout-shaped exception, the
    iteration's sandbox_error_class column is populated. The gate's
    SANDBOX_ERROR rationale carries the exception type, which the
    persistence wrapper substring-sniffs for the enum value."""
    workflow_id = "wf-sandbox-timeout"
    await _seed(db, workflow_id=workflow_id, skill_id="m5.skill")

    class _TimeoutRunner:
        async def run(self, task_ids=None):
            raise TimeoutError("synthetic timeout after 30s")

    persisted = await persist_gate_run(
        db,
        _TimeoutRunner(),  # type: ignore[arg-type]
        workflow_id=workflow_id,
        skill_id="m5.skill",
        proposed_content="...",
        plain_language_summary="Skill that times out",
        actor="test:timeout",
    )

    assert persisted.gate_result.decision == GateDecision.SANDBOX_ERROR
    assert persisted.iteration.state == IterationState.SANDBOX_ERROR
    assert persisted.iteration.sandbox_error_class == SandboxErrorClass.TIMEOUT


# ---------------------------------------------------------------------------
# Concurrency + bookkeeping
# ---------------------------------------------------------------------------


async def test_iteration_index_advances_per_workflow(db: asyncpg.Connection):
    """Three sequential persists → iteration_index 0, 1, 2 per
    workflow. Pin so the workflow-row FOR UPDATE doesn't drop a slot."""
    workflow_id = "wf-index"
    await _seed(db, workflow_id=workflow_id, skill_id="m5.skill")

    indices = []
    for _ in range(3):
        persisted = await persist_gate_run(
            db,
            _doubler_runner(lambda x: x * 2),
            workflow_id=workflow_id,
            skill_id="m5.skill",
            proposed_content="...",
            plain_language_summary="iter",
            actor="test:index",
        )
        indices.append(persisted.iteration.iteration_index)
    assert indices == [0, 1, 2]


async def test_unknown_workflow_raises_before_any_writes(db: asyncpg.Connection):
    """A missing workflow_id should fail loudly before any iteration /
    proposal / audit row is written. Rolls back cleanly."""
    with pytest.raises(ValueError, match="does not exist"):
        await persist_gate_run(
            db,
            _doubler_runner(lambda x: x * 2),
            workflow_id="wf-does-not-exist",
            skill_id="m5.skill",
            proposed_content="...",
            plain_language_summary="should fail",
            actor="test:missing-workflow",
        )

    counts = await db.fetchrow(
        "SELECT "
        "(SELECT COUNT(*) FROM iterations)::int AS i, "
        "(SELECT COUNT(*) FROM proposals)::int AS p, "
        "(SELECT COUNT(*) FROM audit_entries)::int AS a"
    )
    assert (counts["i"], counts["p"], counts["a"]) == (0, 0, 0)


async def test_runner_exception_rolls_back_iteration_and_proposal(
    db: asyncpg.Connection,
):
    """A non-Timeout/OOM/Crash exception from the runner shouldn't
    leak — the gate catches BaseException and emits SANDBOX_ERROR, so
    the call still completes. This pins that expected behavior."""
    workflow_id = "wf-runner-raises"
    await _seed(db, workflow_id=workflow_id, skill_id="m5.skill")

    class _BoomRunner:
        async def run(self, task_ids=None):
            raise RuntimeError("internal bug")

    persisted = await persist_gate_run(
        db,
        _BoomRunner(),  # type: ignore[arg-type]
        workflow_id=workflow_id,
        skill_id="m5.skill",
        proposed_content="...",
        plain_language_summary="Skill with internal bug",
        actor="test:boom",
    )
    # The gate's catch surfaces SANDBOX_ERROR → iteration written, not
    # rolled back. Pinned so future "tighten the catch" changes are
    # observed.
    assert persisted.gate_result.decision == GateDecision.SANDBOX_ERROR
    assert persisted.iteration.state == IterationState.SANDBOX_ERROR


# ---------------------------------------------------------------------------
# Audit chain → export round-trip
# ---------------------------------------------------------------------------


async def test_promotable_task_ids_surface_for_caller(db: asyncpg.Connection):
    """On PASS, tasks not in `prior_eval_task_ids` that scored at
    threshold are surfaced as `promotable_task_ids`. The wrapper does
    NOT auto-promote — the gate has no opinion on what input /
    expected_behavior to seed for a new case."""
    workflow_id = "wf-promotable"
    await _seed(db, workflow_id=workflow_id, skill_id="m5.skill")

    runner = _doubler_runner(lambda x: x * 2)
    persisted = await persist_gate_run(
        db,
        runner,
        workflow_id=workflow_id,
        skill_id="m5.skill",
        proposed_content="...",
        plain_language_summary="passes all",
        actor="test:promotable",
        prior_eval_task_ids=["t1"],
    )
    # All three pass; t2 + t3 are new admits.
    assert set(persisted.gate_result.promotable_task_ids) == {"t2", "t3"}

    # No new eval_cases rows — promotion is caller's responsibility.
    n_eval_cases = await db.fetchval("SELECT COUNT(*)::int FROM eval_cases")
    assert n_eval_cases == 0
