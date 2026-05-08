"""HTTP-level tests for the W5.5 `/api/nl-gen/preview` surface.

Unlike the proposals API, the preview endpoint is DB-free (returns
fixture data only). These tests run in CI without `OWNEVO_DATABASE_URL`
so the W5.5 contract is exercised on every test run, not just on the
machines with a Postgres around.

We bypass `create_app`'s lifespan (which insists on a pool) by mounting
just the nl_gen router on a fresh FastAPI instance.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from ownevo_kernel.api.routes.nl_gen import router
from ownevo_kernel.nl_gen.fixtures import FIXTURES


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c


async def test_preview_index_lists_every_fixture(client: httpx.AsyncClient):
    resp = await client.get("/api/nl-gen/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    ids = {entry["workflow_id"] for entry in body["items"]}
    assert ids == set(FIXTURES.keys())
    # Sorted-output contract — UI depends on stable ordering.
    returned_ids = [entry["workflow_id"] for entry in body["items"]]
    assert returned_ids == sorted(returned_ids)


async def test_preview_index_carries_descriptions(client: httpx.AsyncClient):
    resp = await client.get("/api/nl-gen/preview")
    body = resp.json()
    for entry in body["items"]:
        assert isinstance(entry["description"], str)
        assert len(entry["description"]) > 0


@pytest.mark.parametrize("workflow_id", sorted(FIXTURES))
async def test_preview_one_returns_full_bundle(
    client: httpx.AsyncClient,
    workflow_id: str,
):
    resp = await client.get(f"/api/nl-gen/preview/{workflow_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["workflow_id"] == workflow_id
    assert body["provenance"] == "preview-fixture"
    assert isinstance(body["description"], str)
    for key in (
        "workflow_spec",
        "simulation_plan",
        "eval_case_set",
        "metric_definition",
        "meta_eval_judgment",
    ):
        assert isinstance(body[key], dict)
        assert body[key]  # non-empty


async def test_preview_judgment_shape_matches_schema(
    client: httpx.AsyncClient,
):
    resp = await client.get("/api/nl-gen/preview/demand-prediction")
    judgment = resp.json()["meta_eval_judgment"]
    assert judgment["workflow_spec_id"] == "demand-prediction"
    assert judgment["overall_verdict"] in ("good", "bad")
    for dim in ("sim_coverage", "eval_case_coverage", "metric_alignment"):
        assert judgment[dim]["verdict"] in ("pass", "partial", "fail")
        assert isinstance(judgment[dim]["rationale"], str)
        assert len(judgment[dim]["rationale"]) > 0


async def test_preview_unknown_id_returns_404(client: httpx.AsyncClient):
    resp = await client.get("/api/nl-gen/preview/no-such-workflow")
    assert resp.status_code == 404
    assert "no-such-workflow" in resp.json()["detail"]
    # Available list surfaced so UI can recover.
    assert "available" in resp.json()["detail"]


async def test_preview_eval_case_fields_are_flat(client: httpx.AsyncClient):
    """expected_value and rationale are top-level fields on each case,
    not nested under expected_behavior. This guards against the
    expected_behavior wrapper mismatch (page.tsx was using c.expected_behavior)."""
    resp = await client.get("/api/nl-gen/preview/demand-prediction")
    cases = resp.json()["eval_case_set"]["cases"]
    assert len(cases) > 0
    first = cases[0]
    assert isinstance(first["expected_value"], bool)
    assert isinstance(first["rationale"], str)
    assert len(first["rationale"]) > 0
    assert "expected_behavior" not in first


async def test_preview_workflow_spec_carries_tools(
    client: httpx.AsyncClient,
):
    """Spot-check that the spec serialization actually contains the
    tools array — the UI lists tools so a missing field would render
    an empty section."""
    resp = await client.get("/api/nl-gen/preview/demand-prediction")
    spec = resp.json()["workflow_spec"]
    assert "tools" in spec
    assert isinstance(spec["tools"], list)
    assert len(spec["tools"]) >= 1
