"""DB-backed tests for `import_log.persist_design_agent_import_log`.

Requires `OWNEVO_DATABASE_URL` (the per-test database fixture in
`conftest.py` provisions a fresh schema with migrations applied). The
trace-import path persists to `workflows.design_agent_import_log` and
mirrors the reverse-discovery turn + every Q/A into the audit chain
under the dedicated `design-agent-negotiation-import` kind.
"""

from __future__ import annotations

import json
import os

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.design_agent.import_log import (
    DesignAgentImportLog,
    ReverseDiscoveryRecord,
    load_design_agent_import_log,
    persist_design_agent_import_log,
)
from ownevo_kernel.design_agent.log import DesignAgentLogEntry

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
        "Workflow seeded for import-log persistence test.",
    )


def _transcript() -> tuple[DesignAgentLogEntry, ...]:
    return (
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
    )


async def test_persists_column_and_reverse_discovery_plus_qa(
    db: asyncpg.Connection,
) -> None:
    wf_id = "import-log-roundtrip"
    await _seed_workflow(db, wf_id)

    log = DesignAgentImportLog(
        reverse_discovery=ReverseDiscoveryRecord(
            inferred_summary="Forecasts weekly demand per SKU from recent sales.",
            basis="definition+traces",
            source="llm",
            decision="corrected",
            final_definition="Forecasts daily demand per SKU and store.",
        ),
        discovery_transcript=_transcript(),
    )

    await persist_design_agent_import_log(db, workflow_id=wf_id, log=log)

    # JSONB column populated and round-trips.
    raw = await db.fetchval(
        "SELECT design_agent_import_log FROM workflows WHERE id = $1", wf_id
    )
    assert raw is not None
    rt = load_design_agent_import_log(raw)
    assert rt == log

    # One reverse-discovery row + one per Q/A, all under the import kind.
    rows = await db.fetch(
        "SELECT payload FROM audit_entries "
        "WHERE kind = 'design-agent-negotiation-import' ORDER BY seq ASC"
    )
    assert len(rows) == 3
    rd_payload = json.loads(rows[0]["payload"])
    assert rd_payload["phase"] == "reverse-discovery"
    assert rd_payload["decision"] == "corrected"
    assert rd_payload["basis"] == "definition+traces"
    assert rd_payload["final_definition"] == "Forecasts daily demand per SKU and store."

    qa_first = json.loads(rows[1]["payload"])
    assert qa_first["phase"] == "negotiation"
    assert qa_first["question_index"] == 0
    assert qa_first["answer"] == "Recall (3x weight)"
    assert qa_first["skipped"] is False

    qa_second = json.loads(rows[2]["payload"])
    assert qa_second["skipped"] is True


async def test_reverse_discovery_only_no_transcript(db: asyncpg.Connection) -> None:
    wf_id = "import-log-rd-only"
    await _seed_workflow(db, wf_id)

    log = DesignAgentImportLog(
        reverse_discovery=ReverseDiscoveryRecord(
            inferred_summary="Triages inbound support tickets by urgency.",
            basis="traces",
            source="fallback",
            decision="confirmed",
            final_definition="Triages inbound support tickets by urgency.",
        ),
    )
    await persist_design_agent_import_log(db, workflow_id=wf_id, log=log)

    count = await db.fetchval(
        "SELECT COUNT(*) FROM audit_entries "
        "WHERE kind = 'design-agent-negotiation-import'"
    )
    assert count == 1


async def test_does_not_use_authoring_negotiation_kind(
    db: asyncpg.Connection,
) -> None:
    """The import path must not write `design-agent-negotiation` rows —
    those belong to the written-description authoring surface."""
    wf_id = "import-log-kind-isolation"
    await _seed_workflow(db, wf_id)

    await persist_design_agent_import_log(
        db,
        workflow_id=wf_id,
        log=DesignAgentImportLog(discovery_transcript=_transcript()),
    )

    authoring_count = await db.fetchval(
        "SELECT COUNT(*) FROM audit_entries WHERE kind = 'design-agent-negotiation'"
    )
    assert authoring_count == 0


async def test_custom_actor(db: asyncpg.Connection) -> None:
    wf_id = "import-log-actor"
    await _seed_workflow(db, wf_id)

    await persist_design_agent_import_log(
        db,
        workflow_id=wf_id,
        log=DesignAgentImportLog(discovery_transcript=_transcript()[:1]),
        actor="custom-actor",
    )

    actor = await db.fetchval(
        "SELECT actor FROM audit_entries "
        "WHERE kind = 'design-agent-negotiation-import' ORDER BY seq DESC LIMIT 1"
    )
    assert actor == "custom-actor"


async def test_raises_if_workflow_missing(db: asyncpg.Connection) -> None:
    with pytest.raises(ValueError, match="not found"):
        await persist_design_agent_import_log(
            db,
            workflow_id="nonexistent-workflow-id",
            log=DesignAgentImportLog(discovery_transcript=_transcript()[:1]),
        )
