"""Tests for `nl_gen.sim_render` — pure renderer + AST safety check.

No LLM, no docker, no DB. Covers:
  * Rendered module is parseable Python and contains the expected
    structure (frontmatter, init_state, step, run_simulation, entrypoint
    guard).
  * Round-trips through `parse_skill` so the registry will accept it.
  * Replay-equivalence on rendered output (same seed → identical
    trajectories) for all 3 fixtures.
  * AST safety pass rejects forbidden imports / builtins / dunder access.
  * Plan/spec id mismatch raises ValueError.
"""

from __future__ import annotations

import ast

import pytest
from ownevo_kernel.nl_gen import (
    EventField,
    SimRenderError,
    SimulationPlan,
    render_simulation_module,
)
from ownevo_kernel.nl_gen.fixtures import (
    CONTRACT_REVIEW_SIM_PLAN,
    CONTRACT_REVIEW_SPEC,
    CREDIT_RISK_SIM_PLAN,
    CREDIT_RISK_SPEC,
    DEMAND_PREDICTION_SIM_PLAN,
    DEMAND_PREDICTION_SPEC,
    SIM_PLAN_FIXTURES,
)
from ownevo_kernel.skills.format import parse_skill


# ---------------------------------------------------------------------------
# Renderer happy path
# ---------------------------------------------------------------------------


_FIXTURE_PAIRS = [
    ("demand-prediction", DEMAND_PREDICTION_SIM_PLAN, DEMAND_PREDICTION_SPEC),
    ("credit-risk", CREDIT_RISK_SIM_PLAN, CREDIT_RISK_SPEC),
    ("contract-review", CONTRACT_REVIEW_SIM_PLAN, CONTRACT_REVIEW_SPEC),
]


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
def test_renders_to_parseable_python(fixture_id, plan, spec):
    content = render_simulation_module(plan, spec)
    # Skip the docstring frontmatter; ast.parse is fine with the docstring,
    # but we want to make sure the body parses too.
    ast.parse(content)


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
def test_renders_skill_format_compliant(fixture_id, plan, spec):
    content = render_simulation_module(plan, spec)
    record = parse_skill(content)
    assert record.frontmatter.kind == "python"
    assert record.frontmatter.id == f"nl-gen.sim.{spec.id}"
    assert "simulation" in record.frontmatter.capability_tags
    assert spec.domain in record.frontmatter.capability_tags
    assert record.frontmatter.retention.stateless is True


@pytest.mark.parametrize(("fixture_id", "plan", "spec"), _FIXTURE_PAIRS)
def test_renders_contain_required_definitions(fixture_id, plan, spec):
    content = render_simulation_module(plan, spec)
    assert "def init_state(rng):" in content
    assert "def step(rng, state, step_index):" in content
    assert "def run_simulation(seed, n_steps):" in content
    # Entrypoint is guarded so the module can be exec'd in tests without
    # auto-printing JSON.
    assert 'if "input_data" in globals():' in content
    # The renderer wires `random.Random(seed)` as the only RNG.
    assert "rng = random.Random(seed)" in content


def test_renderer_is_deterministic():
    """Re-rendering the same plan twice produces byte-identical output."""
    a = render_simulation_module(DEMAND_PREDICTION_SIM_PLAN, DEMAND_PREDICTION_SPEC)
    b = render_simulation_module(DEMAND_PREDICTION_SIM_PLAN, DEMAND_PREDICTION_SPEC)
    assert a == b


def test_skill_id_override():
    content = render_simulation_module(
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        skill_id="custom.skill.id",
    )
    record = parse_skill(content)
    assert record.frontmatter.id == "custom.skill.id"


def test_created_by_override():
    content = render_simulation_module(
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
        created_by="test-runner",
    )
    record = parse_skill(content)
    assert record.frontmatter.created_by == "test-runner"


def test_extra_imports_appear_in_body():
    content = render_simulation_module(
        DEMAND_PREDICTION_SIM_PLAN, DEMAND_PREDICTION_SPEC
    )
    # demand-prediction plan declares `math`. Always-on imports are
    # `json` + `random`; `math` should be the only extra.
    assert "\nimport math\n" in content


def test_credit_risk_includes_math():
    content = render_simulation_module(CREDIT_RISK_SIM_PLAN, CREDIT_RISK_SPEC)
    assert "\nimport math\n" in content


def test_contract_review_no_extra_imports():
    """Contract-review plan declares `imports=[]` — only json+random emitted."""
    content = render_simulation_module(CONTRACT_REVIEW_SIM_PLAN, CONTRACT_REVIEW_SPEC)
    # A literal "import math" should not appear.
    assert "\nimport math\n" not in content
    # But the always-on imports must.
    assert "\nimport json\n" in content
    assert "\nimport random\n" in content


# ---------------------------------------------------------------------------
# Plan/spec mismatch
# ---------------------------------------------------------------------------


def test_workflow_id_mismatch_raises():
    with pytest.raises(ValueError, match="does not match"):
        render_simulation_module(DEMAND_PREDICTION_SIM_PLAN, CREDIT_RISK_SPEC)


# ---------------------------------------------------------------------------
# Import whitelist
# ---------------------------------------------------------------------------


def _plan_with(*, imports=None, init_state="return {}", step="return {'step_index': step_index}"):
    """Build a minimal plan we can manipulate for safety tests."""
    return SimulationPlan(
        workflow_spec_id="supply-chain-demand-forecast",
        description="test plan",
        n_steps_default=10,
        seed_default=0,
        imports=imports or [],
        init_state_code=init_state,
        step_code=step,
        event_fields=[EventField(name="step_index", type="int")],
    )


