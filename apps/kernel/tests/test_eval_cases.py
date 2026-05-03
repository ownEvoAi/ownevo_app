"""Eval-case CRUD — DB-backed integration tests (W2.3).

Provenance taxonomy is the eng-review surface — every case carries the
audit trail of how it got into the suite. These tests pin the round-trip
through asyncpg + JSONB + the provenance enum, plus the filter shapes
the gate runner uses.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.eval_cases import add_eval_case, get_eval_case, list_eval_cases
from ownevo_kernel.types import EvalCase, ProvenanceKind

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Add + retrieve
# ---------------------------------------------------------------------------


async def test_add_returns_typed_eval_case(db: asyncpg.Connection):
    case = await add_eval_case(
        db,
        provenance=ProvenanceKind.HAND_AUTHORED,
        input={"sku": "FOODS_1_001", "store": "CA_1"},
        expected_behavior={"forecast_within": 0.05},
        regression_tolerance=0.05,
    )
    assert isinstance(case, EvalCase)
    assert case.provenance == ProvenanceKind.HAND_AUTHORED
    assert case.input["sku"] == "FOODS_1_001"
    assert case.expected_behavior["forecast_within"] == 0.05
    assert case.regression_tolerance == 0.05
    assert case.is_test_fold is False
    assert case.cluster_id is None


async def test_get_round_trip(db: asyncpg.Connection):
    added = await add_eval_case(
        db,
        provenance="retention-violation",
        input={"source_id": "supplier_doc:lead_time", "T_plus": "25h"},
        expected_behavior={"trace_must_contain": "re-fetch tool call"},
    )
    got = await get_eval_case(db, added.id)
    assert got is not None
    assert got.id == added.id
    assert got.provenance == ProvenanceKind.RETENTION_VIOLATION
    assert got.input == added.input


async def test_get_unknown_returns_none(db: asyncpg.Connection):
    assert await get_eval_case(db, uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# Provenance taxonomy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provenance",
    [
        ProvenanceKind.HAND_AUTHORED,
        ProvenanceKind.CLUSTER_DERIVED,
        ProvenanceKind.NL_GEN,
        ProvenanceKind.RETENTION_VIOLATION,
        ProvenanceKind.REJECTED_FEEDBACK,
    ],
)
async def test_all_provenance_values_round_trip(
    db: asyncpg.Connection,
    provenance: ProvenanceKind,
):
    """Every provenance the schema declares must round-trip cleanly —
    the eng-review taxonomy is part of the contract."""
    case = await add_eval_case(
        db, provenance=provenance,
        input={"x": 1}, expected_behavior={"y": 2},
    )
    fetched = await get_eval_case(db, case.id)
    assert fetched is not None
    assert fetched.provenance == provenance


async def test_unknown_provenance_rejected_by_db(db: asyncpg.Connection):
    """The provenance_kind enum is the schema's enforcement boundary."""
    with pytest.raises(asyncpg.PostgresError):
        await add_eval_case(
            db, provenance="not-a-real-provenance",
            input={}, expected_behavior={},
        )


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


async def test_list_filters_by_provenance(db: asyncpg.Connection):
    await add_eval_case(db, provenance="hand-authored", input={}, expected_behavior={})
    await add_eval_case(db, provenance="hand-authored", input={}, expected_behavior={})
    await add_eval_case(db, provenance="nl-gen", input={}, expected_behavior={})

    hand = await list_eval_cases(db, provenance=ProvenanceKind.HAND_AUTHORED)
    assert len(hand) == 2
    assert all(c.provenance == ProvenanceKind.HAND_AUTHORED for c in hand)

    nl = await list_eval_cases(db, provenance="nl-gen")
    assert len(nl) == 1
    assert nl[0].provenance == ProvenanceKind.NL_GEN


async def test_list_filters_by_test_fold(db: asyncpg.Connection):
    """Train/test discipline — the gate runner refuses to train on
    test-fold rows. The filter is what surfaces those for held-out eval."""
    await add_eval_case(
        db, provenance="hand-authored",
        input={}, expected_behavior={}, is_test_fold=False,
    )
    await add_eval_case(
        db, provenance="hand-authored",
        input={}, expected_behavior={}, is_test_fold=True,
    )

    train = await list_eval_cases(db, is_test_fold=False)
    test = await list_eval_cases(db, is_test_fold=True)
    assert len(train) == 1
    assert len(test) == 1
    assert train[0].is_test_fold is False
    assert test[0].is_test_fold is True


