"""Per-kind apply logic for non-skill proposals.

Skill proposals point at a `skill_versions` row and a deploy step
flips `skills.deployed_version_id` to that row. Non-skill artifact
proposals (description / metric / sim / ui-primitive) update the
workflow row directly on approval — there's no separate version
table to point at and no separate deploy step.

These helpers run inside the approve_proposal transaction, after
the state row update and before the audit entry. A failure to apply
the change raises, the transaction rolls back, and the proposal
stays in `gate-passed` so the reviewer can retry or reject.

Two "sims" — keep them straight
-------------------------------
The workflow row stores TWO sim-shaped artifacts and they serve
different roles:

  * `workflows.spec` — the agent's runtime environment: tools the
    agent can call, user-simulator personas, env_generators, and
    data_sources. Editable via `kind='sim'` proposals (this module).
    Surfaced in the UI as "Agent environment" on the Spec tab.

  * `workflows.simulation_plan` — the REPLAY sim: deterministic
    `run_simulation(seed, n_steps) → trajectory` Python module
    (init_state_code / step_code / event_fields). Eval cases bind
    to its trajectories — every case is a (seed, step_index,
    label_field, expected_value) pointer into one. The replay sim
    has NO proposal kind and CANNOT be edited in place by this
    module. Two writers exist in the codebase: (a) NL-gen at
    workflow creation, and (b) `POST /api/workflows/{id}/eval-cases
    /generate`, which atomically rewrites `simulation_plan` AND the
    eval cases together — so case-to-sim binding stays consistent.
    No other path may write this column.

The split matters because the replay sim pins eval-case validity.
Editing it in isolation would invalidate cases (their
`expected_value` was recorded against the old trajectory at that
seed). The `/eval-cases/generate` route gets away with rewriting it
only because it regenerates the cases in the same transaction. The
agent environment is safe to edit — adding/removing tools or
personas changes the agent's behaviour against the same cases,
which is exactly what the regression gate is for.

If we ever expose the replay sim for editing, we need a
`simulation_plan_versions` table + per-eval-case `sim_version_id`
FK + a replay-on-apply migration step (re-run each case against
the proposed sim, drop/quarantine/re-label as needed). Until then
`apply_sim_proposal` must NEVER write to `workflows.simulation_plan`
— see the invariant test in tests/test_apply_sim_invariant.py.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg


# Per-kind apply dispatch returns the brief audit-ready summary of
# what changed. Same shape for every kind so the approve_proposal
# audit entry can carry it generically.


async def apply_description_proposal(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Replace `workflows.description` with the proposed text."""
    new_text = payload.get("description")
    if not isinstance(new_text, str) or not new_text.strip():
        raise ValueError(
            "description proposal payload missing non-empty `description`"
        )
    previous = await conn.fetchval(
        "SELECT description FROM workflows WHERE id = $1",
        workflow_id,
    )
    await conn.execute(
        "UPDATE workflows SET description = $1 WHERE id = $2",
        new_text,
        workflow_id,
    )
    return {
        "applied_kind": "description",
        "char_delta": len(new_text) - len(previous or ""),
    }


