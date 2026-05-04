"""DB-backed integration test for `scripts/m5_baseline.py` (W2.6).

Skipped when `OWNEVO_DATABASE_URL` is not set, matching the convention
used by `test_audit_log.py` etc. CI / dev compose runs exercise the
actual workflow + skill_versions + iterations writes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR

# Make `scripts/m5_baseline.py` importable. The script lives outside both
# `src/` and `tests/`; same trick `tests/test_baselines_m5_lightgbm.py` uses
# for `baselines/`.
_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from scripts.m5_baseline import record_baseline  # noqa: E402

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Workflow + skills + iterations land
# ---------------------------------------------------------------------------


async def test_record_baseline_writes_workflow_skills_and_iteration(
    db: asyncpg.Connection,
):
    """Single call → workflow row + 6 skill versions + one iteration row."""
    workflow_id = "m5-test-workflow-w26"

    await record_baseline(db, workflow_id=workflow_id, val_score=0.42)

    wf = await db.fetchrow(
        "SELECT id, description FROM workflows WHERE id = $1",
        workflow_id,
    )
    assert wf is not None
    assert "Day-1" in wf["description"]

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

    iterations = await db.fetch(
        """
        SELECT iteration_index, state::text AS state, val_score, best_ever_score_after
        FROM iterations
        WHERE workflow_id = $1
        ORDER BY iteration_index
        """,
        workflow_id,
    )
    assert len(iterations) == 1
    row = iterations[0]
    assert row["iteration_index"] == 0
    assert row["state"] == "gate-pass"
    assert float(row["val_score"]) == pytest.approx(0.42)
    assert float(row["best_ever_score_after"]) == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# Re-run idempotence — skills don't bump, iteration_index advances
# ---------------------------------------------------------------------------


async def test_rerun_skips_skill_re_register_and_appends_iteration(
    db: asyncpg.Connection,
):
    """Re-running the bootstrap on unchanged skill bodies must not bump
    `version_seq` (keeps the registry tidy across CI runs), but it MUST
    append a new iterations row so the lift chart picks it up."""
    workflow_id = "m5-rerun-workflow"

    await record_baseline(db, workflow_id=workflow_id, val_score=0.5)
    await record_baseline(db, workflow_id=workflow_id, val_score=0.55)

    versions = await db.fetch(
        """
        SELECT skill_id, MAX(version_seq) AS max_seq
        FROM skill_versions
        WHERE skill_id LIKE 'm5.baseline.v1.%'
        GROUP BY skill_id
        """,
    )
    assert len(versions) == 6
    assert all(v["max_seq"] == 1 for v in versions), (
        "Re-run should not bump skill versions when the body is unchanged."
    )

    iterations = await db.fetch(
        """
        SELECT iteration_index, val_score
        FROM iterations
        WHERE workflow_id = $1
        ORDER BY iteration_index
        """,
        workflow_id,
    )
    assert [r["iteration_index"] for r in iterations] == [0, 1]
    assert float(iterations[0]["val_score"]) == pytest.approx(0.5)
    assert float(iterations[1]["val_score"]) == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# Workflow upsert is idempotent
# ---------------------------------------------------------------------------


async def test_workflow_upsert_does_not_duplicate(db: asyncpg.Connection):
    workflow_id = "m5-upsert-workflow"
    await record_baseline(db, workflow_id=workflow_id, val_score=0.1)
    await record_baseline(db, workflow_id=workflow_id, val_score=0.2)

    count = await db.fetchval(
        "SELECT COUNT(*) FROM workflows WHERE id = $1",
        workflow_id,
    )
    assert count == 1
