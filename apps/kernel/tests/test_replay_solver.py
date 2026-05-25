"""Tests for `solve_with_replay_agent` — Track 9.0.3 Slice A.

What we pin:

  1. Cross-check parity with solve_with_agent — workflow_spec_id
     disagreement raises ValueError before any DB lookup.
  2. Empty source iteration returns empty results + every case as
     missing.
  3. DB-backed roundtrip (when OWNEVO_DATABASE_URL is set):
     - seed iteration_case_outputs with N captured rows
     - call solve_with_replay_agent
     - assert each ReplayResult equals the captured shape
     - cases not in the captured set come back in `missing`

The DB-gated test is the load-bearing one — the cross-check tests
exercise the early-return paths but don't probe the actual replay
behaviour.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.eval_runner.replay_solver import (
    ReplayCaseMissingError,
    solve_with_replay_agent,
)
from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet, GeneratedEvalCase
from ownevo_kernel.nl_gen.fixtures import (
    DEMAND_PREDICTION_METRIC,
    DEMAND_PREDICTION_SIM_PLAN,
    DEMAND_PREDICTION_SPEC,
)
from ownevo_kernel.nl_gen.fixtures.eval_case_sets import (
    DEMAND_PREDICTION_EVAL_CASE_SET,
)
from ownevo_kernel.nl_gen.spec import Provenance

# ---------------------------------------------------------------------------
# Cross-check parity (no DB needed)
# ---------------------------------------------------------------------------


async def test_mismatched_workflow_spec_id_raises_value_error() -> None:
    """Mirrors solve_with_agent's xref check — fails fast before the
    captured-set lookup."""
    mismatched = DEMAND_PREDICTION_EVAL_CASE_SET.model_copy(
        update={
            "workflow_spec_id": "not-the-real-spec-id",
            "simulation_plan_workflow_id": "not-the-real-spec-id",
        },
    )

    class _Stub:
        async def fetch(self, *args: Any, **kwargs: Any) -> list:
            raise AssertionError("should not reach DB on xref failure")

    with pytest.raises(ValueError, match="case_set.workflow_spec_id"):
        await solve_with_replay_agent(
            _Stub(),  # type: ignore[arg-type]
            mismatched,
            DEMAND_PREDICTION_SIM_PLAN,
            DEMAND_PREDICTION_SPEC,
            DEMAND_PREDICTION_METRIC,
            source_iteration_id=uuid4(),
        )


# ---------------------------------------------------------------------------
# ReplayCaseMissingError shape
# ---------------------------------------------------------------------------


def test_replay_case_missing_error_carries_actionable_context() -> None:
    """The error must name the source iteration AND the missing case
    ids so an operator can either pick a different source iteration
    or switch fallback mode without spelunking through logs."""
    iter_id = uuid4()
    err = ReplayCaseMissingError(iter_id, ["case-a", "case-b"])
    msg = str(err)
    assert str(iter_id) in msg
    assert "case-a" in msg and "case-b" in msg
    assert err.source_iteration_id == iter_id
    assert err.missing_case_ids == ["case-a", "case-b"]


# ---------------------------------------------------------------------------
# DB-backed roundtrip
# ---------------------------------------------------------------------------


pytestmark_db = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping db-backed replay roundtrip",
)


async def _seed_workflow_and_iteration(
    db: asyncpg.Connection,
    *,
    workflow_id: str = "test-replay-wf",
    case_ids: list[str],
) -> tuple[UUID, dict[str, UUID]]:
    """Seed a workflow + iteration + eval_cases + captured outputs.

    Returns (iteration_id, {case_id: eval_case_uuid}). The captured
    iteration_case_outputs rows are NOT written here — tests write
    their own to exercise specific shapes.
    """
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, 'replay test', '{}'::jsonb) ON CONFLICT DO NOTHING",
        workflow_id,
    )
    iteration_id: UUID = await db.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, started_at)
        VALUES ($1, 0, 'gate-pass'::iteration_state, now())
        RETURNING id
        """,
        workflow_id,
    )
    case_uuid_by_id: dict[str, UUID] = {}
    for case_id in case_ids:
        # case_id lives inside expected_behavior JSONB — eval_cases has no
        # dedicated case_id column (matches iteration_runner._persist_case_outputs
        # and the JSONB path used by solve_with_replay_agent).
        eval_case_uuid: UUID = await db.fetchval(
            """
            INSERT INTO eval_cases (
                workflow_id, input, expected_behavior, is_test_fold, provenance
            )
            VALUES (
                $1, '{}'::jsonb, jsonb_build_object('case_id', $2::text), false,
                'hand-authored'::provenance_kind
            )
            RETURNING id
            """,
            workflow_id,
            case_id,
        )
        case_uuid_by_id[case_id] = eval_case_uuid
    return iteration_id, case_uuid_by_id


