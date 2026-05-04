"""End-to-end substrate proof on a non-M5 workflow (W2.7).

PLAN.md § 2.7 — "Substrate proves itself on a non-M5 task. Hand-written
sim + 3 eval cases + a hand-written skill that solves them. Run through
the full pipeline (skill → sandbox → eval → gate → audit). Smoke test
passes end-to-end. Confirms substrate is domain-agnostic before Phase 2
starts."

What this test exercises
------------------------
A workflow distinct from M5 (`labour-shift-validation`) is driven through
every primitive the gate run touches:

    register_skill   →  skills + skill_versions rows written
    add_eval_case    →  eval_cases rows written (provenance=hand-authored)
    LabourBench →
      run_pipeline   →  LocalDockerSandbox executes the registered skill
      score          →  per-case 1.0/0.0
    persist_gate_run →  iterations + proposals + audit_entries (× 2)

A green test means the substrate is workflow-agnostic at the wiring
level — the same primitives that drive M5 also drive an unrelated
domain. This is the Phase 1 exit gate; Phase 2 cannot start without it.

Skipped when DB or Docker is unavailable. The skill is stdlib-only, so
the test uses the sandbox's default `python:3.11-slim` image — no
domain-specific Dockerfile needed, which is itself part of the proof:
a workflow whose skill has no third-party deps doesn't need a custom
image to drive the substrate.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

# `apps/kernel/baselines/` lives outside `src/`. Match the same sys.path
# bridge as test_baselines_m5_lightgbm_sandboxed.py — keeps both tests
# bootable from `pytest apps/kernel`.
_REPO_KERNEL = Path(__file__).resolve().parents[1]
if str(_REPO_KERNEL) not in sys.path:
    sys.path.insert(0, str(_REPO_KERNEL))

from ownevo_kernel.benchmark import LabourBenchmarkRunner, LabourCase  # noqa: E402
from ownevo_kernel.db import ENV_VAR  # noqa: E402
from ownevo_kernel.eval_cases import add_eval_case, list_eval_cases  # noqa: E402
from ownevo_kernel.gate import GateDecision, persist_gate_run  # noqa: E402
from ownevo_kernel.sandbox import (  # noqa: E402
    DEFAULT_IMAGE,
    LocalDockerSandbox,
    docker_available,
)
from ownevo_kernel.skills import register_skill  # noqa: E402
from ownevo_kernel.types import (  # noqa: E402
    AuditKind,
    IterationState,
    ProposalState,
    ProvenanceKind,
)

_SKILL_PATH = _REPO_KERNEL / "baselines/labour_v1/skill.py"
_SKILL_ID = "labour.baseline.v1.shift_validator"
_WORKFLOW_ID = "labour-shift-validation"


def _docker_ok() -> bool:
    return asyncio.run(docker_available())


def _image_present(tag: str) -> bool:
    """Skip when the sandbox base image isn't pulled. Mirrors
    test_baselines_m5_lightgbm_sandboxed.py's helper, generalized to
    any tag (the labour proof uses `python:3.11-slim`)."""
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", tag],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


pytestmark = [
    pytest.mark.skipif(
        ENV_VAR not in os.environ,
        reason=f"{ENV_VAR} not set; skipping integration tests",
    ),
    pytest.mark.skipif(
        not _docker_ok() or not _image_present(DEFAULT_IMAGE),
        reason=(
            "Substrate proof requires Docker + the default sandbox image "
            f"({DEFAULT_IMAGE}). Pull with `docker pull {DEFAULT_IMAGE}`."
        ),
    ),
]


# Three cases mapped to the documented Labour management failure modes
# (`ownevo_docs/ownEvo_MVP_mocks.md` § Failure-mode taxonomy). Order
# matters for the eval_cases.created_at fail-fast contract; cleanest
# (most likely to be load-bearing) goes first.
_CASES: tuple[LabourCase, ...] = (
    LabourCase(
        task_id="shift-001-clean",
        shift_hours=8,
        weekly_hours_so_far=32,
        required_skill="forklift",
        worker_skills=("forklift", "loading"),
        expected={"valid": True, "reason": "clean"},
    ),
    LabourCase(
        task_id="shift-002-overtime-cap",
        shift_hours=8,
        weekly_hours_so_far=35,
        required_skill="forklift",
        worker_skills=("forklift",),
        expected={"valid": False, "reason": "overtime_cap"},
    ),
    LabourCase(
        task_id="shift-003-skill-mismatch",
        shift_hours=4,
        weekly_hours_so_far=20,
        required_skill="cnc",
        worker_skills=("forklift", "loading"),
        expected={"valid": False, "reason": "skill_mismatch"},
    ),
)


async def _seed_workflow(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, $2, '{"benchmark": "labour-shift-validation"}'::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        _WORKFLOW_ID,
        "Labour management — shift assignment validator (W2.7 substrate proof)",
    )


async def test_substrate_proves_itself_on_non_m5_workflow(db: asyncpg.Connection):
    """Skill → sandbox → eval → gate → audit, on a non-M5 workflow.

    Asserts the gate decides PASS with val_score=1.0 (the hand-written
    skill solves all three hand-written cases), and verifies the rows
    every primitive is supposed to write actually land in the DB.
    """
    await _seed_workflow(db)

    # 1. Skill registry — `register_skill` parses the YAML frontmatter,
    # writes both the `skills` row (kind, capability_tags) and the head
    # `skill_versions` row.
    skill_content = _SKILL_PATH.read_text()
    reg = await register_skill(db, skill_content, created_by="bootstrap-w2.7")
    assert reg.skill_id == _SKILL_ID
    assert reg.version_seq == 1

    # 2. Eval cases — three hand-authored, mapped to the documented
    # Labour failure modes. provenance=hand-authored matches the seam
    # the gate's W3 cluster→eval-case lift will use later (different
    # provenance, same shape).
    for case in _CASES:
        await add_eval_case(
            db,
            workflow_id=_WORKFLOW_ID,
            provenance=ProvenanceKind.HAND_AUTHORED,
            input={
                "task_id": case.task_id,
                "shift_hours": case.shift_hours,
                "weekly_hours_so_far": case.weekly_hours_so_far,
                "required_skill": case.required_skill,
                "worker_skills": list(case.worker_skills),
            },
            expected_behavior=case.expected,
        )
    persisted_cases = await list_eval_cases(db, workflow_id=_WORKFLOW_ID)
    assert len(persisted_cases) == 3
    assert {c.provenance for c in persisted_cases} == {ProvenanceKind.HAND_AUTHORED}

    # 3. Build the sandbox-backed runner. The labour skill is
    # stdlib-only — the default `python:3.11-slim` image is enough; no
    # domain-specific Dockerfile required (part of the proof).
    sandbox = LocalDockerSandbox(image=DEFAULT_IMAGE, tmpfs_size_mb=64)
    runner = LabourBenchmarkRunner(
        cases=_CASES,
        skill_content=skill_content,
        sandbox=sandbox,
        timeout_seconds=60.0,
        memory_mb=256,
    )

    # 4. Drive the gate through `persist_gate_run` — the production
    # entry-point for any agent loop. Bootstrap shape: empty prior suite,
    # no best-ever score (gate steps 1+2 skip; step 3 promotes all
    # newly-passing cases).
    persisted = await persist_gate_run(
        db,
        runner,
        workflow_id=_WORKFLOW_ID,
        skill_id=reg.skill_id,
        proposed_skill_version_id=reg.version_id,
        proposed_content=skill_content,
        plain_language_summary="Initial labour shift validator (W2.7 substrate proof).",
        actor="bootstrap-w2.7",
        prior_eval_task_ids=(),
        best_ever_score=None,
    )

    # 5. Gate decision: PASS, val_score=1.0 (3/3 cases match expected).
    assert persisted.gate_result.decision == GateDecision.PASS, (
        f"gate did not pass: {persisted.gate_result.rationale}"
    )
    assert persisted.gate_result.val_score == 1.0
    assert persisted.gate_result.best_ever_score_after == 1.0

    # All 3 hand-authored cases promotable (passed at threshold, none in
    # prior suite). The gate doesn't auto-write them — that's W3's job —
    # but it surfaces them on the result for the caller.
    assert set(persisted.gate_result.promotable_task_ids) == {
        c.task_id for c in _CASES
    }

    # 6. Iteration row finalized in `gate-pass`, val_score persisted.
    assert persisted.iteration.state == IterationState.GATE_PASS
    assert persisted.iteration.val_score == 1.0
    assert persisted.iteration.best_ever_score_after == 1.0
    assert persisted.iteration.iteration_index == 0  # first iteration on this workflow
    assert persisted.iteration.sandbox_error_class is None

    # 7. Proposal row finalized in `gate-passed`, eval_score = val_score,
    # rationale captured for the approval UI.
    assert persisted.proposal.state == ProposalState.GATE_PASSED
    assert persisted.proposal.eval_score == 1.0
    assert persisted.proposal.eval_rationale is not None
    assert "Gate passed" in persisted.proposal.eval_rationale

    # 8. Audit chain: gate-run-started + gate-run-completed, both linked
    # to the iteration. The marketing claim is "append-only audit log,
    # customer-controlled export"; this is the floor.
    audit_rows = await db.fetch(
        "SELECT kind::text AS kind, related_id, actor "
        "FROM audit_entries WHERE related_id = $1 ORDER BY seq ASC",
        persisted.iteration.id,
    )
    assert [r["kind"] for r in audit_rows] == [
        AuditKind.GATE_RUN_STARTED.value,
        AuditKind.GATE_RUN_COMPLETED.value,
    ]
    assert all(r["actor"] == "bootstrap-w2.7" for r in audit_rows)
