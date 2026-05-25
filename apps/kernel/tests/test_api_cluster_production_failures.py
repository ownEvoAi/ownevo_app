"""Route tests for POST /api/workflows/{id}/cluster-production-failures.

The heavy pipeline (sentence-transformers / UMAP / HDBSCAN / Anthropic)
is mocked at the `cluster_production_failures` boundary — these tests
assert the route's wiring (404, response shape, demo-mode block), not
the clustering math (covered in test_clustering_from_traces.py).
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import httpx
import numpy as np
import pytest
from ownevo_kernel.clustering import from_traces
from ownevo_kernel.clustering.types import ClusterSummary, PersistedCluster
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping route tests",
)


async def _seed_workflow(db: asyncpg.Connection, wf_id: str) -> None:
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, 'route test', '{}'::jsonb) ON CONFLICT DO NOTHING",
        wf_id,
    )


def _fake_persisted(n_members: int) -> PersistedCluster:
    return PersistedCluster(
        id=uuid.uuid4(),
        summary=ClusterSummary(
            label="prod-cluster-0",
            severity="medium",
            member_indices=tuple(range(n_members)),
            centroid=np.zeros(384, dtype=np.float32),
            quality_score=0.7,
            sample_signatures=("Timeout | tool=forecast | x",),
        ),
    )


async def test_route_404_for_unknown_workflow(
    api_client: httpx.AsyncClient,
) -> None:
    resp = await api_client.post(
        "/api/workflows/nope/cluster-production-failures"
    )
    assert resp.status_code == 404


async def test_route_returns_cluster_summary(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_workflow(db, "wf-route")

    async def fake_cluster(conn, workflow_id, **kwargs):  # noqa: ANN001, ANN003
        assert workflow_id == "wf-route"
        return [_fake_persisted(5)]

    monkeypatch.setattr(from_traces, "cluster_production_failures", fake_cluster)

    resp = await api_client.post(
        "/api/workflows/wf-route/cluster-production-failures"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workflow_id"] == "wf-route"
    assert body["clusters_created"] == 1
    assert body["clustered_failures"] == 5
    assert len(body["cluster_ids"]) == 1


async def test_route_empty_when_no_failures(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_workflow(db, "wf-empty")

    async def fake_cluster(conn, workflow_id, **kwargs):  # noqa: ANN001, ANN003
        return []

    monkeypatch.setattr(from_traces, "cluster_production_failures", fake_cluster)

    resp = await api_client.post(
        "/api/workflows/wf-empty/cluster-production-failures"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clusters_created"] == 0
    assert body["clustered_failures"] == 0
    assert body["cluster_ids"] == []
