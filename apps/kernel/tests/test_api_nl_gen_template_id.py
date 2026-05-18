"""Tests for `template_id` handling on POST /api/nl-gen/generate and
GET /api/workflows/{id}.

Validation tests (no DB): mount the nl-gen router on a bare FastAPI instance
and verify the 400 paths without needing a Postgres connection.

Round-trip test (DB required): seeds a workflow row directly with
`created_from_template` set, then calls GET /api/workflows/{id} to confirm
the field is returned in WorkflowAnatomy.
"""

from __future__ import annotations

import os

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from ownevo_kernel.api.routes.nl_gen import router as nl_gen_router
from ownevo_kernel.db import ENV_VAR

# ---------------------------------------------------------------------------
# Stateless validation tests (no DB)
# ---------------------------------------------------------------------------


@pytest.fixture
async def nl_gen_client():
    app = FastAPI()
    app.include_router(nl_gen_router)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c


async def test_generate_400_on_invalid_template_id_format(
    nl_gen_client: httpx.AsyncClient,
):
    resp = await nl_gen_client.post(
        "/api/nl-gen/generate",
        json={
            "description": "x" * 60,
            "template_id": "INVALID_SLUG",
        },
    )
    assert resp.status_code == 400
    assert "template_id" in resp.json()["detail"]
    assert "kebab slug" in resp.json()["detail"]


async def test_generate_400_on_unrecognised_template_id(
    nl_gen_client: httpx.AsyncClient,
):
    resp = await nl_gen_client.post(
        "/api/nl-gen/generate",
        json={
            "description": "x" * 60,
            "template_id": "unknown-template-slug",
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "template_id" in detail
    assert "recognised" in detail


async def test_generate_none_template_id_is_accepted(
    nl_gen_client: httpx.AsyncClient,
):
    # A missing ANTHROPIC_API_KEY returns 503, not 400 — confirming validation passed.
    resp = await nl_gen_client.post(
        "/api/nl-gen/generate",
        json={"description": "x" * 60},
    )
    assert resp.status_code != 400


async def test_generate_valid_template_id_passes_validation(
    nl_gen_client: httpx.AsyncClient,
):
    resp = await nl_gen_client.post(
        "/api/nl-gen/generate",
        json={
            "description": "x" * 60,
            "template_id": "retail-demand-planning",
        },
    )
    # 503 = key not set; validation passed.
    assert resp.status_code != 400


# ---------------------------------------------------------------------------
# Round-trip test: created_from_template persisted and returned (DB required)
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping DB integration tests",
)


@pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping DB integration tests",
)
async def test_get_workflow_returns_created_from_template(
    db: asyncpg.Connection,
    api_client: httpx.AsyncClient,
):
    wf_id = "tpl-roundtrip-test"
    await db.execute(
        """
        INSERT INTO workflows (id, description, spec, created_from_template)
        VALUES ($1, $2, '{}'::jsonb, $3)
        ON CONFLICT DO NOTHING
        """,
        wf_id,
        "Credit risk recalibration workflow for testing.",
        "credit-risk-recalibration",
    )

    resp = await api_client.get(f"/api/workflows/{wf_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["created_from_template"] == "credit-risk-recalibration"
