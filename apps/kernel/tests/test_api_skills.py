"""Integration tests for the W7 slices 9 + 10 (7.1.10 + 7.1.11)
`/api/skills` + `/api/workflows/{id}/skills` surface.
"""

from __future__ import annotations

import json
import os
import uuid
from urllib.parse import urlparse, urlunparse

import asyncpg
import httpx
import pytest
from httpx import ASGITransport
from ownevo_kernel.api.app import create_app
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


@pytest.fixture
async def api_client(db: asyncpg.Connection):
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


async def _seed_workflow(conn: asyncpg.Connection, *, workflow_id: str) -> None:
    await conn.execute(
        "INSERT INTO workflows (id, description, spec, mode) "
        "VALUES ($1, 'desc', '{}'::jsonb, 'gated'::workflow_mode) "
        "ON CONFLICT DO NOTHING",
        workflow_id,
    )


async def _seed_skill(
    conn: asyncpg.Connection,
    *,
    skill_id: str,
    kind: str,
    workflow_id: str | None = None,
    capability_tags: list[str] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO skills (id, kind, workflow_id, capability_tags)
        VALUES ($1, $2::skill_kind, $3, $4)
        ON CONFLICT (id) DO NOTHING
        """,
        skill_id,
        kind,
        workflow_id,
        capability_tags or [],
    )


async def _seed_version(
    conn: asyncpg.Connection,
    *,
    skill_id: str,
    version_seq: int,
    content: str,
    parent_version_id: uuid.UUID | None = None,
    retention_block: dict | None = None,
    diff_summary: str | None = None,
    created_by: str = "agent:test",
) -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO skill_versions
            (skill_id, version_seq, content, parent_version_id,
             retention_block, diff_summary, created_by)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
        RETURNING id
        """,
        skill_id,
        version_seq,
        content,
        parent_version_id,
        json.dumps(retention_block) if retention_block is not None else None,
        diff_summary,
        created_by,
    )


async def _set_head(
    conn: asyncpg.Connection, *, skill_id: str, version_id: uuid.UUID,
) -> None:
    await conn.execute(
        "UPDATE skills SET head_version_id = $1 WHERE id = $2",
        version_id,
        skill_id,
    )


# ---------------------------------------------------------------------------
# GET /api/skills/{skill_id}
# ---------------------------------------------------------------------------


async def test_get_skill_404_on_unknown(api_client: httpx.AsyncClient):
    res = await api_client.get("/api/skills/nope")
    assert res.status_code == 404


