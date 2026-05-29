"""`GET /api/jobs` — read-only job-queue list for the request's workspace.

DB-gated. Jobs are seeded directly on the shared per-test DB; the in-process
`api_client` resolves to the seeded dev principal (the `default` workspace), so
it reads back exactly what was seeded — the same seed-via-`db` / read-via-client
split the trace API tests use.
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


async def _seed_job(
    db: asyncpg.Connection,
    *,
    workflow_id: str,
    status: str = "queued",
    age_seconds: int = 0,
    last_error: str | None = None,
) -> str:
    """Insert one job, `age_seconds` in the past so created_at ordering is
    deterministic (newer = smaller age)."""
    return await db.fetchval(
        """
        INSERT INTO jobs (kind, payload, status, last_error, created_at)
        VALUES ('run_iteration', $1::jsonb, $2::job_status, $3,
                now() - make_interval(secs => $4))
        RETURNING id
        """,
        json.dumps({"workflow_id": workflow_id}),
        status,
        last_error,
        age_seconds,
    )


async def test_jobs_empty(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    res = await api_client.get("/api/jobs")
    assert res.status_code == 200
    assert res.json() == {"items": [], "counts": {}}


async def test_jobs_lists_newest_first_with_counts(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    await _seed_job(db, workflow_id="wf-old", status="queued", age_seconds=30)
    await _seed_job(db, workflow_id="wf-mid", status="failed", age_seconds=20,
                    last_error="boom")
    await _seed_job(db, workflow_id="wf-new", status="queued", age_seconds=10)

    res = await api_client.get("/api/jobs")
    assert res.status_code == 200
    body = res.json()

    # Newest first.
    assert [i["workflow_id"] for i in body["items"]] == ["wf-new", "wf-mid", "wf-old"]
    # Workspace-wide depth counts, independent of the returned page.
    assert body["counts"] == {"queued": 2, "failed": 1}
    mid = next(i for i in body["items"] if i["workflow_id"] == "wf-mid")
    assert mid["status"] == "failed"
    assert mid["last_error"] == "boom"
    assert mid["kind"] == "run_iteration"


async def test_jobs_status_filter(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    await _seed_job(db, workflow_id="wf-q", status="queued")
    await _seed_job(db, workflow_id="wf-f", status="failed")

    res = await api_client.get("/api/jobs", params={"status": "failed"})
    assert res.status_code == 200
    body = res.json()
    assert [i["workflow_id"] for i in body["items"]] == ["wf-f"]
    # counts are workspace-wide, not filtered.
    assert body["counts"] == {"queued": 1, "failed": 1}


async def test_jobs_invalid_status_is_422(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    res = await api_client.get("/api/jobs", params={"status": "bogus"})
    assert res.status_code == 422


async def test_jobs_limit_out_of_range_is_422(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    assert (await api_client.get("/api/jobs", params={"limit": 0})).status_code == 422
    assert (await api_client.get("/api/jobs", params={"limit": 999})).status_code == 422


async def test_jobs_limit_caps_page_not_counts(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    for i in range(3):
        await _seed_job(db, workflow_id=f"wf-{i}", status="queued", age_seconds=i)

    res = await api_client.get("/api/jobs", params={"limit": 2})
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 2  # page bounded
    assert body["counts"] == {"queued": 3}  # full depth still reported


async def test_jobs_no_credentials_is_401(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With dev-auth disabled and no assertion, GET /api/jobs returns 401."""
    monkeypatch.delenv("OWNEVO_DEV_AUTH", raising=False)
    res = await api_client.get("/api/jobs")
    assert res.status_code == 401


async def test_jobs_workspace_isolation(
    api_client: httpx.AsyncClient,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Jobs seeded in a second workspace are invisible to the default workspace's client.

    Exercises the full HTTP path through ConnDep / RLS — distinct from the
    DB-layer isolation tested by test_jobs_metrics.py under an RLS role.
    """
    # Create a second workspace and seed a job into it using SET LOCAL so the
    # RLS policy stamps workspace_id = 'ws-isolated' on the inserted row.
    await db.execute(
        "INSERT INTO workspaces (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        "ws-isolated",
        "Isolated WS",
    )
    async with db.transaction():
        await db.execute("SET LOCAL app.workspace_id = 'ws-isolated'")
        await db.execute(
            """
            INSERT INTO jobs (kind, payload, status)
            VALUES ('run_iteration', '{"workflow_id":"wf-secret"}'::jsonb, 'queued')
            """
        )

    # The api_client is authenticated to the default workspace.
    res = await api_client.get("/api/jobs")
    assert res.status_code == 200
    body = res.json()
    wf_ids = [item["workflow_id"] for item in body["items"]]
    assert "wf-secret" not in wf_ids
