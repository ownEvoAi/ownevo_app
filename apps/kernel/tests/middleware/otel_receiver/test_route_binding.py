"""Workflow-binding tests for `POST /api/otel/v1/traces`.

Once a receiver token names a workflow (or the request supplies one),
ingested traces must land with `traces.workflow_id` set instead of
NULL — otherwise they never surface on the workflow's Failures /
Overview tabs and the clustering pipeline can't scope them.

These tests run in the directory's default auth-optional mode (set by
conftest) for the query-param cases, and explicitly insert tokens for
the token-binding cases.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import httpx
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.middleware.otel_receiver.auth import mint_token

from ._fixture_cases import CASES

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping binding tests",
)


def _payload() -> dict:
    return next(c.payload for c in CASES if c.name == "01_chat_basic_text")


async def _seed_workflow(db: asyncpg.Connection, wf_id: str) -> None:
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, $2, '{}'::jsonb) ON CONFLICT DO NOTHING",
        wf_id,
        f"binding test ({wf_id})",
    )


async def _mint_bound_token(
    db: asyncpg.Connection, wf_id: str | None
) -> str:
    plaintext, token_hash = mint_token()
    await db.execute(
        "INSERT INTO receiver_tokens (token_hash, label, workflow_id) "
        "VALUES ($1, 'binding-test', $2)",
        token_hash,
        wf_id,
    )
    return plaintext


async def test_token_bound_workflow_lands_on_trace(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    await _seed_workflow(db, "wf-bound")
    token = await _mint_bound_token(db, "wf-bound")

    resp = await api_client.post(
        "/api/otel/v1/traces",
        json=_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    trace_id = uuid.UUID(resp.json()["created_trace_ids"][0])

    bound = await db.fetchval(
        "SELECT workflow_id FROM traces WHERE id = $1", trace_id
    )
    assert bound == "wf-bound"


async def test_query_param_binds_for_agnostic_token(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    await _seed_workflow(db, "wf-query")
    token = await _mint_bound_token(db, None)  # workflow-agnostic

    resp = await api_client.post(
        "/api/otel/v1/traces?workflow_id=wf-query",
        json=_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    trace_id = uuid.UUID(resp.json()["created_trace_ids"][0])

    bound = await db.fetchval(
        "SELECT workflow_id FROM traces WHERE id = $1", trace_id
    )
    assert bound == "wf-query"


async def test_query_param_for_unknown_workflow_404(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    token = await _mint_bound_token(db, None)
    resp = await api_client.post(
        "/api/otel/v1/traces?workflow_id=does-not-exist",
        json=_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_cross_workflow_query_param_rejected_403(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    await _seed_workflow(db, "wf-token")
    await _seed_workflow(db, "wf-other")
    token = await _mint_bound_token(db, "wf-token")

    resp = await api_client.post(
        "/api/otel/v1/traces?workflow_id=wf-other",
        json=_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_matching_query_param_allowed(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    await _seed_workflow(db, "wf-match")
    token = await _mint_bound_token(db, "wf-match")

    resp = await api_client.post(
        "/api/otel/v1/traces?workflow_id=wf-match",
        json=_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


async def test_unbound_when_no_token_and_no_query(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    # Auth-optional mode (set by conftest), no token, no query param →
    # trace lands unbound, preserving pre-binding behaviour.
    resp = await api_client.post("/api/otel/v1/traces", json=_payload())
    assert resp.status_code == 200, resp.text
    trace_id = uuid.UUID(resp.json()["created_trace_ids"][0])
    bound = await db.fetchval(
        "SELECT workflow_id FROM traces WHERE id = $1", trace_id
    )
    assert bound is None


async def test_append_preserves_existing_binding(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    # First batch binds the trace to a workflow; a later workflow-agnostic
    # flush for the same trace_id must not orphan it (COALESCE keeps the
    # first binding).
    await _seed_workflow(db, "wf-first")
    bound_token = await _mint_bound_token(db, "wf-first")
    agnostic_token = await _mint_bound_token(db, None)

    resp1 = await api_client.post(
        "/api/otel/v1/traces",
        json=_payload(),
        headers={"Authorization": f"Bearer {bound_token}"},
    )
    assert resp1.status_code == 200, resp1.text
    trace_id = uuid.UUID(resp1.json()["created_trace_ids"][0])

    resp2 = await api_client.post(
        "/api/otel/v1/traces",
        json=_payload(),
        headers={"Authorization": f"Bearer {agnostic_token}"},
    )
    assert resp2.status_code == 200, resp2.text
    # Same trace_id appended, not newly created.
    assert uuid.UUID(resp2.json()["appended_trace_ids"][0]) == trace_id

    bound = await db.fetchval(
        "SELECT workflow_id FROM traces WHERE id = $1", trace_id
    )
    assert bound == "wf-first"