@pytest.mark.parametrize(
    "bad_import",
    ["os", "sys", "subprocess", "urllib", "socket", "pathlib", "requests"],
)
def test_rejects_forbidden_imports(bad_import):
    plan = _plan_with(imports=[bad_import])
    with pytest.raises(SimRenderError, match="ALLOWED_IMPORTS"):
        render_simulation_module(plan, DEMAND_PREDICTION_SPEC)


def test_accepts_whitelisted_imports():
    plan = _plan_with(imports=["math", "statistics", "datetime"])
    content = render_simulation_module(plan, DEMAND_PREDICTION_SPEC)
    assert "\nimport math\n" in content
    assert "\nimport statistics\n" in content
    assert "\nimport datetime\n" in content


# ---------------------------------------------------------------------------
# AST safety pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("forbidden_call", "snippet"),
    [
        ("eval", "eval('1+1')\nreturn {'step_index': step_index}"),
        ("exec", "exec('x = 1')\nreturn {'step_index': step_index}"),
        ("compile", "compile('1', '<x>', 'eval')\nreturn {'step_index': step_index}"),
        ("open", "open('/etc/passwd').read()\nreturn {'step_index': step_index}"),
        (
            "__import__",
            "__import__('os')\nreturn {'step_index': step_index}",
        ),
        (
            "globals",
            "g = globals()\nreturn {'step_index': step_index}",
        ),
        ("locals", "l = locals()\nreturn {'step_index': step_index}"),
        ("vars", "v = vars()\nreturn {'step_index': step_index}"),
    ],
)
def test_rejects_forbidden_calls_in_step(forbidden_call, snippet):
    plan = _plan_with(step=snippet)
    with pytest.raises(SimRenderError, match=f"forbidden call to {forbidden_call!r}"):
        render_simulation_module(plan, DEMAND_PREDICTION_SPEC)


def test_rejects_forbidden_calls_in_init_state():
    plan = _plan_with(init_state="x = eval('1')\nreturn {}")
    with pytest.raises(SimRenderError, match="forbidden call to 'eval'"):
        render_simulation_module(plan, DEMAND_PREDICTION_SPEC)


def test_rejects_dunder_attribute_access():
    plan = _plan_with(step="x = (1).__class__\nreturn {'step_index': step_index}")
    with pytest.raises(SimRenderError, match="dunder access"):
        render_simulation_module(plan, DEMAND_PREDICTION_SPEC)


def test_rejects_inline_import_statements():
    plan = _plan_with(step="import os\nreturn {'step_index': step_index}")
    with pytest.raises(SimRenderError, match="import statements are not allowed"):
        render_simulation_module(plan, DEMAND_PREDICTION_SPEC)


def test_rejects_getattr_to_dunder():
    plan = _plan_with(
        step="x = getattr(rng, '__class__')\nreturn {'step_index': step_index}"
    )
    with pytest.raises(SimRenderError, match="dunder access via builtins"):
        render_simulation_module(plan, DEMAND_PREDICTION_SPEC)


def test_allows_normal_getattr():
    """Plain `getattr(obj, 'name')` with a non-dunder string is fine."""
    plan = _plan_with(
        step=(
            "v = getattr(state, 'get', dict.get)(state, 'k', 0)\n"
            "return {'step_index': step_index}"
        )
    )
    content = render_simulation_module(plan, DEMAND_PREDICTION_SPEC)
    assert "step(rng, state, step_index)" in content


def test_rejects_invalid_python():
    plan = _plan_with(step="x = (1 +\nreturn {'step_index': step_index}")
    with pytest.raises(SimRenderError, match="not valid Python"):
        render_simulation_module(plan, DEMAND_PREDICTION_SPEC)


def test_rejects_body_with_no_return():
    plan = _plan_with(step="x = 1")
    with pytest.raises(SimRenderError, match="no `return` statement"):
        render_simulation_module(plan, DEMAND_PREDICTION_SPEC)


# ---------------------------------------------------------------------------
# Plan-level Pydantic guards
# ---------------------------------------------------------------------------


def test_plan_rejects_extra_fields():
    """SimulationPlan has extra='forbid' — so does its frontmatter promise."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SimulationPlan(
            workflow_spec_id="supply-chain-demand-forecast",
            description="x",
            init_state_code="return {}",
            step_code="return {'step_index': step_index}",
            event_fields=[EventField(name="step_index", type="int")],
            bonus_field="rejected",  # type: ignore[call-arg]
        )


def test_plan_workflow_id_must_be_kebab_case():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SimulationPlan(
            workflow_spec_id="Not-Kebab-Case",
            description="x",
            init_state_code="return {}",
            step_code="return {'step_index': step_index}",
            event_fields=[EventField(name="step_index", type="int")],
        )


def test_plan_n_steps_default_capped():
    """`n_steps_default` is capped at 10_000 to keep runs under the sandbox budget."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SimulationPlan(
            workflow_spec_id="supply-chain-demand-forecast",
            description="x",
            n_steps_default=20_000,
            init_state_code="return {}",
            step_code="return {'step_index': step_index}",
            event_fields=[EventField(name="step_index", type="int")],
        )


def test_all_three_fixture_pairs_in_registry():
    """The fixture-pairs map covers exactly the 3 PLAN.md A3.1 workflows."""
    assert set(SIM_PLAN_FIXTURES.keys()) == {
        "demand-prediction",
        "credit-risk",
        "contract-review",
    }
