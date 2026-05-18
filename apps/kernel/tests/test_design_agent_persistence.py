"""DB-backed tests for `design_agent.log.persist_design_agent_log`.

Requires `OWNEVO_DATABASE_URL` (the per-test database fixture in
`conftest.py` provisions a fresh schema with migrations applied).
"""

from __future__ import annotations

import json
import os

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.design_agent.ambiguity import AmbiguityFinding, AmbiguityReport
from ownevo_kernel.design_agent.log import (
    DesignAgentLog,
    DesignAgentLogEntry,
    persist_design_agent_log,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping DB integration tests",
)


async def _seed_workflow(db: asyncpg.Connection, workflow_id: str) -> None:
    await db.execute(
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, $2, '{}'::jsonb)
        """,
        workflow_id,
        "Workflow seeded for design-agent log persistence test.",
    )


async def test_persists_log_column_and_audit_rows(db: asyncpg.Connection) -> None:
    wf_id = "design-agent-log-roundtrip"
    await _seed_workflow(db, wf_id)

    log = DesignAgentLog(
        discovery_transcript=(
            DesignAgentLogEntry(
                question_index=0,
                kind="metric",
                question="Recall vs. precision?",
                answer="Recall (3x weight)",
            ),
            DesignAgentLogEntry(
                question_index=1,
                kind="ambiguity",
                question="Which drift baseline?",
                answer=None,
            ),
        ),
        ambiguity_report=AmbiguityReport(
            workflow_spec_id=wf_id,
            findings=(
                AmbiguityFinding(
                    kind="conflict",
                    severity="high",
                    location="description",
                    summary="Recall + zero false positives conflict",
                    suggested_question="Pick one?",
                ),
            ),
        ),
    )

    await persist_design_agent_log(db, workflow_id=wf_id, log=log)

    # JSONB column populated
    raw = await db.fetchval(
        "SELECT design_agent_log FROM workflows WHERE id = $1", wf_id,
    )
    assert raw is not None
    rt = DesignAgentLog.model_validate_json(raw if isinstance(raw, str) else json.dumps(raw))
    assert rt == log

    # One audit row per Q/A, plus one for the ambiguity report
    negotiation_rows = await db.fetch(
        "SELECT payload FROM audit_entries WHERE kind = 'design-agent-negotiation' "
        "ORDER BY seq ASC",
    )
    assert len(negotiation_rows) == 2
    payload_0 = json.loads(negotiation_rows[0]["payload"])
    assert payload_0["workflow_id"] == wf_id
    assert payload_0["question_index"] == 0
    assert payload_0["kind"] == "metric"
    assert payload_0["answer"] == "Recall (3x weight)"
    assert payload_0["skipped"] is False

    payload_1 = json.loads(negotiation_rows[1]["payload"])
    assert payload_1["skipped"] is True
    assert payload_1["answer"] is None

    ambiguity_rows = await db.fetch(
        "SELECT payload FROM audit_entries WHERE kind = 'design-agent-ambiguity'",
    )
    assert len(ambiguity_rows) == 1
    amb_payload = json.loads(ambiguity_rows[0]["payload"])
    assert amb_payload["workflow_id"] == wf_id
    assert amb_payload["workflow_spec_id"] == wf_id
    assert amb_payload["high_severity_count"] == 1
    assert len(amb_payload["findings"]) == 1


async def test_persists_log_with_no_ambiguity_report(
    db: asyncpg.Connection,
) -> None:
    wf_id = "design-agent-log-no-ambiguity"
    await _seed_workflow(db, wf_id)

    log = DesignAgentLog(
        discovery_transcript=(
            DesignAgentLogEntry(
                question_index=0,
                kind="metric",
                question="Q?",
                answer="A.",
            ),
        ),
    )
    await persist_design_agent_log(db, workflow_id=wf_id, log=log)

    negotiation_count = await db.fetchval(
        "SELECT COUNT(*) FROM audit_entries WHERE kind = 'design-agent-negotiation'"
    )
    ambiguity_count = await db.fetchval(
        "SELECT COUNT(*) FROM audit_entries WHERE kind = 'design-agent-ambiguity'"
    )
    assert negotiation_count == 1
    assert ambiguity_count == 0


async def test_persists_empty_transcript_writes_only_column(
    db: asyncpg.Connection,
) -> None:
    """An empty transcript writes the column (set to {}) but no audit rows."""
    wf_id = "design-agent-log-empty"
    await _seed_workflow(db, wf_id)

    await persist_design_agent_log(db, workflow_id=wf_id, log=DesignAgentLog())

    raw = await db.fetchval(
        "SELECT design_agent_log FROM workflows WHERE id = $1", wf_id,
    )
    assert raw is not None  # column populated, not NULL

    n_audits = await db.fetchval(
        "SELECT COUNT(*) FROM audit_entries "
        "WHERE kind IN ('design-agent-negotiation', 'design-agent-ambiguity')"
    )
    assert n_audits == 0


async def test_persists_actor_string(db: asyncpg.Connection) -> None:
    wf_id = "design-agent-log-actor"
    await _seed_workflow(db, wf_id)

    log = DesignAgentLog(
        discovery_transcript=(
            DesignAgentLogEntry(
                question_index=0,
                kind="metric",
                question="Q?",
                answer="A.",
            ),
        ),
    )
    await persist_design_agent_log(
        db, workflow_id=wf_id, log=log, actor="custom-actor",
    )

    actor = await db.fetchval(
        "SELECT actor FROM audit_entries WHERE kind = 'design-agent-negotiation' "
        "ORDER BY seq DESC LIMIT 1",
    )
    assert actor == "custom-actor"


async def test_default_actor_is_design_agent(db: asyncpg.Connection) -> None:
    wf_id = "design-agent-log-default-actor"
    await _seed_workflow(db, wf_id)

    log = DesignAgentLog(
        discovery_transcript=(
            DesignAgentLogEntry(
                question_index=0,
                kind="metric",
                question="Q?",
                answer="A.",
            ),
        ),
    )
    await persist_design_agent_log(db, workflow_id=wf_id, log=log)

    actor = await db.fetchval(
        "SELECT actor FROM audit_entries WHERE kind = 'design-agent-negotiation' "
        "ORDER BY seq DESC LIMIT 1",
    )
    assert actor == "design-agent"
