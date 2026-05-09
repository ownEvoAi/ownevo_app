"""DB-backed integration tests for deploy/rollback (TODO-32).

Pins the two transitions added beyond `service.py`'s approve/reject:

  * `approved-awaiting-deploy → deployed` via `deploy_proposal`
  * `deployed → rolled-back` via `rollback_proposal`

Plus the side effects on `skills.deployed_version_id` (production
pointer; advanced on deploy, reverted on rollback) and the matching
`proposal-deployed` / `proposal-rolled-back` audit entries.

Uses the `db` fixture from conftest.py — fresh DB per test, migration
applied (incl. 0004), asyncpg connection.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import asyncpg
import pytest
from ownevo_kernel.approvals import (
    ApprovalStateError,
    ProposalNotFoundError,
    deploy_proposal,
    rollback_proposal,
)
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.types import AuditKind, ProposalState

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Fixture helpers — seed (workflow → skill → skill_version → iteration → proposal)
# ---------------------------------------------------------------------------


async def _seed_deployable_proposal(
    conn: asyncpg.Connection,
    *,
    state: ProposalState = ProposalState.APPROVED_AWAITING_DEPLOY,
    workflow_id: str = "wf-test-deploy",
    skill_id: str = "test.skill.deploy",
    version_seq: int = 1,
) -> tuple[UUID, UUID, UUID]:
    """Returns (proposal_id, iteration_id, skill_version_id).

    Seeds a full lineage so the deploy path has a concrete
    `iterations.proposed_skill_version_id` to point production at.
    """
    await conn.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, 'test', '{}'::jsonb) "
        "ON CONFLICT DO NOTHING",
        workflow_id,
    )
    await conn.execute(
        "INSERT INTO skills (id, kind) VALUES ($1, 'python'::skill_kind) "
        "ON CONFLICT DO NOTHING",
        skill_id,
    )
    skill_version_id = await conn.fetchval(
        """
        INSERT INTO skill_versions (
            skill_id, version_seq, content, created_by
        )
        VALUES ($1, $2, 'pretend skill body', 'test')
        RETURNING id
        """,
        skill_id, version_seq,
    )
    iteration_id = await conn.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, val_score,
                                best_ever_score_after, ended_at,
                                proposed_skill_version_id)
        VALUES ($1, $2, 'gate-pass'::iteration_state, 0.95, 0.95, now(), $3)
        RETURNING id
        """,
        workflow_id,
        # Each call gets a fresh iteration_index per workflow.
        await conn.fetchval(
            "SELECT COALESCE(MAX(iteration_index), -1) + 1 FROM iterations "
            "WHERE workflow_id = $1",
            workflow_id,
        ),
        skill_version_id,
    )
    proposal_id = await conn.fetchval(
        """
        INSERT INTO proposals (
            iteration_id, skill_id, parent_version_id, proposed_content,
            plain_language_summary, state, eval_score, eval_rationale
        )
        VALUES ($1, $2, $3, 'pretend skill body', 'Test deploy proposal',
                $4::proposal_state, 0.95, 'Gate passed: 0.95')
        RETURNING id
        """,
        iteration_id, skill_id, skill_version_id, state.value,
    )
    return proposal_id, iteration_id, skill_version_id


# ---------------------------------------------------------------------------
# Deploy path
# ---------------------------------------------------------------------------


async def test_deploy_advances_state_and_pointer(db: asyncpg.Connection):
    proposal_id, _, version_id = await _seed_deployable_proposal(db)

    proposal = await deploy_proposal(
        db, proposal_id=proposal_id, decided_by="human:operator",
    )

    assert proposal.state == ProposalState.DEPLOYED
    state = await db.fetchval(
        "SELECT state::text FROM proposals WHERE id = $1", proposal_id,
    )
    assert state == "deployed"
    deployed = await db.fetchval(
        "SELECT deployed_version_id FROM skills WHERE id = $1",
        "test.skill.deploy",
    )
    assert deployed == version_id


async def test_deploy_writes_audit_entry(db: asyncpg.Connection):
    proposal_id, _, version_id = await _seed_deployable_proposal(db)
    await deploy_proposal(
        db, proposal_id=proposal_id, decided_by="human:operator",
    )
    audit = await db.fetchrow(
        """
        SELECT kind::text AS kind, actor, payload, related_id
        FROM audit_entries
        WHERE related_id = $1 AND kind = 'proposal-deployed'::audit_kind
        """,
        proposal_id,
    )
    assert audit is not None
    assert audit["kind"] == AuditKind.PROPOSAL_DEPLOYED.value
    assert audit["actor"] == "human:operator"
    assert audit["related_id"] == proposal_id
    import json as _json
    payload = (
        _json.loads(audit["payload"])
        if isinstance(audit["payload"], str)
        else audit["payload"]
    )
    assert payload["proposal_id"] == str(proposal_id)
    assert payload["skill_id"] == "test.skill.deploy"
    assert payload["deployed_version_id"] == str(version_id)
    # Bootstrap deploy: there was no prior production version.
    assert payload["prior_deployed_version_id"] is None


async def test_deploy_rejects_non_awaiting_state(db: asyncpg.Connection):
    """A gate-passed proposal that hasn't been approved yet can't be deployed."""
    proposal_id, _, _ = await _seed_deployable_proposal(
        db, state=ProposalState.GATE_PASSED,
    )
    with pytest.raises(ApprovalStateError, match="approved-awaiting-deploy"):
        await deploy_proposal(
            db, proposal_id=proposal_id, decided_by="human:operator",
        )


