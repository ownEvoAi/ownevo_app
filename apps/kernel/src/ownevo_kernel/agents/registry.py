"""Agent registry write/read API.

One agent per workflow. `register_agent_for_workflow` is the idempotent
write path: it reads the workflow row to derive the agent's display name
and origin, then inserts the registry row if one does not already exist.
Calling it again for the same workflow is a no-op — the agent keeps its
original identity, name, and origin across re-imports and config edits.

Reads (`list_agents` / `get_agent`) decorate each stored row with three
derived metrics computed live from `iterations` and `eval_cases`, so the
registry never carries counters that can drift from the source tables.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from .models import AgentOrigin, AgentRecord, AgentStatus

# Cap the derived display name. A workflow description can be a full
# paragraph; the registry wants a label, and the UI truncates further.
_NAME_MAX_LEN = 200


# Shared projection: stored columns + three derived metrics. The derived
# fields are scalar subqueries keyed on the agent's workflow_id, mirroring
# the live-computation approach the workflow-list endpoint uses.
_AGENT_SELECT = """
SELECT
    a.id,
    a.workflow_id,
    a.name,
    a.origin,
    a.owner,
    a.status,
    a.identity_hash,
    a.created_at,
    a.status_updated_at,
    (
        SELECT MAX(i.ended_at)
        FROM iterations i
        WHERE i.workflow_id = a.workflow_id
          AND i.state <> 'running'
    )                                           AS last_iteration_at,
    (
        SELECT COUNT(*)::int
        FROM eval_cases ec
        WHERE ec.workflow_id = a.workflow_id
    )                                           AS eval_coverage_count,
    (
        SELECT COUNT(*)::int
        FROM iterations i
        WHERE i.workflow_id = a.workflow_id
          AND i.state <> 'running'
    )                                           AS iteration_count
FROM agents a
"""


async def register_agent(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    description: str | None,
    workflow_origin: str | None,
    owner: str | None = None,
) -> None:
    """Idempotently register an agent from already-known workflow fields.

    For callers that hold the workflow's `description` and `origin` at
    creation time (the NL-gen / import routes, the replay seeder) — they
    pass those directly rather than forcing a re-read. The agent's `name`
    and `origin` are derived here so the derivation stays in one place;
    `ON CONFLICT (workflow_id) DO NOTHING` keeps registration a no-op once
    an agent already exists (identity, name, and origin persist).
    """
    await conn.execute(
        """
        INSERT INTO agents (workflow_id, name, origin, owner)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (workflow_id) DO NOTHING
        """,
        workflow_id,
        _derive_name(description, workflow_id),
        _normalize_origin(workflow_origin).value,
        owner,
    )


async def register_agent_for_workflow(
    conn: asyncpg.Connection,
    workflow_id: str,
    *,
    owner: str | None = None,
) -> AgentRecord | None:
    """Ensure a registry row exists for `workflow_id`; return the agent.

    Reads the workflow row to derive the agent's name and origin, so this
    is the registration path for callers that only hold a workflow id —
    notably the OTLP ingest hook, where an imported agent shows up for the
    first time as a stream of traces. Returns ``None`` when the workflow
    does not exist (nothing to register). Idempotent.
    """
    wf = await conn.fetchrow(
        "SELECT description, origin FROM workflows WHERE id = $1",
        workflow_id,
    )
    if wf is None:
        return None

    await register_agent(
        conn,
        workflow_id=workflow_id,
        description=wf["description"],
        workflow_origin=wf["origin"],
        owner=owner,
    )
    return await get_agent_by_workflow(conn, workflow_id)


async def set_agent_status(
    conn: asyncpg.Connection,
    agent_id: UUID,
    status: AgentStatus | str,
) -> AgentRecord | None:
    """Transition an agent's lifecycle status. None if the agent is gone.

    Stamps `status_updated_at` so the registry records when the
    transition happened.
    """
    status_value = status.value if isinstance(status, AgentStatus) else status
    result = await conn.execute(
        """
        UPDATE agents
        SET status = $2, status_updated_at = now()
        WHERE id = $1
        """,
        agent_id,
        status_value,
    )
    if result == "UPDATE 0":
        return None
    return await get_agent(conn, agent_id)


async def list_agents(
    conn: asyncpg.Connection,
    *,
    origin: AgentOrigin | str | None = None,
    status: AgentStatus | str | None = None,
) -> list[AgentRecord]:
    """Every registered agent across all origins.

    Optional `origin` / `status` filters. Ordered by `created_at ASC`
    so the bootstrap agents rank first, matching the workflow-list
    ordering the rest of the UI uses.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if origin is not None:
        params.append(origin.value if isinstance(origin, AgentOrigin) else origin)
        clauses.append(f"a.origin = ${len(params)}")
    if status is not None:
        params.append(status.value if isinstance(status, AgentStatus) else status)
        clauses.append(f"a.status = ${len(params)}")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await conn.fetch(
        f"{_AGENT_SELECT}{where}\nORDER BY a.created_at ASC, a.id ASC",
        *params,
    )
    return [_row_to_agent(r) for r in rows]


async def get_agent(conn: asyncpg.Connection, agent_id: UUID) -> AgentRecord | None:
    row = await conn.fetchrow(f"{_AGENT_SELECT}WHERE a.id = $1", agent_id)
    return _row_to_agent(row) if row is not None else None


async def get_agent_by_workflow(
    conn: asyncpg.Connection, workflow_id: str
) -> AgentRecord | None:
    row = await conn.fetchrow(f"{_AGENT_SELECT}WHERE a.workflow_id = $1", workflow_id)
    return _row_to_agent(row) if row is not None else None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _derive_name(description: str | None, workflow_id: str) -> str:
    """A short display label for the agent.

    The workflow description doubles as the human label; trimmed and
    capped so a paragraph-length description does not become the name.
    Falls back to the workflow id when the description is empty.
    """
    if description:
        trimmed = description.strip()
        if trimmed:
            return trimmed[:_NAME_MAX_LEN]
    return workflow_id


def _normalize_origin(workflow_origin: str | None) -> AgentOrigin:
    """Map `workflows.origin` onto the agent origin vocabulary.

    A greenfield workflow has `origin IS NULL`; the registry records that
    as the explicit `greenfield` value. An unrecognised origin also maps
    to greenfield rather than failing the registration — the registry
    should never block a workflow from getting an agent.
    """
    if workflow_origin is None:
        return AgentOrigin.GREENFIELD
    try:
        return AgentOrigin(workflow_origin)
    except ValueError:
        return AgentOrigin.GREENFIELD


def _row_to_agent(row: asyncpg.Record) -> AgentRecord:
    return AgentRecord(
        id=row["id"],
        workflow_id=row["workflow_id"],
        name=row["name"],
        origin=AgentOrigin(row["origin"]),
        owner=row["owner"],
        status=AgentStatus(row["status"]),
        identity_hash=row["identity_hash"],
        created_at=row["created_at"],
        status_updated_at=row["status_updated_at"],
        last_iteration_at=row["last_iteration_at"],
        eval_coverage_count=row["eval_coverage_count"],
        iteration_count=row["iteration_count"],
    )


__all__ = [
    "get_agent",
    "get_agent_by_workflow",
    "list_agents",
    "register_agent",
    "register_agent_for_workflow",
    "set_agent_status",
]
