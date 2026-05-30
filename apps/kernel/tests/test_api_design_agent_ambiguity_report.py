"""HTTP tests for `POST /api/design-agent/ambiguity-report`.

DB-free, key-free. Mount the router on a bare FastAPI instance and
exercise the contract end-to-end against the packaged spec fixtures.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from ownevo_kernel.api.routes.design_agent_ambiguity import router
from ownevo_kernel.nl_gen.fixtures import (
    DEMAND_PREDICTION_METRIC,
    DEMAND_PREDICTION_SPEC,
)

_BENIGN_DESC = (
    "Forecast weekly demand at SKU-store level. Flag SKUs likely to need "
    "markdown. The category planner reviews flags weekly."
)


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as c:
        yield c


async def test_endpoint_returns_inferred_findings_for_demand_prediction(
    client: httpx.AsyncClient,
) -> None:
    """The packaged demand-prediction spec has at least one inferred
    artifact — the endpoint should surface it."""
    resp = await client.post(
        "/api/design-agent/ambiguity-report",
        json={
            "description": _BENIGN_DESC,
            "spec": DEMAND_PREDICTION_SPEC.model_dump(mode="json"),
            "metric_definition": DEMAND_PREDICTION_METRIC.model_dump(mode="json"),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_spec_id"] == DEMAND_PREDICTION_SPEC.id
    assert len(body["findings"]) >= 1
    inferred = [f for f in body["findings"] if f["kind"] == "inferred-artifact"]
    assert len(inferred) >= 1


async def test_endpoint_surfaces_description_conflict(
    client: httpx.AsyncClient,
) -> None:
    """Description with a 'recall + zero false positives' contradiction
    produces a high-severity conflict finding."""
    resp = await client.post(
        "/api/design-agent/ambiguity-report",
        json={
            "description": (
                "Maximize recall and accept zero false positives. The "
                "operations lead reviews flags daily."
            ),
            "spec": DEMAND_PREDICTION_SPEC.model_dump(mode="json"),
            "metric_definition": None,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    conflicts = [f for f in body["findings"] if f["kind"] == "conflict"]
    assert len(conflicts) >= 1
    assert any(f["severity"] == "high" for f in conflicts)


async def test_endpoint_accepts_null_metric_definition(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/design-agent/ambiguity-report",
        json={
            "description": _BENIGN_DESC,
            "spec": DEMAND_PREDICTION_SPEC.model_dump(mode="json"),
            "metric_definition": None,
        },
    )
    assert resp.status_code == 200


async def test_endpoint_rejects_empty_description(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/ambiguity-report",
        json={
            "description": "",
            "spec": DEMAND_PREDICTION_SPEC.model_dump(mode="json"),
        },
    )
    assert resp.status_code == 422


async def test_endpoint_rejects_extra_fields(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/ambiguity-report",
        json={
            "description": _BENIGN_DESC,
            "spec": DEMAND_PREDICTION_SPEC.model_dump(mode="json"),
            "metric_definition": None,
            "rogue_field": "no",
        },
    )
    assert resp.status_code == 422


async def test_endpoint_rejects_malformed_spec(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/design-agent/ambiguity-report",
        json={
            "description": _BENIGN_DESC,
            "spec": {"id": "x"},  # missing required fields
        },
    )
    assert resp.status_code == 422


async def test_endpoint_accepts_description_at_max_length(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/design-agent/ambiguity-report",
        json={
            "description": "x" * 4096,
            "spec": DEMAND_PREDICTION_SPEC.model_dump(mode="json"),
        },
    )
    assert resp.status_code == 200


async def test_endpoint_rejects_description_over_max_length(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/design-agent/ambiguity-report",
        json={
            "description": "x" * 4097,
            "spec": DEMAND_PREDICTION_SPEC.model_dump(mode="json"),
        },
    )
    assert resp.status_code == 422


async def test_endpoint_returns_zero_findings_for_derived_spec(
    client: httpx.AsyncClient,
) -> None:
    """A fully-derived spec with a benign description should yield no findings."""
    from ownevo_kernel.nl_gen.spec import (
        AgentTool,
        DataSource,
        EnvGenerator,
        Persona,
        Provenance,
        ReviewerSpec,
        SuccessCriterionStub,
        UILayout,
        UITab,
        WorkflowEnvironment,
        WorkflowSpec,
    )
    from ownevo_format.ui_views import MetricCards

    derived = Provenance(kind="derived", source="quoted from description")
    clean_spec = WorkflowSpec(
        id="clean-spec",
        domain="supply-chain",  # type: ignore[arg-type]
        environment=WorkflowEnvironment(
            data_sources=[DataSource(id="erp", description="x", provenance=derived)],
            env_generators=[EnvGenerator(name="gen", description="x", provenance=derived)],
            personas=[Persona(role="planner", description="x", provenance=derived)],
        ),
        tools=[AgentTool(name="run_forecast", description="x", provenance=derived)],
        reviewer=ReviewerSpec(role="ops lead", description="x", provenance=derived),
        success_criterion=SuccessCriterionStub(
            direction="maximize",  # type: ignore[arg-type]
            target_metric_name="wape",
            description="minimize error",
        ),
        ui=UILayout(
            tabs=[UITab(name="Overview", views=[MetricCards(type="MetricCards", fields=["score"])])],
        ),
    )
    resp = await client.post(
        "/api/design-agent/ambiguity-report",
        json={
            "description": _BENIGN_DESC,
            "spec": clean_spec.model_dump(mode="json"),
            "metric_definition": None,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["findings"] == []
