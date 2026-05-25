"""Tests for the agent registry — registration, status, and read metrics.

Split across the registry seam (`ownevo_kernel.agents`) and the
`/api/agents` HTTP surface. Both run against the per-test Postgres DB
provided by `conftest.py`.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.agents import (
    AgentOrigin,
    AgentStatus,
    get_agent,
    get_agent_by_workflow,
    list_agents,
    register_agent,
    register_agent_for_workflow,
    set_agent_status,
)
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_workflow(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    description: str = "Recalibrate credit lines",
    origin: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO workflows (id, description, spec, mode, origin)
        VALUES ($1, $2, '{}'::jsonb, 'gated'::workflow_mode, $3)
        ON CONFLICT (id) DO NOTHING
        """,
        workflow_id,
        description,
        origin,
    )


async def _seed_iteration(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    iteration_index: int,
    state: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, ended_at)
        VALUES ($1, $2, $3::iteration_state,
                CASE WHEN $3 = 'running' THEN NULL ELSE now() END)
        """,
        workflow_id,
        iteration_index,
        state,
    )


async def _seed_eval_case(conn: asyncpg.Connection, *, workflow_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO eval_cases (workflow_id, provenance, input, expected_behavior)
        VALUES ($1, 'hand-authored'::provenance_kind, '{}'::jsonb, '{}'::jsonb)
        """,
        workflow_id,
    )


# ---------------------------------------------------------------------------
# Registry: registration
# ---------------------------------------------------------------------------


async def test_register_creates_agent_for_workflow(db: asyncpg.Connection) -> None:
    await _seed_workflow(db, workflow_id="wf-credit")
    agent = await register_agent_for_workflow(db, "wf-credit")
    assert agent is not None
    assert agent.workflow_id == "wf-credit"
    assert agent.name == "Recalibrate credit lines"
    assert agent.origin is AgentOrigin.GREENFIELD
    assert agent.status is AgentStatus.ACTIVE
    assert agent.owner is None


async def test_register_is_idempotent(db: asyncpg.Connection) -> None:
    await _seed_workflow(db, workflow_id="wf-1")
    first = await register_agent_for_workflow(db, "wf-1")
    second = await register_agent_for_workflow(db, "wf-1")
    assert first is not None and second is not None
    # Same identity persists across re-registration.
    assert first.id == second.id
    assert first.identity_hash == second.identity_hash
    agents = await list_agents(db)
    assert len([a for a in agents if a.workflow_id == "wf-1"]) == 1


async def test_register_unknown_workflow_returns_none(db: asyncpg.Connection) -> None:
    assert await register_agent_for_workflow(db, "does-not-exist") is None


async def test_register_maps_origin_from_workflow(db: asyncpg.Connection) -> None:
    await _seed_workflow(db, workflow_id="wf-ls", origin="langsmith")
    agent = await register_agent_for_workflow(db, "wf-ls")
    assert agent is not None
    assert agent.origin is AgentOrigin.LANGSMITH


async def test_name_falls_back_to_id_when_description_blank(
    db: asyncpg.Connection,
) -> None:
    await _seed_workflow(db, workflow_id="wf-blank", description="")
    agent = await register_agent_for_workflow(db, "wf-blank")
    assert agent is not None
    assert agent.name == "wf-blank"


# ---------------------------------------------------------------------------
# Registry: status transitions
# ---------------------------------------------------------------------------


async def test_set_status_transitions(db: asyncpg.Connection) -> None:
    await _seed_workflow(db, workflow_id="wf-s")
    agent = await register_agent_for_workflow(db, "wf-s")
    assert agent is not None

    paused = await set_agent_status(db, agent.id, AgentStatus.PAUSED)
    assert paused is not None
    assert paused.status is AgentStatus.PAUSED
    assert paused.status_updated_at >= agent.status_updated_at

    archived = await set_agent_status(db, agent.id, "archived")
    assert archived is not None
    assert archived.status is AgentStatus.ARCHIVED


async def test_set_status_unknown_agent_returns_none(db: asyncpg.Connection) -> None:
    import uuid

    assert await set_agent_status(db, uuid.uuid4(), AgentStatus.PAUSED) is None


# ---------------------------------------------------------------------------
# Registry: live-computed metrics
# ---------------------------------------------------------------------------


async def test_metrics_computed_live(db: asyncpg.Connection) -> None:
    await _seed_workflow(db, workflow_id="wf-m")
    await register_agent_for_workflow(db, "wf-m")

    # Two completed iterations + one still running (excluded from counts).
    await _seed_iteration(db, workflow_id="wf-m", iteration_index=0, state="gate-pass")
    await _seed_iteration(db, workflow_id="wf-m", iteration_index=1, state="gate-pass")
    await _seed_iteration(db, workflow_id="wf-m", iteration_index=2, state="running")
    await _seed_eval_case(db, workflow_id="wf-m")
    await _seed_eval_case(db, workflow_id="wf-m")
    await _seed_eval_case(db, workflow_id="wf-m")

    agent = await get_agent(db, (await list_agents(db))[0].id)
    assert agent is not None
    assert agent.iteration_count == 2
    assert agent.eval_coverage_count == 3
    assert agent.last_iteration_at is not None