async def test_deploy_rejects_when_skill_already_deployed(
    db: asyncpg.Connection,
):
    """Two awaiting-deploy proposals on the same skill: the operator must
    rollback the live one before deploying a newer one. Otherwise we'd
    silently leak the prior deployment."""
    p1, _, _ = await _seed_deployable_proposal(db, version_seq=1)
    await deploy_proposal(db, proposal_id=p1, decided_by="human:operator")

    # Seed a second proposal on the same skill, also awaiting deploy.
    p2, _, _ = await _seed_deployable_proposal(db, version_seq=2)
    with pytest.raises(ApprovalStateError, match="already has a deployed"):
        await deploy_proposal(db, proposal_id=p2, decided_by="human:operator")


async def test_deploy_unknown_proposal_raises(db: asyncpg.Connection):
    with pytest.raises(ProposalNotFoundError):
        await deploy_proposal(
            db, proposal_id=uuid4(), decided_by="human:operator",
        )


async def test_deploy_requires_decided_by(db: asyncpg.Connection):
    proposal_id, _, _ = await _seed_deployable_proposal(db)
    with pytest.raises(ValueError, match="decided_by"):
        await deploy_proposal(db, proposal_id=proposal_id, decided_by="   ")


# ---------------------------------------------------------------------------
# Rollback path
# ---------------------------------------------------------------------------


async def test_rollback_clears_pointer_when_no_prior_deployment(
    db: asyncpg.Connection,
):
    """Single-deploy lineage: rollback returns the production pointer to NULL."""
    p1, _, _ = await _seed_deployable_proposal(db, version_seq=1)
    await deploy_proposal(db, proposal_id=p1, decided_by="human:operator")

    proposal = await rollback_proposal(
        db, proposal_id=p1, decided_by="human:operator",
    )
    assert proposal.state == ProposalState.ROLLED_BACK
    state = await db.fetchval(
        "SELECT state::text FROM proposals WHERE id = $1", p1,
    )
    assert state == "rolled-back"
    deployed = await db.fetchval(
        "SELECT deployed_version_id FROM skills WHERE id = $1",
        "test.skill.deploy",
    )
    assert deployed is None


async def test_rollback_restores_prior_deployment(db: asyncpg.Connection):
    """Two-deploy lineage: rollback of the live one points production
    back at whatever was deployed immediately prior. Without this the
    rollback semantics would be 'remove change' instead of 'revert to
    previous good'."""
    p1, _, v1 = await _seed_deployable_proposal(db, version_seq=1)
    await deploy_proposal(db, proposal_id=p1, decided_by="human:operator")

    # Roll back v1 to clear the slot, then deploy v2.
    await rollback_proposal(db, proposal_id=p1, decided_by="human:operator")
    p2, _, v2 = await _seed_deployable_proposal(db, version_seq=2)
    await deploy_proposal(db, proposal_id=p2, decided_by="human:operator")

    # Rollback v2 — should restore v1 as the live version.
    await rollback_proposal(db, proposal_id=p2, decided_by="human:operator")
    deployed = await db.fetchval(
        "SELECT deployed_version_id FROM skills WHERE id = $1",
        "test.skill.deploy",
    )
    assert deployed == v1


async def test_rollback_writes_audit_entry(db: asyncpg.Connection):
    p1, _, v1 = await _seed_deployable_proposal(db, version_seq=1)
    await deploy_proposal(db, proposal_id=p1, decided_by="human:operator")
    await rollback_proposal(db, proposal_id=p1, decided_by="human:operator")

    audit = await db.fetchrow(
        """
        SELECT kind::text AS kind, actor, payload
        FROM audit_entries
        WHERE related_id = $1 AND kind = 'proposal-rolled-back'::audit_kind
        """,
        p1,
    )
    assert audit is not None
    assert audit["kind"] == AuditKind.PROPOSAL_ROLLED_BACK.value
    assert audit["actor"] == "human:operator"
    import json as _json
    payload = (
        _json.loads(audit["payload"])
        if isinstance(audit["payload"], str)
        else audit["payload"]
    )
    assert payload["proposal_id"] == str(p1)
    assert payload["rolled_back_version_id"] == str(v1)
    assert payload["restored_deployed_version_id"] is None


async def test_rollback_rejects_non_deployed_proposal(db: asyncpg.Connection):
    """A proposal that's only approved-awaiting-deploy can't be rolled back —
    rollback is a state transition out of `deployed` only."""
    p1, _, _ = await _seed_deployable_proposal(db)
    with pytest.raises(ApprovalStateError, match="deployed"):
        await rollback_proposal(
            db, proposal_id=p1, decided_by="human:operator",
        )


async def test_rollback_unknown_proposal_raises(db: asyncpg.Connection):
    with pytest.raises(ProposalNotFoundError):
        await rollback_proposal(
            db, proposal_id=uuid4(), decided_by="human:operator",
        )


# ---------------------------------------------------------------------------
# Idempotency / double-action
# ---------------------------------------------------------------------------


async def test_double_deploy_rejects_second_call(db: asyncpg.Connection):
    """Same proposal, two deploys: second must fail (already in 'deployed').
    Idempotency falls out of the state-machine check, not a separate guard."""
    p1, _, _ = await _seed_deployable_proposal(db)
    await deploy_proposal(db, proposal_id=p1, decided_by="human:operator")
    with pytest.raises(ApprovalStateError):
        await deploy_proposal(db, proposal_id=p1, decided_by="human:operator")


async def test_double_rollback_rejects_second_call(db: asyncpg.Connection):
    p1, _, _ = await _seed_deployable_proposal(db)
    await deploy_proposal(db, proposal_id=p1, decided_by="human:operator")
    await rollback_proposal(db, proposal_id=p1, decided_by="human:operator")
    with pytest.raises(ApprovalStateError):
        await rollback_proposal(db, proposal_id=p1, decided_by="human:operator")
