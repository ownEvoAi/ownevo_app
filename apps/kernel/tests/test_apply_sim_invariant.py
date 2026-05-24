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

  1. Source-level (always-on, AST scan) — walk `approvals/apply.py`
     and flag any string-literal argument to an asyncpg call method
     (execute / fetchval / etc.) that contains `simulation_plan`.
     Whole-module scope so a refactor that pushes the write into a
     helper can't bypass the invariant. Whitespace-tokenized so the
     future `simulation_plan_versions` table is allowed.

  2. Behavioural (db-gated) — seed a workflow with a known
     `simulation_plan`, run a fully-populated sim payload through the
     apply path, assert the column is byte-identical afterwards.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import asyncpg
import pytest
from ownevo_kernel.approvals import apply as apply_module
from ownevo_kernel.approvals.apply import apply_sim_proposal
from ownevo_kernel.db import ENV_VAR

# Method names through which a write could plausibly leave this module.
# asyncpg's connection API plus the handful of wrappers we have.
_DB_CALL_METHODS = frozenset(
    {"execute", "executemany", "fetch", "fetchrow", "fetchval", "fetchmany"}
)


def test_apply_module_db_calls_do_not_reference_simulation_plan() -> None:
    """Static guard: every database call site in `approvals/apply.py`
    must avoid the `simulation_plan` column.

    Scans the WHOLE module (not just `apply_sim_proposal`'s body) so a
    refactor that pushes the write into a helper can't bypass the
    invariant. Scans only the *arguments to DB call methods* — comments,
    docstrings, and prose mentioning `simulation_plan` are fine.

    The check is: for every `<obj>.<method>(...)` where method is one of
    asyncpg's read/write entry points, none of the string-literal
    arguments may contain the column name.

    Why string-literal scan, not raw substring? Because SQL writers
    smuggle the column name as a literal. Identifier-level analysis
    would miss e.g. `await conn.execute("UPDATE workflows SET
    simulation_plan = $1 ...")`. We allow `simulation_plan_versions`
    (a future versioning table) by tokenizing on whitespace.
    """
    src = Path(apply_module.__file__).read_text()
    tree = ast.parse(src)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr in _DB_CALL_METHODS):
            continue
        for arg in (*node.args, *(kw.value for kw in node.keywords)):
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            if "simulation_plan" not in arg.value:
                continue
            # Tokenize on whitespace so `simulation_plan_versions` (a
            # future versioning table) doesn't trip the check; the bare
            # `simulation_plan` column name DOES.
            tokens = arg.value.replace("\n", " ").split()
            bad = [
                t for t in tokens
                if "simulation_plan" in t and not t.startswith("simulation_plan_versions")
            ]
            if bad:
                offenders.append(
                    f"DB call `.{node.func.attr}(...)` at line {node.lineno} "
                    f"references {bad}"
                )

    assert not offenders, (
        "approvals/apply.py has a DB call referencing `simulation_plan` — "
        "no proposal-apply path may write to that column. Editing "
        "simulation_plan would invalidate every eval case bound to the "
        "prior trajectory. See the module docstring. Offenders:\n  "
        + "\n  ".join(offenders)
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
