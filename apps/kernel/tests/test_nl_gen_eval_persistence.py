"""Persistence tests for `nl_gen.eval_persistence` (A4.1).

End-to-end DB-backed tests pinning that a generated EvalCaseSet round-trips
through the W2.3 eval_cases CRUD seam — every case becomes one row with
provenance=NL_GEN, the input/expected_behavior split is preserved, and
list_eval_cases surfaces them with the right filters.

Skipped unless OWNEVO_DATABASE_URL is set (same gate as test_eval_cases.py).
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.eval_cases import list_eval_cases
from ownevo_kernel.nl_gen import persist_eval_case_set
from ownevo_kernel.nl_gen.fixtures import EVAL_CASE_SET_FIXTURES
from ownevo_kernel.types import ProvenanceKind

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


async def _seed_workflow(conn: asyncpg.Connection, workflow_id: str) -> None:
    """Insert a stub workflow row so eval_cases can FK-reference it."""
    await conn.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, $2, '{}'::jsonb)",
        workflow_id,
        f"{workflow_id} (test stub)",
    )


# ---------------------------------------------------------------------------
# Round-trip: persist → list_eval_cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id", list(EVAL_CASE_SET_FIXTURES.keys()))
async def test_persist_inserts_one_row_per_case(
    db: asyncpg.Connection, fixture_id: str
):
    case_set = EVAL_CASE_SET_FIXTURES[fixture_id]
    await _seed_workflow(db, case_set.workflow_spec_id)

    inserted = await persist_eval_case_set(db, case_set)

    assert len(inserted) == len(case_set.cases)
    fetched = await list_eval_cases(db, workflow_id=case_set.workflow_spec_id)
    assert len(fetched) == len(case_set.cases)


async def test_every_inserted_row_has_nl_gen_provenance(db: asyncpg.Connection):
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    await _seed_workflow(db, case_set.workflow_spec_id)
    inserted = await persist_eval_case_set(db, case_set)
    assert all(c.provenance == ProvenanceKind.NL_GEN for c in inserted)
    listed = await list_eval_cases(
        db, provenance=ProvenanceKind.NL_GEN
    )
    assert len(listed) == len(case_set.cases)


async def test_input_payload_carries_replay_parameters(db: asyncpg.Connection):
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    await _seed_workflow(db, case_set.workflow_spec_id)
    inserted = await persist_eval_case_set(db, case_set)
    # Match by case_id encoded into expected_behavior so we can pair source
    # and persisted case without relying on insertion order.
    persisted_by_case_id = {
        row.expected_behavior["case_id"]: row for row in inserted
    }
    for source_case in case_set.cases:
        persisted = persisted_by_case_id[source_case.case_id]
        assert persisted.input == {
            "sim_seed": source_case.sim_seed,
            "n_steps": source_case.n_steps,
            "target_step_index": source_case.target_step_index,
        }


async def test_expected_behavior_payload_carries_assertion_and_audit_fields(
    db: asyncpg.Connection,
):
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    await _seed_workflow(db, case_set.workflow_spec_id)
    inserted = await persist_eval_case_set(db, case_set)
    by_case_id = {row.expected_behavior["case_id"]: row for row in inserted}
    for source_case in case_set.cases:
        eb = by_case_id[source_case.case_id].expected_behavior
        assert eb["target_label_field"] == source_case.target_label_field
        assert eb["expected_value"] == source_case.expected_value
        assert eb["rationale"] == source_case.rationale
        assert eb["provenance"] == {
            "kind": source_case.provenance.kind,
            "source": source_case.provenance.source,
        }


async def test_test_fold_flag_propagates(db: asyncpg.Connection):
    """Held-out cases must surface as is_test_fold=True so the gate runner
    excludes them from training reads."""
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    await _seed_workflow(db, case_set.workflow_spec_id)
    inserted = await persist_eval_case_set(db, case_set)
    by_case_id = {row.expected_behavior["case_id"]: row for row in inserted}

    expected_test_fold = {
        c.case_id: c.is_test_fold for c in case_set.cases
    }
    for case_id, expect_held in expected_test_fold.items():
        assert by_case_id[case_id].is_test_fold == expect_held

    # Filter sanity: list_eval_cases(is_test_fold=True) returns the right slice.
    held_out = await list_eval_cases(
        db, workflow_id=case_set.workflow_spec_id, is_test_fold=True
    )
    assert {row.expected_behavior["case_id"] for row in held_out} == {
        cid for cid, held in expected_test_fold.items() if held
    }


async def test_workflow_id_override_takes_precedence(db: asyncpg.Connection):
    """An explicit workflow_id should win over the case_set's workflow_spec_id."""
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    override = "wf-override"
    await _seed_workflow(db, override)

    inserted = await persist_eval_case_set(db, case_set, workflow_id=override)
    assert all(row.workflow_id == override for row in inserted)


async def test_transaction_rolls_back_on_failure(db: asyncpg.Connection):
    """If any insert fails, the whole set is rolled back — no partial suite."""
    case_set = EVAL_CASE_SET_FIXTURES["demand-prediction"]
    # Don't seed the workflow row — FK violation on first insert.
    with pytest.raises(asyncpg.PostgresError):
        await persist_eval_case_set(db, case_set)
    # Partial state would show as orphan eval_cases; verify nothing landed.
    all_rows = await list_eval_cases(
        db, workflow_id=case_set.workflow_spec_id
    )
    assert all_rows == []
