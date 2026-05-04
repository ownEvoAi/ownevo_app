"""Integration tests for the W2.5 approval REST API.

Exercises every endpoint against a real Postgres + a real FastAPI app
via `httpx.ASGITransport` (in-process — no network sockets, no uvicorn).
Tests cover:

  * health: 200 + db='ok'
  * GET /api/proposals — list, state filter, workflow filter, limit cap
  * GET /api/proposals/:id — joins iteration + workflow + audit + approval
  * GET /api/proposals/:id — 404 on unknown id
  * POST /approve — 200, state advances, body validation
  * POST /approve — 404 on unknown id
  * POST /approve — 409 on illegal start state
  * POST /reject — 200, state + eval_case + becameeval_case_id linkage
  * POST /reject — comment becomes eval-case `expected_behavior.note`
  * POST /approve — 422 on empty `decided_by`
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import httpx
import pytest
from httpx import ASGITransport
from ownevo_kernel.api.app import create_app
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.types import ProposalState

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures: build an httpx AsyncClient over the FastAPI app + pool
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_client(db: asyncpg.Connection):
    """Wrap the per-test `db` connection's database in a one-shot pool that
    the FastAPI lifespan manages, then build an in-process httpx client.

    `db` is an asyncpg.Connection on a fresh per-test database (created by
    conftest.py). We ask Postgres which database the connection is attached
    to and reconstruct a DSN from the env var pointing at that DB, then
    spin a small pool the FastAPI app holds for the test's duration."""
    dbname = await db.fetchval("SELECT current_database()")
    parsed = urlparse(os.environ[ENV_VAR])
    dsn = urlunparse(parsed._replace(path=f"/{dbname}"))
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        app = create_app(pool=pool, cors_origins=[])
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=transport, base_url="http://api.test",
        ) as client:
            yield client
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_proposal(
    conn: asyncpg.Connection,
    *,
    state: ProposalState = ProposalState.GATE_PASSED,
    workflow_id: str = "wf-api-test",
    skill_id: str = "test.skill.api",
    iteration_index: int = 0,
):
    """Create the workflow/skill/iteration/proposal chain. Returns the ids
    so the test can hit the API by id."""
    await conn.execute(
        "INSERT INTO workflows (id, description, spec) VALUES ($1, $2, '{}'::jsonb) "
        "ON CONFLICT DO NOTHING",
        workflow_id,
        f"Test workflow ({workflow_id})",
    )
    await conn.execute(
        "INSERT INTO skills (id, kind) VALUES ($1, 'python'::skill_kind) "
        "ON CONFLICT DO NOTHING",
        skill_id,
    )
    iteration_id = await conn.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, val_score,
                                best_ever_score_after, ended_at)
        VALUES ($1, $2, 'gate-pass'::iteration_state, 0.92, 0.92, now())
        RETURNING id
        """,
        workflow_id,
        iteration_index,
    )
    proposal_id = await conn.fetchval(
        """
        INSERT INTO proposals (
            iteration_id, skill_id, proposed_content, plain_language_summary,
            state, eval_score, eval_rationale, expected_impact
        )
        VALUES ($1, $2, 'def foo(): pass', 'Add early-warning detector',
                $3::proposal_state, 0.92,
                'Gate passed: val_score 0.9200 (initial baseline); 3 promotable task(s)',
                '{"forecast_accuracy": 0.011}'::jsonb)
        RETURNING id
        """,
        iteration_id,
        skill_id,
        state.value,
    )
    return {
        "workflow_id": workflow_id,
        "skill_id": skill_id,
        "iteration_id": iteration_id,
        "proposal_id": proposal_id,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health_returns_db_ok(api_client: httpx.AsyncClient):
    resp = await api_client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_proposals_empty(api_client: httpx.AsyncClient):
    resp = await api_client.get("/api/proposals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_proposals_filter_by_state(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded_passed = await _seed_proposal(db, state=ProposalState.GATE_PASSED)
    seeded_in_gate = await _seed_proposal(
        db,
        state=ProposalState.IN_GATE,
        workflow_id="wf-api-test-2",
        skill_id="test.skill.api2",
        iteration_index=1,
    )

    resp = await api_client.get("/api/proposals", params={"state": "gate-passed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    [item] = body["items"]
    assert item["id"] == str(seeded_passed["proposal_id"])
    assert item["state"] == "gate-passed"
    assert item["workflow_description"] == "Test workflow (wf-api-test)"
    assert item["expected_impact"] == {"forecast_accuracy": 0.011}

    # Different filter, different row.
    resp2 = await api_client.get("/api/proposals", params={"state": "in-gate"})
    assert resp2.status_code == 200
    [item2] = resp2.json()["items"]
    assert item2["id"] == str(seeded_in_gate["proposal_id"])


async def test_list_proposals_workflow_filter(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded_a = await _seed_proposal(db, workflow_id="wf-a")
    await _seed_proposal(db, workflow_id="wf-b", skill_id="test.skill.b")

    resp = await api_client.get("/api/proposals", params={"workflow_id": "wf-a"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(seeded_a["proposal_id"])


async def test_list_limit_validation_rejects_oversize(api_client: httpx.AsyncClient):
    resp = await api_client.get("/api/proposals", params={"limit": 10000})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


async def test_get_proposal_detail(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)

    resp = await api_client.get(f"/api/proposals/{seeded['proposal_id']}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["id"] == str(seeded["proposal_id"])
    assert body["proposed_content"] == "def foo(): pass"
    assert body["plain_language_summary"] == "Add early-warning detector"
    assert body["state"] == "gate-passed"
    assert body["eval_score"] == 0.92
    assert body["expected_impact"] == {"forecast_accuracy": 0.011}

    # Iteration sub-object
    assert body["iteration"]["id"] == str(seeded["iteration_id"])
    assert body["iteration"]["state"] == "gate-pass"
    assert body["iteration"]["val_score"] == 0.92

    # Workflow sub-object
    assert body["workflow"]["id"] == seeded["workflow_id"]
    assert body["workflow"]["description"] == "Test workflow (wf-api-test)"
    assert body["workflow"]["mode"] == "gated"

    # No parent version on bootstrap iteration
    assert body["parent_version_id"] is None
    assert body["parent_version_content"] is None
    assert body["parent_version_seq"] is None

    # No audit entries yet (we didn't go through persist_gate_run)
    assert body["audit_entries"] == []

    # No approval yet
    assert body["approval"] is None


async def test_get_proposal_detail_404_on_unknown(api_client: httpx.AsyncClient):
    resp = await api_client.get(f"/api/proposals/{uuid4()}")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


async def test_approve_advances_state(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)

    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/approve",
        json={"decided_by": "human:reviewer", "comment": "ship it"},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["proposal_id"] == str(seeded["proposal_id"])
    assert body["state"] == "approved-awaiting-deploy"
    assert body["approval"]["decision"] == "approve"
    assert body["approval"]["decided_by"] == "human:reviewer"
    assert body["approval"]["approver_type"] == "human"
    assert body["approval"]["comment"] == "ship it"
    assert body["approval"]["became_eval_case_id"] is None

    # Re-fetch detail — approval populated, state advanced, audit entry written.
    detail_resp = await api_client.get(f"/api/proposals/{seeded['proposal_id']}")
    detail = detail_resp.json()
    assert detail["state"] == "approved-awaiting-deploy"
    assert detail["approval"]["decision"] == "approve"
    [audit] = detail["audit_entries"]
    assert audit["kind"] == "proposal-approved"
    assert audit["actor"] == "human:reviewer"


async def test_approve_404_on_unknown(api_client: httpx.AsyncClient):
    resp = await api_client.post(
        f"/api/proposals/{uuid4()}/approve",
        json={"decided_by": "human:reviewer"},
    )
    assert resp.status_code == 404


async def test_approve_409_on_wrong_state(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db, state=ProposalState.IN_GATE)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/approve",
        json={"decided_by": "human:reviewer"},
    )
    assert resp.status_code == 409
    assert "gate-passed" in resp.json()["detail"]


async def test_approve_422_on_empty_decided_by(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/approve",
        json={"decided_by": ""},
    )
    assert resp.status_code == 422


async def test_approve_with_autonomous_approver_type(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/approve",
        json={"decided_by": "autonomous", "approver_type": "autonomous"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval"]["approver_type"] == "autonomous"


async def test_approve_invalid_approver_type(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/approve",
        json={"decided_by": "human:reviewer", "approver_type": "robot"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Reject + comment-to-eval-case
# ---------------------------------------------------------------------------


async def test_reject_with_comment_creates_eval_case(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db, workflow_id="wf-api-reject")

    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/reject",
        json={
            "decided_by": "human:reviewer",
            "comment": "still misses the weekend OT cap edge case",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "rejected"
    assert body["approval"]["decision"] == "reject"
    assert body["approval"]["became_eval_case_id"] is not None

    # Verify the eval case row has the right provenance + workflow + note.
    case_row = await db.fetchrow(
        "SELECT provenance::text AS provenance, workflow_id, expected_behavior "
        "FROM eval_cases WHERE id = $1",
        body["approval"]["became_eval_case_id"],
    )
    assert case_row["provenance"] == "rejected-feedback"
    assert case_row["workflow_id"] == "wf-api-reject"

    import json
    expected = (
        json.loads(case_row["expected_behavior"])
        if isinstance(case_row["expected_behavior"], str)
        else case_row["expected_behavior"]
    )
    assert "weekend OT cap" in expected["note"]


async def test_reject_without_comment_no_eval_case(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/reject",
        json={"decided_by": "human:reviewer"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval"]["became_eval_case_id"] is None


async def test_reject_404_on_unknown(api_client: httpx.AsyncClient):
    resp = await api_client.post(
        f"/api/proposals/{uuid4()}/reject",
        json={"decided_by": "human:reviewer"},
    )
    assert resp.status_code == 404


async def test_reject_409_on_wrong_state(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db, state=ProposalState.IN_GATE)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/reject",
        json={"decided_by": "human:reviewer"},
    )
    assert resp.status_code == 409
    assert "gate-passed" in resp.json()["detail"]


async def test_reject_422_on_empty_decided_by(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/reject",
        json={"decided_by": "   "},
    )
    assert resp.status_code == 422
