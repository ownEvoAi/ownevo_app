"""Integration tests for POST /api/proposals/{id}/ship-copilot-studio.

Unlike ship-langsmith there is no external adapter to mock — Copilot
Studio has no fix-feedback API, so the route only records a
plain-language diff to the audit chain. The assertions target the
route's preconditions, the audit entry it writes, and idempotency.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import httpx
import pytest
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping ship-copilot-studio tests",
)


async def _seed_deployed_proposal(
    db: asyncpg.Connection,
    *,
    wf_id: str = "wf-cs-ship",
    origin: str | None = "copilot_studio",
    state: str = "deployed",
) -> str:
    await db.execute(
        "INSERT INTO workflows (id, description, spec, origin) "
        "VALUES ($1, 'cs ship test', '{}'::jsonb, $2)",
        wf_id,
        origin,
    )
    await db.execute(
        "INSERT INTO skills (id, kind, workflow_id) "
        "VALUES ('skill.cs', 'python'::skill_kind, $1)",
        wf_id,
    )
    version_id = await db.fetchval(
        """
        INSERT INTO skill_versions (skill_id, version_seq, content, created_by)
        VALUES ('skill.cs', 1, 'Always cross-check the holiday calendar.', 'human:test')
        RETURNING id
        """,
    )
    await db.execute(
        "UPDATE skills SET deployed_version_id = $1 WHERE id = 'skill.cs'",
        version_id,
    )
    iteration_id = await db.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, val_score,
                                best_ever_score_after, ended_at)
        VALUES ($1, 0, 'gate-pass'::iteration_state, 0.9, 0.9, now())
        RETURNING id
        """,
        wf_id,
    )
    proposal_id = await db.fetchval(
        """
        INSERT INTO proposals (iteration_id, skill_id, proposed_content,
                               plain_language_summary, state, eval_score)
        VALUES ($1, 'skill.cs', 'Always cross-check the holiday calendar.',
                'Fix holiday markdown false-negatives', $2::proposal_state, 0.9)
        RETURNING id
        """,
        iteration_id,
        state,
    )
    return str(proposal_id)


async def test_ship_happy_path_writes_audit(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    pid = await _seed_deployed_proposal(db)
    resp = await api_client.post(f"/api/proposals/{pid}/ship-copilot-studio", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["already_delivered"] is False
    assert "holiday calendar" in body["instruction_text"]
    assert body["summary"]

    audit = await db.fetchrow(
        "SELECT payload FROM audit_entries "
        "WHERE kind = 'fix-exported-copilot-studio' AND related_id = $1",
        uuid.UUID(pid),
    )
    assert audit is not None


async def test_ship_is_idempotent(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    pid = await _seed_deployed_proposal(db)
    r1 = await api_client.post(f"/api/proposals/{pid}/ship-copilot-studio", json={})
    assert r1.status_code == 200
    r2 = await api_client.post(f"/api/proposals/{pid}/ship-copilot-studio", json={})
    assert r2.status_code == 200
    assert r2.json()["already_delivered"] is True

    count = await db.fetchval(
        "SELECT count(*) FROM audit_entries "
        "WHERE kind = 'fix-exported-copilot-studio' AND related_id = $1",
        uuid.UUID(pid),
    )
    assert count == 1


async def test_ship_404_for_unknown(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.post(
        f"/api/proposals/{uuid.uuid4()}/ship-copilot-studio", json={}
    )
    assert resp.status_code == 404


async def test_ship_422_non_copilot_origin(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    pid = await _seed_deployed_proposal(db, wf_id="wf-cs-greenfield", origin=None)
    resp = await api_client.post(f"/api/proposals/{pid}/ship-copilot-studio", json={})
    assert resp.status_code == 422


async def test_ship_422_not_deployed(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    pid = await _seed_deployed_proposal(db, wf_id="wf-cs-pending", state="gate-passed")
    resp = await api_client.post(f"/api/proposals/{pid}/ship-copilot-studio", json={})
    assert resp.status_code == 422
