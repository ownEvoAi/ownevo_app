"""Typed models for the agent registry.

`AgentRecord` is the read shape returned by the registry. The stored
columns (identity, name, origin, owner, status, timestamps) come
straight off the `agents` table; the three derived fields
(`last_iteration_at`, `eval_coverage_count`, `iteration_count`) are
computed live from `iterations` / `eval_cases` at read time rather than
maintained as drift-prone counters — the same approach the workflow
list uses for its summary metrics.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AgentOrigin(StrEnum):
    """Where an agent came from.

    GREENFIELD — authored in the kernel (NL-gen / design agent). Maps to
        a `workflows.origin IS NULL` row.
    LANGSMITH / COPILOT_STUDIO — imported from that external platform.
    """

    GREENFIELD = "greenfield"
    LANGSMITH = "langsmith"
    COPILOT_STUDIO = "copilot_studio"


class AgentStatus(StrEnum):
    """Lifecycle state of a registered agent."""

    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class AgentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workflow_id: str
    name: str
    origin: AgentOrigin
    owner: str | None
    status: AgentStatus
    identity_hash: UUID
    created_at: datetime
    status_updated_at: datetime
    # Derived (computed at read time, not stored):
    last_iteration_at: datetime | None
    eval_coverage_count: int
    iteration_count: int


__all__ = ["AgentOrigin", "AgentRecord", "AgentStatus"]
