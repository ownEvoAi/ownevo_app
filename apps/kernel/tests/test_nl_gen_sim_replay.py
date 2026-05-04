"""Replay-equivalence + in-process execution tests for rendered sim modules.

The PLAN.md A3.2 validation gate is two-pronged:

  1. **Replay-equivalence** — same seed → same trajectory across two runs.
  2. **End-to-end without manual fixup** for at least one of the three
     hand-picked workflows.

We exercise both here with an *in-process* exec of the rendered skill body
(stripped of frontmatter). The same module also runs unmodified in the
substrate sandbox — that path is exercised by `test_nl_gen_sim_sandbox.py`
(A3.3, gated on Docker availability) so this in-process suite keeps the
fast feedback loop green even on CI workers without Docker.

Why in-process is sound here: the rendered module's only side effect when
exec'd without `input_data` in scope is module-level constant + function
definitions. The entrypoint is guarded by `if "input_data" in globals():`.
We then call `run_simulation(seed, n_steps)` directly and assert
determinism + shape.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from ownevo_kernel.nl_gen import render_simulation_module
from ownevo_kernel.nl_gen.fixtures import (
    CONTRACT_REVIEW_SIM_PLAN,
    CONTRACT_REVIEW_SPEC,
    CREDIT_RISK_SIM_PLAN,
    CREDIT_RISK_SPEC,
    DEMAND_PREDICTION_SIM_PLAN,
    DEMAND_PREDICTION_SPEC,
)
from ownevo_kernel.skills.format import parse_skill


_FIXTURE_PAIRS = [
    ("demand-prediction", DEMAND_PREDICTION_SIM_PLAN, DEMAND_PREDICTION_SPEC),
    ("credit-risk", CREDIT_RISK_SIM_PLAN, CREDIT_RISK_SPEC),
    ("contract-review", CONTRACT_REVIEW_SIM_PLAN, CONTRACT_REVIEW_SPEC),
]


def _exec_skill_body(plan, spec) -> dict[str, Any]:
    """Render the plan to a skill, strip the frontmatter, exec the body.

    Returns the resulting module-namespace dict so the caller can pull out
    `run_simulation`, `EXPECTED_EVENT_KEYS`, etc.
    """
    content = render_simulation_module(plan, spec)
    record = parse_skill(content)
    namespace: dict[str, Any] = {}
    # `__name__` is the conventional thing to set; the renderer doesn't use
    # it, but if a future plan does (e.g. for logger names) we don't want
    # to surprise it with a default of '__main__' that triggers some
    # side-effect path.
    namespace["__name__"] = "_sim_under_test"
    exec(compile(record.body, f"<sim:{spec.id}>", "exec"), namespace)
    return namespace


# ---------------------------------------------------------------------------
# Replay-equivalence — the load-bearing A3.2 contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
def test_replay_equivalence_same_seed_same_trajectory(fixture_id, plan, spec):
    ns = _exec_skill_body(plan, spec)
    run_simulation = ns["run_simulation"]
    a = run_simulation(seed=42, n_steps=20)
    b = run_simulation(seed=42, n_steps=20)
    assert a == b
    # Stronger: the JSON serialization is byte-identical, which is the
    # contract the substrate replay nightly relies on.
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
def test_different_seeds_diverge(fixture_id, plan, spec):
    """A working sim is non-trivially seed-dependent.

    Without this check, a sim that ignores `rng` entirely would silently
    pass the replay test (every seed produces the same output). We require
    that two different seeds produce *different* trajectories.
    """
    ns = _exec_skill_body(plan, spec)
    a = ns["run_simulation"](seed=42, n_steps=20)
    b = ns["run_simulation"](seed=43, n_steps=20)
    assert a["seed"] == 42
    assert b["seed"] == 43
    assert a["trajectory"] != b["trajectory"]


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
def test_replay_across_two_module_namespaces(fixture_id, plan, spec):
    """Two fresh namespaces produce identical trajectories.

    Catches a class of bug where module-level state (a global counter, a
    cached value) leaks across runs and breaks determinism only after the
    first run. Each `_exec_skill_body` call gets a fresh namespace.
    """
    ns_a = _exec_skill_body(plan, spec)
    ns_b = _exec_skill_body(plan, spec)
    a = ns_a["run_simulation"](seed=42, n_steps=20)
    b = ns_b["run_simulation"](seed=42, n_steps=20)
    assert a == b


# ---------------------------------------------------------------------------
# End-to-end shape — every fixture runs without manual fixup.
# (PLAN.md says ≥1 of 3; we ship all 3 so the gate is well-cleared.)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
def test_trajectory_has_expected_event_keys(fixture_id, plan, spec):
    ns = _exec_skill_body(plan, spec)
    expected_keys = set(ns["EXPECTED_EVENT_KEYS"])
    declared_keys = {f.name for f in plan.event_fields}
    # Renderer's contract: EXPECTED_EVENT_KEYS comes from the plan's
    # event_fields. If this drifts, the per-step shape check is broken.
    assert expected_keys == declared_keys

    result = ns["run_simulation"](seed=plan.seed_default, n_steps=10)
    assert len(result["trajectory"]) == 10
    for event in result["trajectory"]:
        assert expected_keys <= set(event.keys())


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
def test_trajectory_envelope_shape(fixture_id, plan, spec):
    ns = _exec_skill_body(plan, spec)
    result = ns["run_simulation"](seed=plan.seed_default, n_steps=5)
    assert result["workflow_spec_id"] == spec.id
    assert result["schema_version"] == "1.0"
    assert result["seed"] == plan.seed_default
    assert result["n_steps"] == 5
    assert isinstance(result["trajectory"], list)


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
def test_step_index_is_zero_based_and_monotonic(fixture_id, plan, spec):
    ns = _exec_skill_body(plan, spec)
    result = ns["run_simulation"](seed=plan.seed_default, n_steps=15)
    indices = [e["step_index"] for e in result["trajectory"]]
    assert indices == list(range(15))


def test_demand_prediction_emits_alert_correct_label():
    """Hidden ground truth field is present on every event.

    This is what eval cases will key off in W4 — if it's missing the
    downstream pipeline silently produces unscored events.
    """
    ns = _exec_skill_body(DEMAND_PREDICTION_SIM_PLAN, DEMAND_PREDICTION_SPEC)
    result = ns["run_simulation"](seed=42, n_steps=30)
    for event in result["trajectory"]:
        assert "alert_correct_label" in event
        assert isinstance(event["alert_correct_label"], bool)


def test_credit_risk_default_label_is_bool():
    ns = _exec_skill_body(CREDIT_RISK_SIM_PLAN, CREDIT_RISK_SPEC)
    result = ns["run_simulation"](seed=7, n_steps=50)
    for event in result["trajectory"]:
        assert isinstance(event["default_label"], bool)
    # Sanity: at the default seed/length, both classes should be present.
    labels = {e["default_label"] for e in result["trajectory"]}
    assert labels == {True, False}, (
        "credit-risk sim degenerated to a single class — check the "
        "logistic-style risk function or the seed."
    )


def test_contract_review_problematic_label_is_bool():
    ns = _exec_skill_body(CONTRACT_REVIEW_SIM_PLAN, CONTRACT_REVIEW_SPEC)
    result = ns["run_simulation"](seed=13, n_steps=40)
    for event in result["trajectory"]:
        assert isinstance(event["is_problematic"], bool)
    labels = {e["is_problematic"] for e in result["trajectory"]}
    assert labels == {True, False}


# ---------------------------------------------------------------------------
# Shape-check enforcement — the renderer's per-step KeyError trip wire
# ---------------------------------------------------------------------------


def test_step_returning_missing_keys_raises_keyerror():
    """If the LLM's `step` body forgets to populate a declared field, the
    rendered loop raises KeyError on step 0 — fast and loud, not silent."""
    from ownevo_kernel.nl_gen import EventField, SimulationPlan

    plan = SimulationPlan(
        workflow_spec_id="supply-chain-demand-forecast",
        description="bad plan that omits a declared field",
        n_steps_default=5,
        seed_default=0,
        imports=[],
        init_state_code="return {}",
        step_code="return {'step_index': step_index}",
        event_fields=[
            EventField(name="step_index", type="int"),
            EventField(name="missing_field", type="str"),
        ],
    )
    ns = _exec_skill_body(plan, DEMAND_PREDICTION_SPEC)
    with pytest.raises(KeyError, match="missing_field"):
        ns["run_simulation"](seed=0, n_steps=1)


def test_step_returning_non_dict_raises_typeerror():
    from ownevo_kernel.nl_gen import EventField, SimulationPlan

    plan = SimulationPlan(
        workflow_spec_id="supply-chain-demand-forecast",
        description="bad plan that returns a list",
        n_steps_default=5,
        seed_default=0,
        imports=[],
        init_state_code="return {}",
        step_code="return [step_index]",
        event_fields=[EventField(name="step_index", type="int")],
    )
    ns = _exec_skill_body(plan, DEMAND_PREDICTION_SPEC)
    with pytest.raises(TypeError, match="must return a dict"):
        ns["run_simulation"](seed=0, n_steps=1)
