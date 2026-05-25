"""`/api/agents` — the workspace-wide agent registry.

Read endpoints back the registry page: a single list spanning every
origin (greenfield + imported), and a per-agent detail. The PATCH
endpoint records lifecycle transitions (active / paused / archived).

Single-tenant for MVP — no workspace filter is applied; the list
is the entire registry. Multi-tenant retrofit adds `WHERE workspace_id`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from ...agents import (
    AgentOrigin,
    AgentRecord,
    AgentStatus,
    get_agent,
    list_agents,
    set_agent_status,
)
from ..deps import ConnDep, DemoModeCheck

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentView(BaseModel):
    """One agent as rendered on the registry page.

    Mirrors `AgentRecord`; the `identity_hash` is surfaced as a string so
    JSON consumers don't have to special-case UUID formatting.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workflow_id: str
    name: str
    origin: AgentOrigin
    owner: str | None
    status: AgentStatus
    identity_hash: str
    created_at: datetime
    status_updated_at: datetime
    last_iteration_at: datetime | None
    eval_coverage_count: int
    iteration_count: int


class AgentList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AgentView]
    total: int


class AgentStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentStatus


def _to_view(record: AgentRecord) -> AgentView:
    return AgentView(
        id=record.id,
        workflow_id=record.workflow_id,
        name=record.name,
        origin=record.origin,
        owner=record.owner,
        status=record.status,
        identity_hash=str(record.identity_hash),
        created_at=record.created_at,
        status_updated_at=record.status_updated_at,
        last_iteration_at=record.last_iteration_at,
        eval_coverage_count=record.eval_coverage_count,
        iteration_count=record.iteration_count,
    )


@router.get("", response_model=AgentList)
async def list_all_agents(
    conn: ConnDep,
    origin: Annotated[AgentOrigin | None, Query()] = None,
    status_filter: Annotated[AgentStatus | None, Query(alias="status")] = None,
) -> AgentList:
    """Every registered agent across origins, with live coverage metrics."""
    records = await list_agents(conn, origin=origin, status=status_filter)
    items = [_to_view(r) for r in records]
    return AgentList(items=items, total=len(items))


@router.get("/{agent_id}", response_model=AgentView)
async def get_one_agent(agent_id: UUID, conn: ConnDep) -> AgentView:
    """Per-agent detail. 404 if the agent is not registered."""
    record = await get_agent(conn, agent_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return _to_view(record)


@router.patch("/{agent_id}/status", response_model=AgentView)
async def update_agent_status(
    agent_id: UUID,
    body: AgentStatusUpdate,
    conn: ConnDep,
    _demo: DemoModeCheck,
) -> AgentView:
    """Transition an agent's lifecycle status. 404 if the agent is gone."""
    record = await set_agent_status(conn, agent_id, body.status)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return _to_view(record)