async def test_list_filters_by_origin_and_status(db: asyncpg.Connection) -> None:
    await _seed_workflow(db, workflow_id="wf-green")
    await _seed_workflow(db, workflow_id="wf-cs", origin="copilot_studio")
    green = await register_agent_for_workflow(db, "wf-green")
    await register_agent_for_workflow(db, "wf-cs")
    assert green is not None
    await set_agent_status(db, green.id, AgentStatus.ARCHIVED)

    cs_only = await list_agents(db, origin=AgentOrigin.COPILOT_STUDIO)
    assert [a.workflow_id for a in cs_only] == ["wf-cs"]

    archived_only = await list_agents(db, status=AgentStatus.ARCHIVED)
    assert [a.workflow_id for a in archived_only] == ["wf-green"]


async def test_list_filters_combined_origin_and_status(db: asyncpg.Connection) -> None:
    # Verify that origin= AND status= are ANDed, not ORed.
    await _seed_workflow(db, workflow_id="wf-ls-active", origin="langsmith")
    await _seed_workflow(db, workflow_id="wf-ls-paused", origin="langsmith")
    await _seed_workflow(db, workflow_id="wf-gf-paused")
    ls_active = await register_agent_for_workflow(db, "wf-ls-active")
    ls_paused = await register_agent_for_workflow(db, "wf-ls-paused")
    await register_agent_for_workflow(db, "wf-gf-paused")
    assert ls_paused is not None and ls_active is not None
    await set_agent_status(db, ls_paused.id, AgentStatus.PAUSED)
    gf_paused_agent = await get_agent_by_workflow(db, "wf-gf-paused")
    assert gf_paused_agent is not None
    await set_agent_status(db, gf_paused_agent.id, AgentStatus.PAUSED)

    # Only the langsmith+paused intersection — not the greenfield+paused one.
    result = await list_agents(
        db, origin=AgentOrigin.LANGSMITH, status=AgentStatus.PAUSED
    )
    assert [a.workflow_id for a in result] == ["wf-ls-paused"]


# ---------------------------------------------------------------------------
# Registry: lower-level register_agent and edge cases
# ---------------------------------------------------------------------------


async def test_register_agent_direct(db: asyncpg.Connection) -> None:
    """register_agent (lower-level) inserts a row with the supplied fields."""
    await _seed_workflow(db, workflow_id="wf-direct", description="Forecast credit risk")
    await register_agent(
        db,
        workflow_id="wf-direct",
        description="Forecast credit risk",
        workflow_origin="langsmith",
    )
    agent = await get_agent_by_workflow(db, "wf-direct")
    assert agent is not None
    assert agent.name == "Forecast credit risk"
    assert agent.origin is AgentOrigin.LANGSMITH
    # Second call is idempotent — no duplicate row, original identity kept.
    await register_agent(
        db, workflow_id="wf-direct", description="other", workflow_origin=None
    )
    matches = [a for a in await list_agents(db) if a.workflow_id == "wf-direct"]
    assert len(matches) == 1
    assert matches[0].name == "Forecast credit risk"


async def test_name_truncated_at_200_chars(db: asyncpg.Connection) -> None:
    """Descriptions longer than 200 characters are capped to 200."""
    long_desc = "A" * 250
    await _seed_workflow(db, workflow_id="wf-long", description=long_desc)
    agent = await register_agent_for_workflow(db, "wf-long")
    assert agent is not None
    assert agent.name == long_desc[:200]
    assert len(agent.name) == 200


async def test_name_falls_back_when_description_whitespace_only(
    db: asyncpg.Connection,
) -> None:
    """A whitespace-only description is treated as absent; name falls back to id."""
    await _seed_workflow(db, workflow_id="wf-ws", description="   ")
    agent = await register_agent_for_workflow(db, "wf-ws")
    assert agent is not None
    assert agent.name == "wf-ws"


async def test_register_unknown_origin_falls_back_to_greenfield(
    db: asyncpg.Connection,
) -> None:
    """An unrecognised workflow_origin maps to GREENFIELD, not an error."""
    await _seed_workflow(db, workflow_id="wf-unk", description="desc")
    await register_agent(
        db,
        workflow_id="wf-unk",
        description="desc",
        workflow_origin="future_platform",
    )
    agent = await get_agent_by_workflow(db, "wf-unk")
    assert agent is not None
    assert agent.origin is AgentOrigin.GREENFIELD


async def test_get_agent_by_workflow_returns_none_for_missing(
    db: asyncpg.Connection,
) -> None:
    """get_agent_by_workflow returns None for a workflow with no agent row."""
    assert await get_agent_by_workflow(db, "wf-no-agent") is None


async def test_fresh_agent_has_zero_metrics(db: asyncpg.Connection) -> None:
    """A newly registered agent has zero iterations, zero eval coverage, no last_iteration_at."""
    await _seed_workflow(db, workflow_id="wf-zero")
    agent = await register_agent_for_workflow(db, "wf-zero")
    assert agent is not None
    assert agent.iteration_count == 0
    assert agent.eval_coverage_count == 0
    assert agent.last_iteration_at is None
