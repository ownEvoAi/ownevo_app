"""Integration tests for /api/proposals/{id}/deploy + /rollback (TODO-34).

Pins the HTTP status conventions for the deploy/rollback surface so the
web app's Server Action can rely on them:

  * 200 on success — body carries the new proposal state and the
    skill's post-transition production pointer.
  * 404 when the proposal id doesn't exist.
  * 409 when the proposal is in the wrong state for the action, or
    when a second proposal on the same skill is already deployed.
  * 422 when `decided_by` is empty or missing.

Also confirms `/api/skills/{id}` exposes the production-pointer fields
(`deployed_version_seq`, `deployable_proposal_id`,
`deployed_proposal_id`) the skill detail page needs for button-visibility.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_proposal(
    conn: asyncpg.Connection,
    *,
    workflow_id: str = "wf-deploy-api",
    skill_id: str = "test.skill.deploy.api",
    state: str = "approved-awaiting-deploy",
    version_seq: int = 1,
) -> tuple[UUID, UUID]:
    """Returns (proposal_id, skill_version_id)."""
    await conn.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, 'test', '{}'::jsonb) ON CONFLICT DO NOTHING",
        workflow_id,
    )
    await conn.execute(
        "INSERT INTO skills (id, kind, workflow_id) "
        "VALUES ($1, 'python'::skill_kind, $2) ON CONFLICT DO NOTHING",
        skill_id, workflow_id,
    )
    sv_id = await conn.fetchval(
        """
        INSERT INTO skill_versions (skill_id, version_seq, content, created_by)
        VALUES ($1, $2, 'body', 'test')
        RETURNING id
        """,
        skill_id, version_seq,
    )
    iter_id = await conn.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state,
                                proposed_skill_version_id, ended_at)
        VALUES ($1, $2, 'gate-pass'::iteration_state, $3, now())
        RETURNING id
        """,
        workflow_id,
        await conn.fetchval(
            "SELECT COALESCE(MAX(iteration_index), -1) + 1 FROM iterations "
            "WHERE workflow_id = $1",
            workflow_id,
        ),
        sv_id,
    )
    proposal_id = await conn.fetchval(
        """
        INSERT INTO proposals (
            iteration_id, skill_id, parent_version_id, proposed_content,
            plain_language_summary, state, eval_score
        )
        VALUES ($1, $2, $3, 'body', 'summary', $4::proposal_state, 0.95)
        RETURNING id
        """,
        iter_id, skill_id, sv_id, state,
    )
    return proposal_id, sv_id


# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------


async def test_deploy_returns_200_and_advances_state(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    proposal_id, sv_id = await _seed_proposal(db)
    res = await api_client.post(
        f"/api/proposals/{proposal_id}/deploy",
        json={"decided_by": "human:operator"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["proposal_id"] == str(proposal_id)
    assert body["state"] == "deployed"
    assert body["skill_id"] == "test.skill.deploy.api"
    assert body["skill_deployed_version_id"] == str(sv_id)


async def test_deploy_404_for_unknown_proposal(api_client: httpx.AsyncClient):
    res = await api_client.post(
        f"/api/proposals/{uuid4()}/deploy",
        json={"decided_by": "human:operator"},
    )
    assert res.status_code == 404


async def test_deploy_409_when_state_wrong(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    proposal_id, _ = await _seed_proposal(db, state="gate-passed")
    res = await api_client.post(
        f"/api/proposals/{proposal_id}/deploy",
        json={"decided_by": "human:operator"},
    )
    assert res.status_code == 409
    assert "approved-awaiting-deploy" in res.json()["detail"]


async def test_deploy_422_when_decided_by_missing(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    proposal_id, _ = await _seed_proposal(db)
    res = await api_client.post(
        f"/api/proposals/{proposal_id}/deploy", json={},
    )
    assert res.status_code == 422


async def test_deploy_409_when_another_proposal_already_deployed(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """Attempting to deploy a second proposal while one is already deployed
    returns 409 — enforced by the partial unique index + service guard."""
    p1, _ = await _seed_proposal(db, version_seq=1)
    await api_client.post(
        f"/api/proposals/{p1}/deploy",
        json={"decided_by": "human:operator"},
    )
    p2, _ = await _seed_proposal(db, version_seq=2)
    res = await api_client.post(
        f"/api/proposals/{p2}/deploy",
        json={"decided_by": "human:operator"},
    )
    assert res.status_code == 409
    assert "already has a deployed" in res.json()["detail"]


async def test_deploy_422_when_decided_by_whitespace(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """A whitespace-only decided_by is invalid and must return 422."""
    proposal_id, _ = await _seed_proposal(db, version_seq=3)
    res = await api_client.post(
        f"/api/proposals/{proposal_id}/deploy",
        json={"decided_by": "   "},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


async def test_rollback_clears_pointer_after_deploy(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    proposal_id, _ = await _seed_proposal(db)
    deploy_res = await api_client.post(
        f"/api/proposals/{proposal_id}/deploy",
        json={"decided_by": "human:operator"},
    )
    assert deploy_res.status_code == 200, deploy_res.text

    rb_res = await api_client.post(
        f"/api/proposals/{proposal_id}/rollback",
        json={"decided_by": "human:operator"},
    )
    assert rb_res.status_code == 200, rb_res.text
    body = rb_res.json()
    assert body["state"] == "rolled-back"
    assert body["skill_deployed_version_id"] is None


async def test_rollback_409_when_not_deployed(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    proposal_id, _ = await _seed_proposal(db)
    res = await api_client.post(
        f"/api/proposals/{proposal_id}/rollback",
        json={"decided_by": "human:operator"},
    )
    assert res.status_code == 409


# ---------------------------------------------------------------------------
# Skill detail surface
# ---------------------------------------------------------------------------


async def test_skill_detail_exposes_deployable_and_deployed(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    proposal_id, sv_id = await _seed_proposal(db)
    skill_id = "test.skill.deploy.api"

    # Pre-deploy: deployable_proposal_id is set, deployed_proposal_id is null.
    res = await api_client.get(f"/api/skills/{skill_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deployable_proposal_id"] == str(proposal_id)
    assert body["deployable_proposal_version_seq"] == 1
    assert body["deployed_proposal_id"] is None
    assert body["deployed_version_id"] is None
    assert body["deployed_version_seq"] is None

    # Post-deploy: deployable cleared, deployed populated.
    await api_client.post(
        f"/api/proposals/{proposal_id}/deploy",
        json={"decided_by": "human:operator"},
    )
    res = await api_client.get(f"/api/skills/{skill_id}")
    body = res.json()
    assert body["deployable_proposal_id"] is None
    assert body["deployed_proposal_id"] == str(proposal_id)
    assert body["deployed_version_id"] == str(sv_id)
    assert body["deployed_version_seq"] == 1
