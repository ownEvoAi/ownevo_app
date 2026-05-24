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


# ---------------------------------------------------------------------------
# POST /request-changes
# ---------------------------------------------------------------------------


async def test_request_changes_200_advances_state(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/request-changes",
        json={"decided_by": "human:reviewer", "comment": "soften the seasonal cap"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "changes-requested"
    assert body["proposal_id"] == str(seeded["proposal_id"])


async def test_request_changes_422_on_missing_comment(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/request-changes",
        json={"decided_by": "human:reviewer"},
    )
    assert resp.status_code == 422


async def test_request_changes_422_on_blank_comment(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/request-changes",
        json={"decided_by": "human:reviewer", "comment": "   "},
    )
    assert resp.status_code == 422


async def test_request_changes_404_on_unknown(api_client: httpx.AsyncClient):
    resp = await api_client.post(
        f"/api/proposals/{uuid4()}/request-changes",
        json={"decided_by": "human:reviewer", "comment": "adjust threshold"},
    )
    assert resp.status_code == 404


async def test_request_changes_409_on_wrong_state(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    seeded = await _seed_proposal(db, state=ProposalState.IN_GATE)
    resp = await api_client.post(
        f"/api/proposals/{seeded['proposal_id']}/request-changes",
        json={"decided_by": "human:reviewer", "comment": "redirect the agent"},
    )
    assert resp.status_code == 409
    assert "gate-passed" in resp.json()["detail"]


async def test_list_proposals_filter_by_changes_requested(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    """?state=changes-requested returns only proposals in that state."""
    seeded_cr = await _seed_proposal(db, state=ProposalState.CHANGES_REQUESTED)
    await _seed_proposal(db, state=ProposalState.GATE_PASSED)

    resp = await api_client.get("/api/proposals?state=changes-requested")
    assert resp.status_code == 200
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    assert str(seeded_cr["proposal_id"]) in ids
    assert all(item["state"] == "changes-requested" for item in body["items"])


# ---------------------------------------------------------------------------
# 9.2.3 — ordering-inversion check for metric proposals
# ---------------------------------------------------------------------------


async def _seed_metric_workflow_with_outputs(
    db: asyncpg.Connection,
    *,
    workflow_id: str,
    target_label_field: str = "alert_correct",
    cases: list[dict] | None = None,
    iterations: list[list[bool]] | None = None,
):
    """Seed workflow + metric_definition + eval cases + iterations
    each with iteration_case_outputs.

    `cases` is a list of {case_id, expected_value}. `iterations` is
    a list-of-list of bools: iterations[i][j] is the j-th case's
    predicted value in iteration i.
    """
    cases = cases or [
        {"case_id": "c1", "expected_value": True},
        {"case_id": "c2", "expected_value": True},
        {"case_id": "c3", "expected_value": False},
        {"case_id": "c4", "expected_value": False},
    ]
    iterations = iterations or [[True, False, True, False]]
    metric_definition = {
        "schema_version": "0.1",
        "workflow_spec_id": workflow_id,
        "name": "pass-rate",
        "family": "pass_rate",
        "direction": "maximize",
        "lower_bound": 0.0,
        "upper_bound": 1.0,
        "target_value": 0.75,
        "description": "Fraction of cases passing.",
        "rationale": "Demo seed.",
        "provenance": {"kind": "derived", "source": "test fixture"},
    }
    await db.execute(
        """
        INSERT INTO workflows (id, description, spec, metric_definition)
        VALUES ($1, $2, '{}'::jsonb, $3::jsonb)
        ON CONFLICT (id) DO UPDATE SET metric_definition = EXCLUDED.metric_definition
        """,
        workflow_id,
        f"Inversion test {workflow_id}",
        __import__("json").dumps(metric_definition),
    )

    case_ids = []
    for c in cases:
        case_id = await db.fetchval(
            """
            INSERT INTO eval_cases (workflow_id, provenance, input, expected_behavior)
            VALUES ($1, 'cluster-derived', '{}'::jsonb, $2::jsonb)
            RETURNING id
            """,
            workflow_id,
            __import__("json").dumps(
                {
                    "case_id": c["case_id"],
                    "target_label_field": target_label_field,
                    "expected_value": c["expected_value"],
                }
            ),
        )
        case_ids.append(case_id)

    for iter_idx, predictions in enumerate(iterations):
        iter_id = await db.fetchval(
            """
            INSERT INTO iterations
                (workflow_id, iteration_index, state, val_score,
                 best_ever_score_after, ended_at)
            VALUES ($1, $2, 'gate-pass'::iteration_state, 0.5, 0.5, now())
            RETURNING id
            """,
            workflow_id,
            iter_idx,
        )
        for case_id, expected, predicted in zip(
            case_ids,
            [c["expected_value"] for c in cases],
            predictions,
            strict=True,
        ):
            await db.execute(
                """
                INSERT INTO iteration_case_outputs
                    (iteration_id, eval_case_id, output_json, passed)
                VALUES ($1, $2, $3::jsonb, $4)
                """,
                iter_id,
                case_id,
                __import__("json").dumps({target_label_field: predicted}),
                expected == predicted,
            )


async def _seed_metric_proposal(
    db: asyncpg.Connection,
    *,
    workflow_id: str,
    iteration_index: int = 0,
    payload: dict | None = None,
):
    iter_id = await db.fetchval(
        "SELECT id FROM iterations WHERE workflow_id = $1 "
        "AND iteration_index = $2",
        workflow_id,
        iteration_index,
    )
    payload = payload or {"name": "recall", "family": "recall"}
    proposal_id = await db.fetchval(
        """
        INSERT INTO proposals (
            iteration_id, skill_id, parent_version_id,
            proposed_content, proposed_payload, plain_language_summary,
            kind, state
        )
        VALUES ($1, NULL, NULL, '', $2::jsonb, 'Switch metric',
                'metric'::proposal_kind, 'pending'::proposal_state)
        RETURNING id
        """,
        iter_id,
        __import__("json").dumps(payload),
    )
    return proposal_id


async def test_inversion_check_404_on_unknown_proposal(
    api_client: httpx.AsyncClient,
):
    res = await api_client.get(
        f"/api/proposals/{uuid4()}/ordering-inversion-check"
    )
    assert res.status_code == 404


async def test_inversion_check_422_on_skill_proposal(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    seeded = await _seed_proposal(db)
    res = await api_client.get(
        f"/api/proposals/{seeded['proposal_id']}/ordering-inversion-check"
    )
    assert res.status_code == 422


async def test_inversion_check_unavailable_without_metric_definition(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """A workflow with no metric_definition can't anchor the check —
    status='unavailable'."""
    await db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES "
        "($1, 'no-metric', '{}'::jsonb) ON CONFLICT DO NOTHING",
        "wf-no-metric",
    )
    iter_id = await db.fetchval(
        "INSERT INTO iterations (workflow_id, iteration_index, state, "
        "val_score, best_ever_score_after, ended_at) VALUES "
        "($1, 0, 'gate-pass'::iteration_state, 0.5, 0.5, now()) RETURNING id",
        "wf-no-metric",
    )
    proposal_id = await db.fetchval(
        """
        INSERT INTO proposals (iteration_id, skill_id, parent_version_id,
            proposed_content, proposed_payload, plain_language_summary,
            kind, state)
        VALUES ($1, NULL, NULL, '', '{"name":"x"}'::jsonb, 's',
                'metric'::proposal_kind, 'pending'::proposal_state)
        RETURNING id
        """,
        iter_id,
    )
    res = await api_client.get(
        f"/api/proposals/{proposal_id}/ordering-inversion-check"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "unavailable"
    assert "metric definition" in (body["reason"] or "").lower()


async def test_inversion_check_ok_per_iteration_deltas(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """One iteration where the agent over-predicts True (good recall,
    bad precision). Switching from pass_rate to recall should bump
    new_score above old_score for that iteration; no inversion if
    target_value is permissive."""
    await _seed_metric_workflow_with_outputs(
        db,
        workflow_id="wf-inv-ok",
        iterations=[
            # expected: T, T, F, F. predictions: T, T, T, T.
            # pass_rate = 2/4 = 0.5; recall = 2/2 = 1.0.
            [True, True, True, True],
        ],
    )
    proposal_id = await _seed_metric_proposal(
        db,
        workflow_id="wf-inv-ok",
        payload={"name": "recall", "family": "recall"},
    )
    res = await api_client.get(
        f"/api/proposals/{proposal_id}/ordering-inversion-check"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok", body.get("reason")
    assert body["current_metric_family"] == "pass_rate"
    assert body["proposed_metric_family"] == "recall"
    assert len(body["iterations"]) == 1
    it = body["iterations"][0]
    assert it["old_score"] == 0.5
    assert it["new_score"] == 1.0
    assert it["delta"] == 0.5
    assert it["n_cases"] == 4


async def test_inversion_check_flags_gate_verdict_flip(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """Two iterations: iter 0 passes pass_rate (target 0.75 met) but
    fails recall; the check flags an inversion."""
    await _seed_metric_workflow_with_outputs(
        db,
        workflow_id="wf-inv-flip",
        iterations=[
            # expected: T, T, F, F. predictions: F, F, F, F.
            # pass_rate = 2/4 = 0.5 (< 0.75 — fails); recall = 0/2 = 0 (fails too).
            # Pick predictions where the gate verdict actually flips:
            # F, T, F, F → expected match: TN,FN(F),TN,TN. Hmm.
            # Easier: predictions all match expected for iter 0 → pass_rate=1.0
            # (passes target), recall = 2/2 = 1.0 (passes too) — no flip.
            # Construct a real flip: expected T,T,F,F predictions T,F,F,F.
            # pass_rate = 3/4 = 0.75 (meets target 0.75 with maximize);
            # recall = 1/2 = 0.5 (with target 0.75 -> fails).
            [True, False, False, False],
        ],
    )
    proposal_id = await _seed_metric_proposal(
        db,
        workflow_id="wf-inv-flip",
        payload={"name": "recall", "family": "recall"},
    )
    res = await api_client.get(
        f"/api/proposals/{proposal_id}/ordering-inversion-check"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["n_inverted"] >= 1
    it = body["iterations"][0]
    assert it["inverted"] is True
    assert it["old_meets_target"] is True
    assert it["new_meets_target"] is False


# ---------------------------------------------------------------------------
# 9.2.3 — non-skill approve applies the change + → deployed
# ---------------------------------------------------------------------------


async def _create_metric_proposal_via_api(
    api_client: httpx.AsyncClient,
    *,
    workflow_id: str,
    payload: dict,
):
    res = await api_client.post(
        f"/api/workflows/{workflow_id}/proposals/metric",
        json={
            "plain_language_summary": "Switch metric",
            "proposed_metric": payload,
            "rationale": None,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_approve_description_applies_change_and_deploys(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    await db.execute(
        "INSERT INTO workflows (id, description, spec) VALUES "
        "('wf-app-desc', 'Original description text.', '{}'::jsonb) "
        "ON CONFLICT DO NOTHING"
    )
    await db.execute(
        "INSERT INTO iterations (workflow_id, iteration_index, state, "
        "val_score, best_ever_score_after, ended_at) VALUES "
        "('wf-app-desc', 0, 'gate-pass'::iteration_state, 0.5, 0.5, now())"
    )
    res = await api_client.post(
        "/api/workflows/wf-app-desc/proposals/description",
        json={
            "plain_language_summary": "Add past misses.",
            "proposed_description": (
                "Predict demand for the upcoming planning week. Past misses: "
                "2024 holiday markdowns."
            ),
        },
    )
    assert res.status_code == 201, res.text
    proposal_id = res.json()["id"]

    decide = await api_client.post(
        f"/api/proposals/{proposal_id}/approve",
        json={"decided_by": "human:reviewer"},
    )
    assert decide.status_code == 200, decide.text

    # Approve stages the change — state advances but the workflow
    # row is untouched until Deploy fires.
    staged = await api_client.get(f"/api/proposals/{proposal_id}")
    assert staged.json()["state"] == "approved-awaiting-deploy"
    pre_desc = await db.fetchval(
        "SELECT description FROM workflows WHERE id = 'wf-app-desc'"
    )
    assert pre_desc == "Original description text."

    # Deploy applies the staged change.
    deploy = await api_client.post(
        f"/api/proposals/{proposal_id}/deploy",
        json={"decided_by": "human:reviewer"},
    )
    assert deploy.status_code == 200, deploy.text
    final = await api_client.get(f"/api/proposals/{proposal_id}")
    assert final.json()["state"] == "deployed"

    desc = await db.fetchval(
        "SELECT description FROM workflows WHERE id = 'wf-app-desc'"
    )
    assert desc == (
        "Predict demand for the upcoming planning week. Past misses: "
        "2024 holiday markdowns."
    )


async def test_approve_metric_writes_inflated_definition(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    # Seed workflow with a current metric_definition so the apply path
    # has something to inherit defaults from.
    import json as _json
    current_md = {
        "schema_version": "0.1",
        "workflow_spec_id": "wf-app-metric",
        "name": "pass-rate",
        "family": "pass_rate",
        "direction": "maximize",
        "lower_bound": 0.0,
        "upper_bound": 1.0,
        "target_value": 0.75,
        "description": "Current.",
        "rationale": "seed.",
        "provenance": {"kind": "derived", "source": "test"},
    }
    await db.execute(
        "INSERT INTO workflows (id, description, spec, metric_definition) "
        "VALUES ('wf-app-metric', 'demo', '{}'::jsonb, $1::jsonb)",
        _json.dumps(current_md),
    )
    await db.execute(
        "INSERT INTO iterations (workflow_id, iteration_index, state, "
        "val_score, best_ever_score_after, ended_at) VALUES "
        "('wf-app-metric', 0, 'gate-pass'::iteration_state, 0.5, 0.5, now())"
    )

    proposal = await _create_metric_proposal_via_api(
        api_client,
        workflow_id="wf-app-metric",
        payload={"name": "recall", "family": "recall", "direction": "higher"},
    )

    # Step 1: approve → stage. metric_definition stays at the seed.
    decide = await api_client.post(
        f"/api/proposals/{proposal['id']}/approve",
        json={"decided_by": "human:reviewer"},
    )
    assert decide.status_code == 200
    pre = await db.fetchval(
        "SELECT metric_definition FROM workflows WHERE id = 'wf-app-metric'"
    )
    pre_md = _json.loads(pre) if isinstance(pre, str) else pre
    assert pre_md["name"] == "pass-rate"
    assert pre_md["family"] == "pass_rate"

    # Step 2: deploy → apply. New metric is now live.
    deploy = await api_client.post(
        f"/api/proposals/{proposal['id']}/deploy",
        json={"decided_by": "human:reviewer"},
    )
    assert deploy.status_code == 200, deploy.text

    raw = await db.fetchval(
        "SELECT metric_definition FROM workflows WHERE id = 'wf-app-metric'"
    )
    md = _json.loads(raw) if isinstance(raw, str) else raw
    assert md["name"] == "recall"
    assert md["family"] == "recall"
    assert md["direction"] == "maximize"  # 'higher' → 'maximize'
    # Inherited from current; not overwritten.
    assert md["target_value"] == 0.75
    assert md["lower_bound"] == 0.0
    assert md["upper_bound"] == 1.0


async def test_approve_ui_primitive_merges_into_spec_ui(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    import json as _json
    spec = {
        "ui": {
            "tabs": [
                {"primitives": [{"type": "HeadlineMetrics"}]},
            ]
        }
    }
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ('wf-app-uip', 'demo', $1::jsonb)",
        _json.dumps(spec),
    )
    await db.execute(
        "INSERT INTO iterations (workflow_id, iteration_index, state, "
        "val_score, best_ever_score_after, ended_at) VALUES "
        "('wf-app-uip', 0, 'gate-pass'::iteration_state, 0.5, 0.5, now())"
    )
    res = await api_client.post(
        "/api/workflows/wf-app-uip/proposals/ui-primitive",
        json={
            "plain_language_summary": "Add AlertList.",
            "proposed_primitives": [
                {"type": "HeadlineMetrics"},
                {"type": "AlertList"},
            ],
        },
    )
    proposal_id = res.json()["id"]
    # Approve stages — primitives list stays at the seed.
    decide = await api_client.post(
        f"/api/proposals/{proposal_id}/approve",
        json={"decided_by": "human:reviewer"},
    )
    assert decide.status_code == 200
    pre = await db.fetchval(
        "SELECT spec FROM workflows WHERE id = 'wf-app-uip'"
    )
    pre_parsed = _json.loads(pre) if isinstance(pre, str) else pre
    assert [p["type"] for p in pre_parsed["ui"]["tabs"][0]["primitives"]] == [
        "HeadlineMetrics",
    ]
    # Deploy applies.
    deploy = await api_client.post(
        f"/api/proposals/{proposal_id}/deploy",
        json={"decided_by": "human:reviewer"},
    )
    assert deploy.status_code == 200, deploy.text

    raw_spec = await db.fetchval(
        "SELECT spec FROM workflows WHERE id = 'wf-app-uip'"
    )
    parsed = _json.loads(raw_spec) if isinstance(raw_spec, str) else raw_spec
    types = [p["type"] for p in parsed["ui"]["tabs"][0]["primitives"]]
    assert types == ["HeadlineMetrics", "AlertList"]


async def test_approve_sim_merges_sections_into_spec(
    db: asyncpg.Connection, api_client: httpx.AsyncClient,
):
    import json as _json
    spec = {
        "tools": [{"name": "lookup_velocity"}],
        "environment": {
            "personas": [{"role": "planner"}],
            "env_generators": [],
            "data_sources": [],
        },
    }
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ('wf-app-sim', 'demo', $1::jsonb)",
        _json.dumps(spec),
    )
    await db.execute(
        "INSERT INTO iterations (workflow_id, iteration_index, state, "
        "val_score, best_ever_score_after, ended_at) VALUES "
        "('wf-app-sim', 0, 'gate-pass'::iteration_state, 0.5, 0.5, now())"
    )
    res = await api_client.post(
        "/api/workflows/wf-app-sim/proposals/sim",
        json={
            "plain_language_summary": "Add seasonal index tool.",
            "proposed_spec": {
                "tools": [
                    {"name": "lookup_velocity"},
                    {"name": "lookup_seasonal_index"},
                ],
            },
        },
    )
    proposal_id = res.json()["id"]
    # Approve stages — tools list stays at the seed.
    decide = await api_client.post(
        f"/api/proposals/{proposal_id}/approve",
        json={"decided_by": "human:reviewer"},
    )
    assert decide.status_code == 200
    pre = await db.fetchval(
        "SELECT spec FROM workflows WHERE id = 'wf-app-sim'"
    )
    pre_parsed = _json.loads(pre) if isinstance(pre, str) else pre
    assert [t["name"] for t in pre_parsed["tools"]] == ["lookup_velocity"]
    # Deploy applies.
    deploy = await api_client.post(
        f"/api/proposals/{proposal_id}/deploy",
        json={"decided_by": "human:reviewer"},
    )
    assert deploy.status_code == 200, deploy.text

    raw_spec = await db.fetchval(
        "SELECT spec FROM workflows WHERE id = 'wf-app-sim'"
    )
    parsed = _json.loads(raw_spec) if isinstance(raw_spec, str) else raw_spec
    tools = [t["name"] for t in parsed["tools"]]
    assert tools == ["lookup_velocity", "lookup_seasonal_index"]
    # Untouched sections survive.
    personas = [p["role"] for p in parsed["environment"]["personas"]]
    assert personas == ["planner"]