async def test_list_filters_by_workflow(db: asyncpg.Connection):
    """Workflow scoping — the gate runner pulls only its workflow's cases."""
    await db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, $2, '{}'::jsonb)",
        "wf-a", "workflow A",
    )
    await db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, $2, '{}'::jsonb)",
        "wf-b", "workflow B",
    )
    common = {"provenance": "hand-authored", "input": {}, "expected_behavior": {}}
    await add_eval_case(db, workflow_id="wf-a", **common)
    await add_eval_case(db, workflow_id="wf-a", **common)
    await add_eval_case(db, workflow_id="wf-b", **common)

    a = await list_eval_cases(db, workflow_id="wf-a")
    b = await list_eval_cases(db, workflow_id="wf-b")
    assert len(a) == 2
    assert len(b) == 1


async def test_list_combined_filters(db: asyncpg.Connection):
    """Filters AND together — the gate's typical query is workflow + non-test fold."""
    await db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, $2, '{}'::jsonb)",
        "wf-a", "A",
    )
    await add_eval_case(
        db, provenance="hand-authored", input={"i": 1}, expected_behavior={},
        workflow_id="wf-a", is_test_fold=False,
    )
    await add_eval_case(
        db, provenance="hand-authored", input={"i": 2}, expected_behavior={},
        workflow_id="wf-a", is_test_fold=True,
    )
    await add_eval_case(
        db, provenance="nl-gen", input={"i": 3}, expected_behavior={},
        workflow_id="wf-a", is_test_fold=False,
    )

    train_hand = await list_eval_cases(
        db,
        workflow_id="wf-a",
        provenance=ProvenanceKind.HAND_AUTHORED,
        is_test_fold=False,
    )
    assert len(train_hand) == 1
    assert train_hand[0].input["i"] == 1


async def test_list_orders_by_created_at(db: asyncpg.Connection):
    """The gate fail-fasts on older cases first — order is part of the contract."""
    a = await add_eval_case(db, provenance="hand-authored", input={"n": 1}, expected_behavior={})
    b = await add_eval_case(db, provenance="hand-authored", input={"n": 2}, expected_behavior={})
    c = await add_eval_case(db, provenance="hand-authored", input={"n": 3}, expected_behavior={})

    cases = await list_eval_cases(db)
    assert [x.id for x in cases] == [a.id, b.id, c.id]


# ---------------------------------------------------------------------------
# Cluster linkage
# ---------------------------------------------------------------------------


async def test_cluster_derived_case_links_to_cluster(db: asyncpg.Connection):
    """cluster-derived cases carry a back-pointer to their failure_clusters row."""
    cluster_id = await db.fetchval(
        """
        INSERT INTO failure_clusters (label, severity, cluster_size)
        VALUES ('winter footwear PNW Q4', 'medium', 12)
        RETURNING id
        """,
    )
    case = await add_eval_case(
        db,
        provenance=ProvenanceKind.CLUSTER_DERIVED,
        cluster_id=cluster_id,
        input={"sku": "BOOT_001"},
        expected_behavior={"forecast_within": 0.10},
    )
    assert case.cluster_id == cluster_id

    listed = await list_eval_cases(db, cluster_id=cluster_id)
    assert len(listed) == 1
    assert listed[0].id == case.id


async def test_list_combined_cluster_and_test_fold(db: asyncpg.Connection):
    """cluster_id + is_test_fold AND-chaining — exercises both clauses in one query."""
    cluster_id = await db.fetchval(
        "INSERT INTO failure_clusters (label, severity, cluster_size) "
        "VALUES ('test cluster', 'low', 3) RETURNING id",
    )
    train_case = await add_eval_case(
        db,
        provenance=ProvenanceKind.CLUSTER_DERIVED,
        cluster_id=cluster_id,
        input={"fold": "train"},
        expected_behavior={},
        is_test_fold=False,
    )
    await add_eval_case(
        db,
        provenance=ProvenanceKind.CLUSTER_DERIVED,
        cluster_id=cluster_id,
        input={"fold": "test"},
        expected_behavior={},
        is_test_fold=True,
    )

    train_only = await list_eval_cases(db, cluster_id=cluster_id, is_test_fold=False)
    assert len(train_only) == 1
    assert train_only[0].id == train_case.id
