"""Integration tests for `scripts/seed_m5_baseline.py` (BL.1).

The contract this test pins:
  * Seed creates the workflow row + registers 6 skill versions at seq=1.
  * Re-running on unchanged bodies skips re-registration (`version_seq`
    does not bump) and does NOT append an iterations row — the bootstrap
    seed deliberately leaves `iterations` empty so the first BL.3 gate
    run gets `best_ever_score=None`.
  * Workflow upsert is idempotent (single workflow row even after N seeds).

Skipped when `OWNEVO_DATABASE_URL` is unset (matches conftest.py + the
sibling `test_scripts_m5_baseline.py`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from scripts.seed_m5_baseline import seed_baseline  # noqa: E402

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# First seed — workflow + 6 skills, no iterations
# ---------------------------------------------------------------------------


async def test_first_seed_writes_workflow_and_six_skills(db: asyncpg.Connection):
    """Single seed call → 1 workflow row + 6 skill_versions, no iterations."""
    workflow_id = "m5-bootstrap-w3-first"

    result = await seed_baseline(db, workflow_id=workflow_id)

    assert result.workflow_id == workflow_id
    assert len(result.registered) == 6
    assert result.skipped == ()

    wf_count = await db.fetchval(
        "SELECT COUNT(*) FROM workflows WHERE id = $1",
        workflow_id,
    )
    assert wf_count == 1

    skill_ids = await db.fetch(
        "SELECT id FROM skills WHERE id LIKE 'm5.baseline.v1.%' ORDER BY id",
    )
    assert {r["id"] for r in skill_ids} == {
        "m5.baseline.v1.data_loader",
        "m5.baseline.v1.ensemble",
        "m5.baseline.v1.feature_engineer",
        "m5.baseline.v1.model_trainer",
        "m5.baseline.v1.outlier_handler",
        "m5.baseline.v1.predictor",
    }

    versions = await db.fetch(
        """
        SELECT skill_id, version_seq
        FROM skill_versions
        WHERE skill_id LIKE 'm5.baseline.v1.%'
        ORDER BY skill_id
        """,
    )
    assert len(versions) == 6
    assert all(v["version_seq"] == 1 for v in versions)

    # Bootstrap rule: NO iterations row. BL.3's first gate run sees
    # best_ever_score=None and the improvement check is skipped.
    iter_count = await db.fetchval(
        "SELECT COUNT(*) FROM iterations WHERE workflow_id = $1",
        workflow_id,
    )
    assert iter_count == 0


# ---------------------------------------------------------------------------
# Re-seed — idempotent skill registration, no iterations row appears
# ---------------------------------------------------------------------------


async def test_reseed_skips_unchanged_skills_and_writes_no_iteration(
    db: asyncpg.Connection,
):
    workflow_id = "m5-bootstrap-w3-reseed"

    first = await seed_baseline(db, workflow_id=workflow_id)
    assert len(first.registered) == 6

    second = await seed_baseline(db, workflow_id=workflow_id)
    assert second.registered == ()
    assert len(second.skipped) == 6

    versions = await db.fetch(
        """
        SELECT skill_id, MAX(version_seq) AS max_seq
        FROM skill_versions
        WHERE skill_id LIKE 'm5.baseline.v1.%'
        GROUP BY skill_id
        """,
    )
    assert all(v["max_seq"] == 1 for v in versions), (
        "Re-seed on unchanged bodies must not bump version_seq"
    )

    iter_count = await db.fetchval(
        "SELECT COUNT(*) FROM iterations WHERE workflow_id = $1",
        workflow_id,
    )
    assert iter_count == 0


# ---------------------------------------------------------------------------
# Workflow upsert idempotence
# ---------------------------------------------------------------------------


async def test_workflow_upsert_does_not_duplicate(db: asyncpg.Connection):
    workflow_id = "m5-bootstrap-w3-upsert"
    await seed_baseline(db, workflow_id=workflow_id)
    await seed_baseline(db, workflow_id=workflow_id)
    await seed_baseline(db, workflow_id=workflow_id)

    count = await db.fetchval(
        "SELECT COUNT(*) FROM workflows WHERE id = $1",
        workflow_id,
    )
    assert count == 1
