"""Eval-case replay (A4.1).

Run a `GeneratedEvalCase` against a `SimulationPlan` and decide pass/fail.
This is the deterministic seam between the eval-case generator (A4.1) and
the regression gate. Inspect AI integration (A4.3) will plug in here later;
for A4.1 the helper is just enough to pin replay-equivalence in tests and
to power the persistence layer's smoke tests.

How it works:

  1. `sim_render.render_simulation_module(plan, spec)` produces the canonical
     skill content. We only need the body — the frontmatter is for the skill
     registry, not for in-process replay.
  2. The body is `exec`-ed in a fresh namespace (no `input_data` global, so
     the `if "input_data" in globals():` guard at the bottom does nothing).
  3. `run_simulation(case.sim_seed, case.n_steps)` returns the trajectory.
  4. We read `trajectory[case.target_step_index][case.target_label_field]`
     and compare against `case.expected_value`.

A fresh namespace per replay is the easy way to keep replays independent.
The rendered module already constructs a fresh `random.Random(seed)` per
`run_simulation` call, so determinism is structural; the namespace
isolation just keeps any module-level state (none today, but defensive
against future renderer changes) from bleeding across cases.

This path stays in-process and is `O(n_steps)` per case. The sandboxed
path (`run_pipeline`) is what the live sandbox / Inspect AI integration
will use; A4.1 doesn't need it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ownevo_kernel.skills.format import parse_skill

from .eval_case_set import EvalCaseSet, GeneratedEvalCase
from .sim_plan import EventField, SimulationPlan
from .sim_render import render_simulation_module
from .spec import WorkflowSpec


class EvalReplayError(ValueError):
    """Replay surfaced a structural problem the case can't recover from.

    Distinct from a normal pass/fail: a `ReplayResult` with `passed=False`
    means the sim produced an event whose label disagreed with the case's
    `expected_value` (legitimate eval signal). `EvalReplayError` means the
    case is structurally broken — it targets a label_field the sim never
    emits, or a step_index past the trajectory's end. These are bugs in
    the case, not gate signal.
    """


@dataclass(frozen=True)
class ReplayResult:
    """Outcome of replaying one eval case.

    `passed` is the gate-signal bit. `actual_value` is the value the sim
    actually produced at the targeted event — useful for the audit trail
    so a reviewer can see exactly what disagreed.

    `rationale` is the agent's per-case explanation (populated by
    `run_with_agent`; None when the replay was deterministic via the
    sim path because there's no agent to explain a sim's output).
    """

    case_id: str
    passed: bool
    actual_value: Any
    expected_value: Any
    rationale: str | None = None


def _bool_label_fields(plan: SimulationPlan) -> dict[str, EventField]:
    """Map of bool-typed event_fields names → EventField on `plan`.

    Eval cases must target one of these — any other type would silently
    coerce in `==` comparisons and let typo'd cases pass. This is the
    set the replay helper validates against.
    """
    return {f.name: f for f in plan.event_fields if f.type == "bool"}


def exec_sim_module(
    plan: SimulationPlan, spec: WorkflowSpec, *, caller: str = "sim"
) -> dict[str, Any]:
    """Render + exec the sim module via render_simulation_module; return its namespace.

    Shared between eval_replay and agent_solver — both paths go through the same
    AST safety gate inside render_simulation_module. `caller` labels the namespace
    and compile filename for debuggable tracebacks (default "sim" when caller
    doesn't matter).
    """
    content = render_simulation_module(plan, spec)
    record = parse_skill(content)
    ns: dict[str, Any] = {"__name__": f"_{caller}_sim"}
    exec(compile(record.body, f"<{caller}:{spec.id}>", "exec"), ns)
    return ns


def replay_case(
    case: GeneratedEvalCase,
    plan: SimulationPlan,
    spec: WorkflowSpec,
    *,
    namespace: dict[str, Any] | None = None,
) -> ReplayResult:
    """Replay one case against the rendered sim and return pass/fail.

    Args:
        case: The case to replay.
        plan: SimulationPlan whose trajectory the case targets.
        spec: WorkflowSpec the plan was rendered against (the renderer
            cross-checks `plan.workflow_spec_id == spec.id`).
        namespace: Optional pre-exec'd module namespace. When replaying
            many cases at once, exec the module once and reuse the
            namespace to skip the AST safety pass + compile cost.

    Returns:
        A `ReplayResult` carrying `passed`, `actual_value`, `expected_value`.

    Raises:
        EvalReplayError: case targets a non-bool label_field, a label_field
            absent from the plan, or a step_index past the trajectory's end.
    """
    bool_fields = _bool_label_fields(plan)
    if case.target_label_field not in bool_fields:
        raise EvalReplayError(
            f"case {case.case_id!r}: target_label_field="
            f"{case.target_label_field!r} is not a bool-typed event_field "
            f"on the SimulationPlan (bool fields: "
            f"{sorted(bool_fields)})"
        )

    ns = namespace if namespace is not None else exec_sim_module(plan, spec, caller="eval-replay")
    try:
        run_simulation = ns["run_simulation"]
    except KeyError:
        raise EvalReplayError(
            f"case {case.case_id!r}: namespace missing 'run_simulation' — "
            "the sim module may not have been exec'd correctly"
        )
    try:
        result = run_simulation(case.sim_seed, case.n_steps)
        trajectory = result["trajectory"]
    except Exception as exc:
        raise EvalReplayError(
            f"case {case.case_id!r}: sim execution failed — {type(exc).__name__}: {exc}"
        ) from exc

    if case.target_step_index >= len(trajectory):
        raise EvalReplayError(
            f"case {case.case_id!r}: target_step_index="
            f"{case.target_step_index} is past the trajectory length "
            f"({len(trajectory)})"
        )

    event = trajectory[case.target_step_index]
    try:
        actual = event[case.target_label_field]
    except KeyError:
        raise EvalReplayError(
            f"case {case.case_id!r}: target_label_field="
            f"{case.target_label_field!r} not found in trajectory event at "
            f"step {case.target_step_index} — sim emitted keys: "
            f"{sorted(event.keys()) if isinstance(event, dict) else repr(event)}"
        )
    if not isinstance(actual, bool):
        raise EvalReplayError(
            f"case {case.case_id!r}: target_label_field="
            f"{case.target_label_field!r} returned {type(actual).__name__!r} "
            f"({actual!r}), expected bool — sim must emit True/False, not 1/0"
        )
    return ReplayResult(
        case_id=case.case_id,
        passed=actual == case.expected_value,
        actual_value=actual,
        expected_value=case.expected_value,
    )


def replay_set(
    case_set: EvalCaseSet,
    plan: SimulationPlan,
    spec: WorkflowSpec,
) -> list[ReplayResult]:
    """Replay every case in a set against one rendered sim.

    Execs the sim module once and reuses the namespace across cases.
    Returns results in the same order as `case_set.cases`.
    """
    if case_set.workflow_spec_id != spec.id:
        raise ValueError(
            f"case_set.workflow_spec_id={case_set.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}"
        )
    if plan.workflow_spec_id != spec.id:
        raise ValueError(
            f"plan.workflow_spec_id={plan.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}"
        )

    ns = exec_sim_module(plan, spec, caller="eval-replay")
    return [replay_case(c, plan, spec, namespace=ns) for c in case_set.cases]


__all__ = [
    "EvalReplayError",
    "ReplayResult",
    "exec_sim_module",
    "replay_case",
    "replay_set",
]