async def _insert_captured(
    db: asyncpg.Connection,
    *,
    iteration_id: UUID,
    eval_case_uuid: UUID,
    predicted: bool,
    rationale: str,
    passed: bool,
    output_payload: dict | None = None,
) -> None:
    output_json = {
        "case_id": "ignored-here",
        "predicted": predicted,
        "expected": passed if predicted else not passed,
        "rationale": rationale,
        "is_test_fold": False,
    }
    payload_arg = json.dumps(output_payload) if output_payload else None
    await db.execute(
        """
        INSERT INTO iteration_case_outputs (
            iteration_id, eval_case_id, output_json, passed, output_payload
        )
        VALUES ($1, $2, $3::jsonb, $4, $5::jsonb)
        """,
        iteration_id,
        eval_case_uuid,
        json.dumps(output_json),
        passed,
        payload_arg,
    )


def _build_case_set_with(case_ids: list[str]) -> EvalCaseSet:
    """EvalCaseSet validator requires ≥10 cases + label balance. Build
    one with the requested case_ids by reusing the demand-prediction
    fixture's cases as the balance-providing tail; tests then look up
    by the requested case_ids only."""
    custom = [
        GeneratedEvalCase(
            case_id=cid,
            provenance=Provenance(kind="inferred", source="test"),
            sim_seed=1,
            n_steps=10,
            target_step_index=5,
            target_label_field="alert_correct_label",
            expected_value=True,
            rationale="test case",
        )
        for cid in case_ids
    ]
    # Add to the BACK so the fixture's label balance is preserved.
    fixture = DEMAND_PREDICTION_EVAL_CASE_SET.model_copy(deep=True)
    fixture.cases.extend(custom)
    return fixture


@pytestmark_db
async def test_replay_returns_captured_predictions(db: asyncpg.Connection) -> None:
    case_id = f"replay-case-{uuid.uuid4().hex[:8]}"
    iteration_id, case_uuid_by_id = await _seed_workflow_and_iteration(
        db, case_ids=[case_id],
    )
    await _insert_captured(
        db,
        iteration_id=iteration_id,
        eval_case_uuid=case_uuid_by_id[case_id],
        predicted=True,
        rationale="captured rationale text",
        passed=True,
    )

    case_set = _build_case_set_with([case_id])
    results, missing = await solve_with_replay_agent(
        db,
        case_set,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        source_iteration_id=iteration_id,
    )

    captured_results = [r for r in results if r.case_id == case_id]
    assert len(captured_results) == 1
    r = captured_results[0]
    assert r.actual_value is True
    assert r.passed is True
    assert r.rationale and "captured rationale text" in r.rationale
    assert r.rationale.startswith("[replay]")


@pytestmark_db
async def test_missing_case_returned_in_missing_list(db: asyncpg.Connection) -> None:
    """A case the fixture defines but the captured iteration didn't
    cover should show up in `missing`, not silently get a default."""
    iteration_id, _ = await _seed_workflow_and_iteration(
        db, case_ids=[],  # no eval_cases for the iteration
    )
    case_set = _build_case_set_with(["uncovered-case-1", "uncovered-case-2"])
    results, missing = await solve_with_replay_agent(
        db,
        case_set,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        DEMAND_PREDICTION_METRIC,
        source_iteration_id=iteration_id,
    )
    # Every case is missing (captured set is empty).
    assert "uncovered-case-1" in missing
    assert "uncovered-case-2" in missing
    # Results list is also empty since no case was covered.
    assert results == []
