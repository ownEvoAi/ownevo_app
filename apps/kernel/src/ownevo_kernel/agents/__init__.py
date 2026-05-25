"""Agent registry — the workspace-wide index of connected agents.

An agent is the improvable unit behind a workflow. This package owns the
registry seam: `register_agent_for_workflow` is the idempotent write path
called when a workflow is created or an external agent's trace is first
ingested; `list_agents` / `get_agent` back the registry page; and
`set_agent_status` records active/paused/archived transitions.
"""

from __future__ import annotations

from .models import AgentOrigin, AgentRecord, AgentStatus
from .registry import (
    get_agent,
    list_agents,
    register_agent,
    register_agent_for_workflow,
    set_agent_status,
)

__all__ = [
    "AgentOrigin",
    "AgentRecord",
    "AgentStatus",
    "get_agent",
    "list_agents",
    "register_agent",
    "register_agent_for_workflow",
    "set_agent_status",
]
