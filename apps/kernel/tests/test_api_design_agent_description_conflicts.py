"""HTTP tests for `POST /api/design-agent/description-conflicts`.

DB-free, key-free. The endpoint is the pre-generation half of the
ambiguity-detection pair: it runs `find_description_conflicts` over the
raw description (no spec required) so the chat panel can surface
contradictions before the operator clicks Generate.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from ownevo_kernel.api.routes.design_agent_ambiguity import router


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c


async def test_benign_description_returns_no_findings(
    client: httpx.AsyncClient,
) -> None:
    """A description without canonical contradictions produces an empty
    findings list. The shape stays `{findings: []}` so the client can
    rely on the same response envelope either way."""
    resp = await client.post(
        "/api/design-agent/description-conflicts",
        json={
            "description": (
                "Forecast weekly demand at SKU-store level. Flag SKUs "
                "likely to need markdown. The category planner reviews "
                "flags weekly and decides which to action."
            ),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"findings": []}


async def test_recall_precision_conflict_surfaces_high_severity_finding(
    client: httpx.AsyncClient,
) -> None:
    """The canonical 'maximize recall, zero false positives' description
    contradiction fires the rule-based pass and surfaces a high-severity
    conflict finding with a non-empty suggested_question."""
    resp = await client.post(
        "/api/design-agent/description-conflicts",
        json={
            "description": (
                "Maximize recall and accept zero false positives. The "
                "operations lead reviews flags daily."
            ),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["findings"]) >= 1
    high_sev = [f for f in body["findings"] if f["severity"] == "high"]
    assert len(high_sev) >= 1
    f0 = body["findings"][0]
    assert f0["kind"] == "conflict"
    assert f0["location"] == "description"
    assert f0["suggested_question"]


async def test_no_change_constraint_surfaces_finding(
    client: httpx.AsyncClient,
) -> None:
    """A 'don't change the model' description fires the second canonical
    contradiction rule."""
    resp = await client.post(
        "/api/design-agent/description-conflicts",
        json={
            "description": (
                "Maximize the score on the eval set but do not change "
                "the model. The data scientist reviews proposed changes."
            ),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(
        "change" in f["summary"].lower() or "change" in f["suggested_question"].lower()
        for f in body["findings"]
    )


async def test_too_short_description_returns_422(
    client: httpx.AsyncClient,
) -> None:
    """The request validator enforces the same 50-char minimum as
    `/ambiguity-report` so the endpoint pair has matching guards."""
    resp = await client.post(
        "/api/design-agent/description-conflicts",
        json={"description": "too short"},
    )
    assert resp.status_code == 422


async def test_extra_field_rejected(client: httpx.AsyncClient) -> None:
    """extra='forbid' guards against silent typos like `spec` in the
    body (which is allowed on the /ambiguity-report endpoint but not
    here)."""
    resp = await client.post(
        "/api/design-agent/description-conflicts",
        json={
            "description": (
                "Forecast weekly demand at SKU-store level. Flag SKUs "
                "likely to need markdown. Category planner reviews."
            ),
            "spec": {},
        },
    )
    assert resp.status_code == 422