async def apply_metric_proposal(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Replace `workflows.metric_definition` with the proposed shape.

    The form-supplied payload is loose (name + family + direction +
    description). The full MetricDefinition is built by merging the
    payload onto the workflow's current metric_definition so required
    schema fields (workflow_spec_id, bounds, target_value) survive.
    The 'higher' / 'lower' direction labels from the form map to the
    schema's 'maximize' / 'minimize'.
    """
    current_raw = await conn.fetchval(
        "SELECT metric_definition FROM workflows WHERE id = $1",
        workflow_id,
    )
    current: dict[str, Any] = {}
    if isinstance(current_raw, str):
        try:
            current = json.loads(current_raw)
        except (ValueError, TypeError):
            current = {}
    elif isinstance(current_raw, dict):
        current = current_raw

    direction_alias = {
        "higher": "maximize",
        "lower": "minimize",
        "maximize": "maximize",
        "minimize": "minimize",
    }

    merged = dict(current) if current else {}
    for key in ("name", "family", "direction", "description"):
        v = payload.get(key)
        if v is None:
            continue
        if key == "direction":
            v = direction_alias.get(str(v), str(v))
        merged[key] = v
    # Preserve the workflow_spec_id linkage; if the current was empty,
    # set it to the workflow id so the gate's spec-check stays honest.
    merged.setdefault("workflow_spec_id", workflow_id)
    merged.setdefault("schema_version", "0.1")

    await conn.execute(
        "UPDATE workflows SET metric_definition = $1::jsonb WHERE id = $2",
        json.dumps(merged),
        workflow_id,
    )
    return {
        "applied_kind": "metric",
        "metric_name": merged.get("name"),
        "metric_family": merged.get("family"),
    }


async def apply_ui_primitive_proposal(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Replace `spec.ui.tabs[0].primitives` with the proposed list.

    `payload['primitives']` is the new list. The spec's other shape
    (tools, personas, environment, etc.) is left untouched.
    """
    new_primitives = payload.get("primitives")
    if not isinstance(new_primitives, list):
        raise ValueError(
            "ui-primitive proposal payload missing list `primitives`"
        )

    spec = await _read_spec(conn, workflow_id)
    ui = spec.get("ui")
    if not isinstance(ui, dict):
        ui = {"tabs": [{}]}
        spec["ui"] = ui
    tabs = ui.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        tabs = [{}]
        ui["tabs"] = tabs
    if not isinstance(tabs[0], dict):
        tabs[0] = {}
    tabs[0]["primitives"] = new_primitives

    await _write_spec(conn, workflow_id, spec)
    return {
        "applied_kind": "ui-primitive",
        "primitive_count": len(new_primitives),
        "primitive_types": [
            p.get("type") for p in new_primitives if isinstance(p, dict)
        ],
    }


async def apply_sim_proposal(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Merge sim sections into the workflow's `spec`.

    Replaces whichever of (tools / personas / env_generators /
    data_sources) the payload carries. Sections not in the payload are
    left untouched. Personas / env_generators / data_sources live
    under `spec.environment`; tools is a top-level spec field.
    """
    spec = await _read_spec(conn, workflow_id)

    if "tools" in payload:
        spec["tools"] = payload["tools"]

    env_keys = ("personas", "env_generators", "data_sources")
    if any(k in payload for k in env_keys):
        env = spec.get("environment")
        if not isinstance(env, dict):
            env = {}
            spec["environment"] = env
        for k in env_keys:
            if k in payload:
                env[k] = payload[k]

    await _write_spec(conn, workflow_id, spec)
    applied: dict[str, Any] = {"applied_kind": "sim"}
    if "tools" in payload and isinstance(payload["tools"], list):
        applied["tool_count"] = len(payload["tools"])
    for k in env_keys:
        if k in payload and isinstance(payload[k], list):
            applied[f"{k}_count"] = len(payload[k])
    return applied


async def _read_spec(conn: asyncpg.Connection, workflow_id: str) -> dict[str, Any]:
    raw = await conn.fetchval(
        "SELECT spec FROM workflows WHERE id = $1",
        workflow_id,
    )
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = {}
    elif isinstance(raw, dict):
        parsed = raw
    else:
        parsed = {}
    return parsed


async def _write_spec(
    conn: asyncpg.Connection, workflow_id: str, spec: dict[str, Any],
) -> None:
    await conn.execute(
        "UPDATE workflows SET spec = $1::jsonb WHERE id = $2",
        json.dumps(spec),
        workflow_id,
    )


# Dispatch table — kind → apply coroutine. The HTTP route looks up by
# kind to delegate; an unknown kind is a programming error and would
# have been blocked at the create endpoint.
APPLY_BY_KIND = {
    "description": apply_description_proposal,
    "metric": apply_metric_proposal,
    "ui-primitive": apply_ui_primitive_proposal,
    "sim": apply_sim_proposal,
}
