"""Integration tests for `scripts/tau3_register.py` (P1.5 / M5).

Mirrors `test_scripts_seed_m5_baseline.py`. Pins:
  * First seed → 1 workflow row + 1 skill_versions(seq=1) + 40 eval_cases.
  * Re-seed → skill skipped (version_seq stays at 1), no new eval cases.
  * Workflow upsert is idempotent.

Skipped when `OWNEVO_DATABASE_URL` is unset.
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

from scripts.tau3_register import (  # noqa: E402
    RETAIL_TEST_TASK_IDS,
    seed_tau3_retail,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# First seed — workflow + skill + 40 eval cases
# ---------------------------------------------------------------------------


async def test_first_seed_writes_workflow_skill_and_eval_cases(
    db: asyncpg.Connection,
):
    workflow_id = "tau3-retail-test-first"

    result = await seed_tau3_retail(db, workflow_id=workflow_id)

    assert result.workflow_id == workflow_id
    assert result.skill_registered is True
    assert result.skill_skipped is False
    assert len(result.eval_cases_added) == 40
    assert result.eval_cases_skipped == ()

    wf_count = await db.fetchval(
        "SELECT COUNT(*) FROM workflows WHERE id = $1",
        workflow_id,
    )
    assert wf_count == 1

    versions = await db.fetch(
        """
        SELECT skill_id, version_seq
        FROM skill_versions
        WHERE skill_id = 'tau3.retail.baseline.v1.agent'
        ORDER BY version_seq
        """,
    )
    assert len(versions) == 1
    assert versions[0]["version_seq"] == 1

    eval_rows = await db.fetch(
        """
        SELECT input->>'task_id' AS task_id,
               input->>'split' AS split,
               is_test_fold
        FROM eval_cases
        WHERE workflow_id = $1
        ORDER BY (input->>'task_id')::int
        """,
        workflow_id,
    )
    assert len(eval_rows) == 40
    assert {r["task_id"] for r in eval_rows} == set(RETAIL_TEST_TASK_IDS)
    assert all(r["split"] == "test" for r in eval_rows)
    assert all(r["is_test_fold"] for r in eval_rows)


# ---------------------------------------------------------------------------
# Re-seed — idempotent
# ---------------------------------------------------------------------------


async def test_reseed_skips_skill_and_eval_cases(db: asyncpg.Connection):
    workflow_id = "tau3-retail-test-reseed"

    first = await seed_tau3_retail(db, workflow_id=workflow_id)
    assert first.skill_registered is True
    assert len(first.eval_cases_added) == 40

    second = await seed_tau3_retail(db, workflow_id=workflow_id)
    assert second.skill_registered is False
    assert second.skill_skipped is True
    assert second.eval_cases_added == ()
    assert len(second.eval_cases_skipped) == 40

    versions = await db.fetch(
        """
        SELECT MAX(version_seq) AS max_seq
        FROM skill_versions
        WHERE skill_id = 'tau3.retail.baseline.v1.agent'
        """,
    )
    assert versions[0]["max_seq"] == 1, (
        "Re-seed on unchanged body must not bump version_seq"
    )

    eval_count = await db.fetchval(
        "SELECT COUNT(*) FROM eval_cases WHERE workflow_id = $1",
        workflow_id,
    )
    assert eval_count == 40


# ---------------------------------------------------------------------------
# --no-eval-cases skips the eval-case seeding
# ---------------------------------------------------------------------------


async def test_seed_without_eval_cases(db: asyncpg.Connection):
    workflow_id = "tau3-retail-test-no-cases"

    result = await seed_tau3_retail(
        db, workflow_id=workflow_id, seed_eval_cases=False,
    )
    assert result.skill_registered is True
    assert result.eval_cases_added == ()
    assert result.eval_cases_skipped == ()

    eval_count = await db.fetchval(
        "SELECT COUNT(*) FROM eval_cases WHERE workflow_id = $1",
        workflow_id,
    )
    assert eval_count == 0


# ---------------------------------------------------------------------------
# Workflow upsert idempotence
# ---------------------------------------------------------------------------


async def test_workflow_upsert_does_not_duplicate(db: asyncpg.Connection):
    workflow_id = "tau3-retail-test-upsert"
    await seed_tau3_retail(db, workflow_id=workflow_id, seed_eval_cases=False)
    await seed_tau3_retail(db, workflow_id=workflow_id, seed_eval_cases=False)
    await seed_tau3_retail(db, workflow_id=workflow_id, seed_eval_cases=False)

    count = await db.fetchval(
        "SELECT COUNT(*) FROM workflows WHERE id = $1",
        workflow_id,
    )
    assert count == 1
