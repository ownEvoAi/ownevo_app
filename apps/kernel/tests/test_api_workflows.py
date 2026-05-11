"""Integration tests for the W7 slice 2 `/api/workflows` surface.

Same in-process httpx + ASGITransport pattern as `test_api_proposals.py`.
Tests skip when `OWNEVO_DATABASE_URL` is unset so unit-only CI stays
green. The `api_client` fixture is shared via `conftest.py`.
"""

from __future__ import annotations

import json
import os

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


async def _seed_workflow(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    description: str = "",
    mode: str = "gated",
) -> None:
    await conn.execute(
        "INSERT INTO workflows (id, description, spec, mode) "
        "VALUES ($1, $2, '{}'::jsonb, $3::workflow_mode) "
        "ON CONFLICT DO NOTHING",
        workflow_id,
        description or f"Workflow {workflow_id}",
        mode,
    )


async def _seed_iteration(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    iteration_index: int,
    state: str = "gate-pass",
    val_score: float | None = 0.42,
    best_ever_score_after: float | None = 0.42,
    cluster_id=None,
):
    return await conn.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state,
                                val_score, best_ever_score_after, ended_at,
                                cluster_id)
        VALUES ($1, $2, $3::iteration_state, $4, $5, now(), $6)
        RETURNING id
        """,
        workflow_id,
        iteration_index,
        state,
        val_score,
        best_ever_score_after,
        cluster_id,
    )


async def _seed_proposal(
    conn: asyncpg.Connection,
    *,
    iteration_id,
    skill_id: str = "skill.test",
    state: str = "gate-passed",
):
    await conn.execute(
        "INSERT INTO skills (id, kind) VALUES ($1, 'python'::skill_kind) "
        "ON CONFLICT DO NOTHING",
        skill_id,
    )
    return await conn.fetchval(
        """
        INSERT INTO proposals (
            iteration_id, skill_id, proposed_content, plain_language_summary, state
        )
        VALUES ($1, $2, 'def foo(): pass', 'Add detector', $3::proposal_state)
        RETURNING id
        """,
        iteration_id,
        skill_id,
        state,
    )


# ---------------------------------------------------------------------------
# GET /api/workflows
# ---------------------------------------------------------------------------


async def test_list_workflows_empty(api_client: httpx.AsyncClient):
    res = await api_client.get("/api/workflows")
    assert res.status_code == 200
    body = res.json()
    assert body == {"items": [], "total": 0}


async def test_list_workflows_summary(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-a", description="Alpha")
    await _seed_workflow(db, workflow_id="wf-b", description="Beta")
    # wf-a: 2 iterations, last one improved + has approved proposal
    await _seed_iteration(
        db, workflow_id="wf-a", iteration_index=0, val_score=0.30,
        best_ever_score_after=0.30,
    )
    iter_a2 = await _seed_iteration(
        db, workflow_id="wf-a", iteration_index=1, val_score=0.45,
        best_ever_score_after=0.45,
    )
    await _seed_proposal(
        db, iteration_id=iter_a2, skill_id="skill.a",
        state="approved-awaiting-deploy",
    )
    # wf-b: 1 iteration, gate-passed proposal pending
    iter_b1 = await _seed_iteration(
        db, workflow_id="wf-b", iteration_index=0, val_score=0.10,
        best_ever_score_after=0.10,
    )
    await _seed_proposal(
        db, iteration_id=iter_b1, skill_id="skill.b",
        state="gate-passed",
    )

    res = await api_client.get("/api/workflows")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2

    by_id = {w["id"]: w for w in body["items"]}

    assert by_id["wf-a"]["description"] == "Alpha"
    assert by_id["wf-a"]["mode"] == "gated"
    assert by_id["wf-a"]["iteration_count"] == 2
    assert by_id["wf-a"]["best_ever_score"] == 0.45
    assert by_id["wf-a"]["pending_proposals_count"] == 0
    assert by_id["wf-a"]["last_improved_at"] is not None

    assert by_id["wf-b"]["iteration_count"] == 1
    assert by_id["wf-b"]["pending_proposals_count"] == 1
    assert by_id["wf-b"]["last_improved_at"] is None


async def test_list_workflows_excludes_running_iterations_from_count(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-running")
    await _seed_iteration(
        db, workflow_id="wf-running", iteration_index=0, state="gate-pass",
        val_score=0.5, best_ever_score_after=0.5,
    )
    await _seed_iteration(
        db, workflow_id="wf-running", iteration_index=1, state="running",
        val_score=None, best_ever_score_after=None,
    )

    res = await api_client.get("/api/workflows")
    body = res.json()
    by_id = {w["id"]: w for w in body["items"]}
    assert by_id["wf-running"]["iteration_count"] == 1


async def test_list_workflows_deployed_state_drives_last_improved_at(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """Both 'approved-awaiting-deploy' and 'deployed' must contribute
    to last_improved_at — they are the two states in _APPROVED_STATES."""
    await _seed_workflow(db, workflow_id="wf-deployed")
    iter_id = await _seed_iteration(
        db, workflow_id="wf-deployed", iteration_index=0,
        val_score=0.5, best_ever_score_after=0.5,
    )
    await _seed_proposal(
        db, iteration_id=iter_id, skill_id="skill.deployed",
        state="deployed",
    )

    res = await api_client.get("/api/workflows")
    body = res.json()
    by_id = {w["id"]: w for w in body["items"]}
    assert by_id["wf-deployed"]["last_improved_at"] is not None


async def test_list_workflows_sort_order_created_at_asc(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """LiftChart hero picks items[0] — sort order is load-bearing."""
    # Seed in non-alphabetical order to verify the SQL ORDER BY drives
    # the response, not the insertion order.
    await _seed_workflow(db, workflow_id="wf-c", description="Charlie")
    await _seed_workflow(db, workflow_id="wf-a", description="Alpha")
    await _seed_workflow(db, workflow_id="wf-b", description="Bravo")

    res = await api_client.get("/api/workflows")
    body = res.json()
    ids = [w["id"] for w in body["items"]]
    # created_at ASC — wf-c was inserted first, so it should land first.
    assert ids[0] == "wf-c"


# ---------------------------------------------------------------------------
# GET /api/workflows/{id}/iterations
# ---------------------------------------------------------------------------


async def test_iterations_404_on_unknown_workflow(api_client: httpx.AsyncClient):
    res = await api_client.get("/api/workflows/nope/iterations")
    assert res.status_code == 404


async def test_iterations_chronological_with_annotations(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-lift")
    iter_0 = await _seed_iteration(
        db, workflow_id="wf-lift", iteration_index=0,
        state="gate-pass", val_score=0.30, best_ever_score_after=0.30,
    )
    await _seed_iteration(
        db, workflow_id="wf-lift", iteration_index=1,
        state="gate-blocked-no-improvement",
        val_score=0.28, best_ever_score_after=0.30,
    )
    iter_2 = await _seed_iteration(
        db, workflow_id="wf-lift", iteration_index=2,
        state="gate-pass", val_score=0.45, best_ever_score_after=0.45,
    )
    # iter 0 has approved proposal, iter 2 has gate-passed-but-not-yet-approved
    await _seed_proposal(
        db, iteration_id=iter_0, skill_id="skill.lift",
        state="approved-awaiting-deploy",
    )
    await _seed_proposal(
        db, iteration_id=iter_2, skill_id="skill.lift",
        state="gate-passed",
    )

    res = await api_client.get("/api/workflows/wf-lift/iterations")
    assert res.status_code == 200
    body = res.json()
    assert body["workflow_id"] == "wf-lift"
    assert len(body["items"]) == 3

    # Order is iteration_index ASC
    indices = [p["iteration_index"] for p in body["items"]]
    assert indices == [0, 1, 2]

    pt0, pt1, pt2 = body["items"]
    assert pt0["val_score"] == 0.30
    assert pt0["state"] == "gate-pass"
    assert pt0["has_approved_proposal"] is True
    assert pt1["val_score"] == 0.28
    assert pt1["state"] == "gate-blocked-no-improvement"
    assert pt1["has_approved_proposal"] is False
    assert pt2["val_score"] == 0.45
    assert pt2["has_approved_proposal"] is False


async def test_iterations_excludes_running(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-mixed")
    await _seed_iteration(
        db, workflow_id="wf-mixed", iteration_index=0,
        state="gate-pass", val_score=0.5, best_ever_score_after=0.5,
    )
    await _seed_iteration(
        db, workflow_id="wf-mixed", iteration_index=1, state="running",
        val_score=None, best_ever_score_after=None,
    )

    res = await api_client.get("/api/workflows/wf-mixed/iterations")
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["iteration_index"] == 0


# ---------------------------------------------------------------------------
# GET /api/workflows/{id}/failure_clusters
# ---------------------------------------------------------------------------


async def _seed_cluster(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    label: str,
    severity: str = "medium",
    cluster_size: int = 5,
):
    return await conn.fetchval(
        """
        INSERT INTO failure_clusters (
            workflow_id, label, severity, cluster_size
        )
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        workflow_id,
        label,
        severity,
        cluster_size,
    )