async def test_get_instruction_skill_renders_retention_block(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-skill")
    await _seed_skill(
        db, skill_id="instr.demand", kind="instruction",
        workflow_id="wf-skill",
        capability_tags=["forecasting", "weekly"],
    )
    v1 = await _seed_version(
        db, skill_id="instr.demand", version_seq=1,
        content="# Skill\nbody",
        retention_block={
            "purpose": "predict",
            "inputs": ["sales_csv"],
            "do_not": ["read_arbitrary_files"],
        },
        diff_summary="initial",
        created_by="nl-gen",
    )
    await _set_head(db, skill_id="instr.demand", version_id=v1)

    res = await api_client.get("/api/skills/instr.demand")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "instr.demand"
    assert body["kind"] == "instruction"
    assert body["workflow_id"] == "wf-skill"
    assert body["head_version_seq"] == 1
    assert body["head_content"] == "# Skill\nbody"
    assert body["head_retention_block"]["purpose"] == "predict"
    assert body["head_retention_block"]["do_not"] == ["read_arbitrary_files"]
    assert body["capability_tags"] == ["forecasting", "weekly"]
    assert body["parent_content"] is None
    assert len(body["versions"]) == 1


async def test_get_python_skill_returns_parent_content_for_diff(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-py")
    await _seed_skill(db, skill_id="py.engineer", kind="python",
                      workflow_id="wf-py")
    v1 = await _seed_version(
        db, skill_id="py.engineer", version_seq=1,
        content="def feature_engineer(df):\n    return df\n",
        diff_summary="initial",
    )
    v2 = await _seed_version(
        db, skill_id="py.engineer", version_seq=2,
        content="def feature_engineer(df):\n    df['weekend'] = df.dt.weekday >= 5\n    return df\n",
        parent_version_id=v1,
        diff_summary="add weekend feature",
    )
    await _set_head(db, skill_id="py.engineer", version_id=v2)

    res = await api_client.get("/api/skills/py.engineer")
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "python"
    assert body["head_version_seq"] == 2
    assert body["parent_version_seq"] == 1
    assert "weekend" in body["head_content"]
    assert "weekend" not in body["parent_content"]
    # Version history is newest-first
    assert [v["version_seq"] for v in body["versions"]] == [2, 1]


# ---------------------------------------------------------------------------
# GET /api/workflows/{id}/skills
# ---------------------------------------------------------------------------


async def test_workflow_skills_404_on_unknown(api_client: httpx.AsyncClient):
    res = await api_client.get("/api/workflows/nope/skills")
    assert res.status_code == 404


async def test_workflow_skills_lists_kind_first_then_id(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    await _seed_workflow(db, workflow_id="wf-mix")
    await _seed_skill(
        db, skill_id="zz.last.python", kind="python", workflow_id="wf-mix",
    )
    await _seed_skill(
        db, skill_id="aa.first.python", kind="python", workflow_id="wf-mix",
    )
    await _seed_skill(
        db, skill_id="instr.alpha", kind="instruction", workflow_id="wf-mix",
    )

    res = await api_client.get("/api/workflows/wf-mix/skills")
    assert res.status_code == 200
    body = res.json()
    ids = [s["id"] for s in body["items"]]
    # instruction first, then python alphabetical
    assert ids == ["instr.alpha", "aa.first.python", "zz.last.python"]


async def test_list_skills_returns_workspace_wide_index(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """PLAN 8.0.4 — `GET /api/skills` returns every skill in the
    workspace across all workflows, sorted by kind then id."""
    await _seed_workflow(db, workflow_id="wf-a")
    await _seed_workflow(db, workflow_id="wf-b")
    await _seed_skill(db, skill_id="py.a", kind="python", workflow_id="wf-a")
    await _seed_skill(db, skill_id="py.b", kind="python", workflow_id="wf-b")
    await _seed_skill(
        db, skill_id="instr.shared", kind="instruction", workflow_id="wf-a",
    )

    res = await api_client.get("/api/skills")
    assert res.status_code == 200
    body = res.json()
    ids = [s["id"] for s in body["items"]]
    # instruction first, then python by id ASC. Other skills in the
    # DB from earlier seed_skill calls in the same session may sit
    # before py.a alphabetically — assert that the three skills we
    # just seeded appear in the expected relative order.
    assert "instr.shared" in ids
    assert "py.a" in ids
    assert "py.b" in ids
    assert ids.index("instr.shared") < ids.index("py.a")
    assert ids.index("py.a") < ids.index("py.b")


# ---------------------------------------------------------------------------
# GET /api/skills?workflow_id= — library filter
# ---------------------------------------------------------------------------


async def test_list_skills_workflow_filter_isolates_by_workflow(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """?workflow_id=X returns only skills whose workflow_id matches X."""
    await _seed_workflow(db, workflow_id="wf-filter-a")
    await _seed_workflow(db, workflow_id="wf-filter-b")
    await _seed_skill(db, skill_id="filter.a1", kind="python", workflow_id="wf-filter-a")
    await _seed_skill(db, skill_id="filter.a2", kind="instruction", workflow_id="wf-filter-a")
    await _seed_skill(db, skill_id="filter.b1", kind="python", workflow_id="wf-filter-b")

    res = await api_client.get("/api/skills?workflow_id=wf-filter-a")
    assert res.status_code == 200
    ids = {s["id"] for s in res.json()["items"]}
    assert "filter.a1" in ids
    assert "filter.a2" in ids
    assert "filter.b1" not in ids


async def test_list_skills_unscoped_filter_returns_only_null_workflow(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
):
    """?workflow_id=_unscoped returns skills with no workflow_id."""
    await _seed_workflow(db, workflow_id="wf-filter-c")
    await _seed_skill(db, skill_id="filter.scoped", kind="python", workflow_id="wf-filter-c")
    await _seed_skill(db, skill_id="filter.unscoped1", kind="python", workflow_id=None)
    await _seed_skill(db, skill_id="filter.unscoped2", kind="instruction", workflow_id=None)

    res = await api_client.get("/api/skills?workflow_id=_unscoped")
    assert res.status_code == 200
    ids = {s["id"] for s in res.json()["items"]}
    assert "filter.unscoped1" in ids
    assert "filter.unscoped2" in ids
    assert "filter.scoped" not in ids


async def test_list_skills_workflow_filter_empty_for_unknown_workflow(
    api_client: httpx.AsyncClient,
):
    """?workflow_id=does-not-exist returns an empty list, not a 404."""
    res = await api_client.get("/api/skills?workflow_id=does-not-exist-xyz")
    assert res.status_code == 200
    assert res.json()["items"] == []
