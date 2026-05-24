"""Invariant: `apply_sim_proposal` must never touch `workflows.simulation_plan`.

See `approvals/apply.py` module docstring for the architectural
background. The short version: `spec` is the agent's runtime environment
(tools / personas / data_sources / env_generators) — editable through
proposals. `simulation_plan` is the deterministic replay sim that eval
cases bind to (init_state_code / step_code / event_fields) — frozen at
NL-gen time, no UI surface. The 'sim' proposal kind is a naming legacy
from before that split was clear; it only edits `spec`.

If anyone wires a UI for editing the replay sim later, they need a
`simulation_plan_versions` table + per-eval-case sim_version_id FK +
replay-on-apply migration. Until that exists, accidentally writing to
`workflows.simulation_plan` from this code path would silently
invalidate every eval case bound to the prior trajectory — the gate
would still pass/fail, but its verdict against historic cases would no
longer be comparable.

Two layers of guard here:

  1. Source-level — assert `apply_sim_proposal`'s body doesn't reference
     `simulation_plan`. Cheap, always-on, catches the obvious accident.

  2. Behavioural (db-gated) — seed a workflow with a known
     `simulation_plan`, run a fully-populated sim payload through the
     apply path, assert the column is byte-identical afterwards.
"""

from __future__ import annotations

import inspect
import json
import os

import asyncpg
import pytest
from ownevo_kernel.approvals.apply import apply_sim_proposal
from ownevo_kernel.db import ENV_VAR


def test_apply_sim_source_does_not_mention_simulation_plan() -> None:
    """Static guard: the apply function's source must not reference
    `simulation_plan`. Catches the case where someone wires a write to
    that column without realizing it breaks eval-case validity.
    """
    src = inspect.getsource(apply_sim_proposal)
    assert "simulation_plan" not in src, (
        "apply_sim_proposal references `simulation_plan` — this function "
        "must only mutate workflows.spec. Editing simulation_plan would "
        "invalidate every eval case bound to the prior trajectory. See "
        "the module docstring in approvals/apply.py."
    )


@pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping db-backed invariant check",
)
async def test_apply_sim_leaves_simulation_plan_untouched(
    db: asyncpg.Connection,
) -> None:
    """Behavioural guard: seed simulation_plan, apply a full sim
    payload, assert the column round-trips byte-identical.

    Exercises every payload key apply_sim_proposal recognises (tools +
    personas + env_generators + data_sources) so a future code change
    that conditionally touches simulation_plan in one branch can't slip
    through by virtue of an under-exercised payload shape.
    """
    workflow_id = "wf-sim-invariant"
    seeded_plan = {
        "schema_version": "1.0",
        "workflow_spec_id": workflow_id,
        "description": "fixed replay sim — must not change",
        "n_steps_default": 50,
        "seed_default": 7,
        "imports": [],
        "init_state_code": "return {'counter': 0}",
        "step_code": "return {'event': step_index}",
        "event_fields": [{"name": "event", "type": "int", "description": ""}],
    }
    await db.execute(
        """
        INSERT INTO workflows (id, description, spec, simulation_plan)
        VALUES ($1, 'invariant test', $2::jsonb, $3::jsonb)
        """,
        workflow_id,
        json.dumps({"tools": [], "environment": {}}),
        json.dumps(seeded_plan),
    )

    payload = {
        "tools": [{"name": "lookup_x", "inputs": []}],
        "personas": [{"role": "buyer", "description": "test"}],
        "env_generators": [{"name": "weekly_demand", "description": "test"}],
        "data_sources": [{"id": "ds-1", "entity": "sku", "description": "test"}],
    }
    summary = await apply_sim_proposal(
        db, workflow_id=workflow_id, payload=payload,
    )
    assert summary["applied_kind"] == "sim"

    after = await db.fetchval(
        "SELECT simulation_plan FROM workflows WHERE id = $1",
        workflow_id,
    )
    after_parsed = json.loads(after) if isinstance(after, str) else after
    assert after_parsed == seeded_plan, (
        "apply_sim_proposal mutated workflows.simulation_plan — every eval "
        "case bound to the prior trajectory is now invalidated."
    )

    spec_after = await db.fetchval(
        "SELECT spec FROM workflows WHERE id = $1", workflow_id,
    )
    spec_parsed = json.loads(spec_after) if isinstance(spec_after, str) else spec_after
    assert spec_parsed["tools"] == payload["tools"]
    assert spec_parsed["environment"]["personas"] == payload["personas"]
    assert spec_parsed["environment"]["env_generators"] == payload["env_generators"]
    assert spec_parsed["environment"]["data_sources"] == payload["data_sources"]