async def test_failure_clusters_404_on_unknown(api_client: httpx.AsyncClient):
    res = await api_client.get("/api/workflows/nope/failure_clusters")
    assert res.status_code == 404


async def test_failure_clusters_severity_then_size(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-clusters")
    await _seed_cluster(
        db, workflow_id="wf-clusters", label="low-3", severity="low", cluster_size=3,
    )
    await _seed_cluster(
        db, workflow_id="wf-clusters", label="high-7", severity="high", cluster_size=7,
    )
    await _seed_cluster(
        db, workflow_id="wf-clusters", label="med-12", severity="medium", cluster_size=12,
    )
    await _seed_cluster(
        db, workflow_id="wf-clusters", label="high-3", severity="high", cluster_size=3,
    )

    res = await api_client.get("/api/workflows/wf-clusters/failure_clusters")
    assert res.status_code == 200
    body = res.json()
    assert body["workflow_id"] == "wf-clusters"

    labels = [c["label"] for c in body["items"]]
    # high-7 (high, size 7), high-3 (high, size 3), med-12 (medium), low-3 (low)
    assert labels == ["high-7", "high-3", "med-12", "low-3"]


async def test_failure_clusters_empty(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-no-clusters")
    res = await api_client.get("/api/workflows/wf-no-clusters/failure_clusters")
    assert res.status_code == 200
    body = res.json()
    assert body == {"workflow_id": "wf-no-clusters", "items": []}


# W7 slice 7 (7.1.4) — latest_proposal_id surface


async def test_failure_clusters_latest_proposal_null_without_proposal(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """Bare cluster with no iteration → latest_proposal_id is null."""
    await _seed_workflow(db, workflow_id="wf-bare")
    await _seed_cluster(
        db, workflow_id="wf-bare", label="bare", severity="medium", cluster_size=1,
    )
    res = await api_client.get("/api/workflows/wf-bare/failure_clusters")
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["latest_proposal_id"] is None


async def test_get_workflow_anatomy_404_on_unknown(api_client: httpx.AsyncClient):
    """W7 slice 11 (7.1.12) — anatomy endpoint 404s on unknown id."""
    res = await api_client.get("/api/workflows/nope")
    assert res.status_code == 404


async def test_get_workflow_anatomy_returns_spec(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """Spec round-trips through JSONB and surfaces in the response."""
    spec = {
        "schema_version": "1.1",
        "id": "wf-anatomy",
        "domain": "supply-chain",
        "tools": [
            {"name": "lookup_supplier", "description": "fetch profile",
             "inputs": [], "outputs": []},
        ],
        "reviewer": {"role": "Supply Chain VP", "cadence": "weekly"},
    }
    await db.execute(
        "INSERT INTO workflows (id, description, spec, mode) "
        "VALUES ($1, $2, $3::jsonb, 'gated'::workflow_mode)",
        "wf-anatomy",
        "Demand prediction workflow",
        json.dumps(spec),
    )

    res = await api_client.get("/api/workflows/wf-anatomy")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "wf-anatomy"
    assert body["description"] == "Demand prediction workflow"
    assert body["mode"] == "gated"
    assert body["spec"]["domain"] == "supply-chain"
    assert body["spec"]["tools"][0]["name"] == "lookup_supplier"
    assert body["spec"]["reviewer"]["role"] == "Supply Chain VP"


async def test_failure_clusters_latest_proposal_picks_newest(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """Two iterations on the same cluster → latest_proposal_id is the
    most-recently-created proposal's id (cluster → iteration via
    iterations.cluster_id → proposal via proposals.iteration_id).
    """
    await _seed_workflow(db, workflow_id="wf-latest")
    cluster_id = await _seed_cluster(
        db, workflow_id="wf-latest", label="cluster-a",
        severity="high", cluster_size=4,
    )
    iter_old = await _seed_iteration(
        db, workflow_id="wf-latest", iteration_index=0,
        cluster_id=cluster_id,
    )
    iter_new = await _seed_iteration(
        db, workflow_id="wf-latest", iteration_index=1,
        cluster_id=cluster_id,
    )
    prop_old = await _seed_proposal(
        db, iteration_id=iter_old, skill_id="skill.cluster.old",
    )
    prop_new = await _seed_proposal(
        db, iteration_id=iter_new, skill_id="skill.cluster.new",
    )

    res = await api_client.get("/api/workflows/wf-latest/failure_clusters")
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    latest = body["items"][0]["latest_proposal_id"]
    # `proposals.created_at` defaults to now() — prop_new was inserted
    # second so it wins. The id is rendered as a UUID string.
    assert latest == str(prop_new)
    assert latest != str(prop_old)
