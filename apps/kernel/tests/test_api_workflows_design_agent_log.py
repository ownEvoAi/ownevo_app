"""HTTP test for `design_agent_log` surface on GET /api/workflows/{id}.

Requires `OWNEVO_DATABASE_URL`. Uses the `api_client` fixture so the
log column flows through asyncpg → JSONB-decode → WorkflowAnatomy →
HTTP response without faking the wire layer.
"""

from __future__ import annotations

import os

import asyncpg
import httpx
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.design_agent.log import (
    DesignAgentLog,
    DesignAgentLogEntry,
    persist_design_agent_log,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping DB integration tests",
)


async def test_get_workflow_returns_design_agent_log(
    db: asyncpg.Connection,
    api_client: httpx.AsyncClient,
) -> None:
    wf_id = "design-agent-log-http-roundtrip"
    await db.execute(
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, $2, '{}'::jsonb)
        """,
        wf_id,
        "Workflow seeded for log HTTP test.",
    )
    log = DesignAgentLog(
        discovery_transcript=(
            DesignAgentLogEntry(
                question_index=0,
                kind="metric",
                question="Recall vs. precision?",
                answer="Recall",
            ),
            DesignAgentLogEntry(
                question_index=1,
                kind="trigger",
                question="What kicks the workflow off?",
                answer=None,
            ),
        ),
    )
    await persist_design_agent_log(db, workflow_id=wf_id, log=log)

    resp = await api_client.get(f"/api/workflows/{wf_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert "design_agent_log" in body
    persisted = body["design_agent_log"]
    assert persisted is not None
    assert len(persisted["discovery_transcript"]) == 2
    assert persisted["discovery_transcript"][0]["kind"] == "metric"
    assert persisted["discovery_transcript"][0]["answer"] == "Recall"
    assert persisted["discovery_transcript"][1]["answer"] is None


async def test_get_workflow_returns_null_log_when_unset(
    db: asyncpg.Connection,
    api_client: httpx.AsyncClient,
) -> None:
    wf_id = "design-agent-log-null"
    await db.execute(
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, $2, '{}'::jsonb)
        """,
        wf_id,
        "Workflow seeded without running discovery.",
    )

    resp = await api_client.get(f"/api/workflows/{wf_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["design_agent_log"] is None
