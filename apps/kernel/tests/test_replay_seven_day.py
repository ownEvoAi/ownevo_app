"""DB-backed integration tests for the W5.4 replay orchestrator.

Pins the spec gates from PLAN.md § W5 § 5.4:

  * Lift curve climbs over 7 cycles.
  * Audit log gains ≥ 14 entries (2 per gate run × 7 cycles, plus
    judge-admit entries when gates pass).
  * Eval set grows from cluster-derived cases (one per cycle on the
    default config).

Pure-Python tests (config validation, lift-curve math, sort key) live
in `test_replay_seven_day_helpers.py` so they aren't suppressed by
the module-level DB-skip marker here.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.replay import (
    ReplayConfig,
    run_seven_day_replay,
)
from ownevo_kernel.replay.seven_day import (
    DEFAULT_JUDGE_ACTOR,
    DEFAULT_WORKFLOW_ID,
)
from ownevo_kernel.types import AuditKind, ProvenanceKind

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping DB-backed integration tests",
)


async def test_seven_cycle_default_lift_curve_climbs(db: asyncpg.Connection):
    """W5.4 spec gate: 7 cycles, lift curve climbs."""
    report = await run_seven_day_replay(db)
    assert report.n_cycles == 7
    assert len(report.lift_curve) == 7
    # Default config: 10 priors, 20 tasks, lift +1/cycle → curve goes
    # 10/20, 11/20, 12/20, 13/20, 14/20, 15/20, 16/20.
    assert report.lift_curve == pytest.approx(
        (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
    )
    assert report.is_climbing()


async def test_seven_cycle_audit_log_grows_at_least_fourteen(
    db: asyncpg.Connection,
):
    """W5.4 spec gate: audit log gains ≥ 7 entries.

    Each cycle writes 2 entries from `persist_gate_run` (started +
    completed); gate-pass cycles add a judge-admit. The default config
    has every cycle pass, so 7 × 3 = 21 entries — well above the spec's
    floor of 7+.
    """
    report = await run_seven_day_replay(db)
    assert report.audit_entry_count_after >= 14
    assert report.judge_admit_count == 7


async def test_seven_cycle_eval_set_grew_from_clusters(db: asyncpg.Connection):
    """W5.4 spec gate: eval set grew from clusters.

    Default `cluster_cases_per_cycle=1`. Every gate-pass adds one
    cluster-derived case. With 7 passing cycles, 7 cluster-derived
    cases land — eval set grows from 10 → 17.
    """
    report = await run_seven_day_replay(db)
    assert report.cluster_derived_count == 7
    assert report.eval_set_size_initial == 10
    assert report.eval_set_size_final == 17

    # And the 7 cluster-derived cases are visibly tagged in the DB so a
    # reviewer can audit "which cases came from clustering".
    n_cluster_cases = await db.fetchval(
        "SELECT COUNT(*) FROM eval_cases "
        "WHERE workflow_id = $1 AND provenance = $2::provenance_kind",
        DEFAULT_WORKFLOW_ID,
        ProvenanceKind.CLUSTER_DERIVED.value,
    )
    assert n_cluster_cases == 7


async def test_judge_admit_audit_entries_are_attributed_to_judge_actor(
    db: asyncpg.Connection,
):
    """Audit-trail seam for the W5.2 LLM-judge wire-up: every admit
    must be stamped `actor=llm-judge:stub` so the report can slice by
    approver_type later."""
    await run_seven_day_replay(db)
    rows = await db.fetch(
        "SELECT actor, payload FROM audit_entries "
        "WHERE kind = $1::audit_kind",
        AuditKind.PROPOSAL_APPROVED.value,
    )
    assert len(rows) == 7
    for row in rows:
        assert row["actor"] == DEFAULT_JUDGE_ACTOR


async def test_orchestrator_is_idempotent_on_seed_when_priors_already_exist(
    db: asyncpg.Connection,
):
    """Running the replay twice in a row with the same workflow_id
    must not double-seed the prior eval cases or fail with a unique-
    key violation. Subsequent runs simply add more iterations and
    cluster-derived cases on top of the prior state.
    """
    report1 = await run_seven_day_replay(db)
    report2 = await run_seven_day_replay(db)
    assert report1.eval_set_size_initial == 10
    assert report2.eval_set_size_initial == 17  # priors + cluster-derived from run 1
    n_iterations = await db.fetchval(
        "SELECT COUNT(*) FROM iterations WHERE workflow_id = $1",
        DEFAULT_WORKFLOW_ID,
    )
    assert n_iterations == 14  # 7 from each run


async def test_one_cycle_run_writes_one_iteration_two_audit_entries(
    db: asyncpg.Connection,
):
    """Smallest-possible run: 1 cycle. Pins the per-cycle write count
    so a regression in `persist_gate_run` (e.g., dropping the
    completed audit entry) is caught here."""
    cfg = ReplayConfig(n_cycles=1)
    report = await run_seven_day_replay(db, config=cfg)

    n_iters = await db.fetchval(
        "SELECT COUNT(*) FROM iterations WHERE workflow_id = $1", cfg.workflow_id
    )
    assert n_iters == 1

    # 2 from gate (started + completed) + 1 judge-admit = 3
    assert report.audit_entry_count_after == 3
    assert report.judge_admit_count == 1
    assert report.cluster_derived_count == 1


async def test_cluster_cases_per_cycle_zero_disables_growth(
    db: asyncpg.Connection,
):
    cfg = ReplayConfig(n_cycles=3, cluster_cases_per_cycle=0)
    report = await run_seven_day_replay(db, config=cfg)
    assert report.cluster_derived_count == 0
    assert report.eval_set_size_initial == report.eval_set_size_final


async def test_no_cluster_growth_when_all_tasks_pass_from_cycle_0(
    db: asyncpg.Connection,
):
    """When n_initial_priors == n_total_tasks the skill passes every task
    on cycle 0. Failure list is empty → _grow_eval_set_from_failures
    early-returns → eval set never grows despite cluster_cases_per_cycle=1."""
    cfg = ReplayConfig(
        n_cycles=3,
        n_initial_priors=5,
        n_total_tasks=5,
        cluster_cases_per_cycle=1,
    )
    report = await run_seven_day_replay(db, config=cfg)
    assert report.cluster_derived_count == 0
    assert report.eval_set_size_initial == report.eval_set_size_final
