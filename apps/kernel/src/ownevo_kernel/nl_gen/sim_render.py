"""Pure renderer: SimulationPlan + WorkflowSpec → SKILL_FORMAT Python module.

No LLM, no I/O — string templating + AST validation. Deterministic so
re-rendering the same plan twice produces byte-identical output.

Two safety layers:

  1. **Import whitelist** — the plan's `imports` field must be a subset of
     `sim_plan.ALLOWED_IMPORTS`. Rejected at render time.
  2. **AST safety check** — `init_state_code` and `step_code` are parsed
     with `ast.parse` and walked; any `Import`/`ImportFrom` (modules cannot
     re-import inside function bodies), any `Call` to a forbidden name OR any
     bare `Name` reference to a forbidden name (`eval`, `exec`, `compile`,
     `open`, `__import__`, `globals`, `locals`, `vars`,
     `getattr`/`setattr`/`delattr`/`hasattr` against dunder targets — blocking
     name references prevents `_f = exec; _f(...)` bypass), and any `Attribute`
     access whose name starts with `__` are rejected.

The rendered module is structured so that:

  * `init_state` and `step` are pure functions of the passed `rng`.
  * `random.Random(seed)` is the only RNG instantiated; a fresh instance is
    constructed at each `run_simulation(seed, ...)` call so two invocations
    with the same seed produce byte-identical trajectories.
  * The skill body's entrypoint is guarded by `if "input_data" in globals():`
    so the rendered module can be `exec`-ed for testing without invoking the
    JSON-print branch.

This file is the trust boundary. If the LLM emits something subtle, the
sandbox is the next defence (A3.3) — but we want the obvious cases to fail
loudly here so iteration stays cheap.
"""

from __future__ import annotations

import ast
from typing import Iterable

from .sim_plan import ALLOWED_IMPORTS, SimulationPlan
from .spec import WorkflowSpec


class SimRenderError(ValueError):
    """Plan failed safety / shape validation at render time.

    Distinct from `SimulationPlanValidationError` (Pydantic-level shape
    failures from the LLM) — `SimRenderError` covers semantic checks
    that Pydantic can't express: AST safety, import whitelist, syntactic
    well-formedness of the function bodies.
    """


_FORBIDDEN_CALL_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "input",
        "breakpoint",
        "exit",
        "quit",
        # Blanket-banned: all four accept an arbitrary name string that the AST
        # check cannot verify statically (a variable holding a dunder string
        # bypasses the constant-check). Pure numeric/dict sim logic never needs
        # them; the sandbox (A3.3) is the next barrier if something slips through.
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
    }
)
"""Builtin call targets the sim is not allowed to invoke."""


def _ast_safety_check(source: str, *, where: str) -> None:
    """Walk `source`'s AST and raise SimRenderError for unsafe nodes.

    `where` is a label for the error message (e.g. "init_state_code").
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SimRenderError(
            f"{where}: not valid Python ({exc.msg} at line {exc.lineno})"
        ) from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise SimRenderError(
                f"{where}: import statements are not allowed inside the function "
                f"body (declare imports in SimulationPlan.imports instead). "
                f"Got: {ast.unparse(node)!r}"
            )
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise SimRenderError(
                f"{where}: global/nonlocal statements are not allowed — they "
                f"corrupt the shared namespace in replay_set. "
                f"Got: {ast.unparse(node)!r}"
            )
        if isinstance(node, ast.Name):
            if node.id.startswith("__") and node.id.endswith("__"):
                raise SimRenderError(
                    f"{where}: dunder name {node.id!r} is not allowed — "
                    f"use of __builtins__ or similar enables sandbox escape."
                )
            if node.id in _FORBIDDEN_CALL_NAMES:
                raise SimRenderError(
                    f"{where}: forbidden name {node.id!r} is not allowed — "
                    f"assigning builtins to variables bypasses the call check "
                    f"(e.g. `_f = exec; _f(...)`). "
                    f"Rejected: {sorted(_FORBIDDEN_CALL_NAMES)}"
                )
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Subscript):
                raise SimRenderError(
                    f"{where}: subscript-based call blocked "
                    f"(e.g. __builtins__['__import__']('os') bypasses name checks)"
                )
            name = (
                target.id if isinstance(target, ast.Name)
                else target.attr if isinstance(target, ast.Attribute)
                else None
            )
            if name in _FORBIDDEN_CALL_NAMES:
                raise SimRenderError(
                    f"{where}: forbidden call to {name!r} "
                    f"(rejected: {_FORBIDDEN_CALL_NAMES})"
                )
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                # Allow `__name__` (module check) and `__doc__` reads — the
                # sim should not need either, but they're harmless.
                if node.attr not in {"__name__", "__doc__"}:
                    raise SimRenderError(
                        f"{where}: attribute access {node.attr!r} blocked — "
                        "dunder access is not allowed."
                    )


def _validate_imports(imports: Iterable[str]) -> None:
    bad = [m for m in imports if m not in ALLOWED_IMPORTS]
    if bad:
        raise SimRenderError(
            f"imports {bad!r} are not in ALLOWED_IMPORTS "
            f"({sorted(ALLOWED_IMPORTS)})"
        )


def _ensure_returns(body: str, *, where: str) -> None:
    """Cheap structural check: the function body must have a `return`."""
    tree = ast.parse(body)
    has_return = any(isinstance(n, ast.Return) for n in ast.walk(tree))
    if not has_return:
        raise SimRenderError(
            f"{where}: function body has no `return` statement; "
            "sim functions must return a value."
        )


def _indent(body: str, level: int) -> str:
    """Indent every line by `level` 4-space units, ignoring blank lines."""
    pad = " " * (level * 4)
    out: list[str] = []
    for line in body.splitlines():
        if line.strip():
            out.append(pad + line)
        else:
            out.append("")
    return "\n".join(out)


def render_simulation_module(
    plan: SimulationPlan,
    workflow_spec: WorkflowSpec,
    *,
    skill_id: str | None = None,
    created_by: str = "nl-gen/sim_generator",
) -> str:
    """Render a SimulationPlan into a SKILL_FORMAT-compliant Python skill.

    Args:
        plan: The SimulationPlan to render. Must reference the same
            workflow as `workflow_spec` (we cross-check `workflow_spec_id`).
        workflow_spec: The originating WorkflowSpec. Used for the skill's
            capability_tags (domain) and the cross-check above.
        skill_id: Optional override for the skill's `id` field. Defaults to
            `nl-gen.sim.<workflow_spec.id>`.
        created_by: Frontmatter `created_by`. Defaults to the generator's name.

    Returns:
        Canonical skill content (frontmatter docstring + body) ready for
        `register_skill` and runnable end-to-end via `run_pipeline`.

    Raises:
        SimRenderError: imports off the whitelist, syntax error in a body,
            forbidden builtin/dunder access, or a body with no `return`.
        ValueError: plan.workflow_spec_id != workflow_spec.id.
    """
    if plan.workflow_spec_id != workflow_spec.id:
        raise ValueError(
            f"plan.workflow_spec_id={plan.workflow_spec_id!r} does not match "
            f"workflow_spec.id={workflow_spec.id!r}"
        )

    _validate_imports(plan.imports)
    _ast_safety_check(plan.init_state_code, where="init_state_code")
    _ast_safety_check(plan.step_code, where="step_code")
    _ensure_returns(plan.init_state_code, where="init_state_code")
    _ensure_returns(plan.step_code, where="step_code")

    sid = skill_id or f"nl-gen.sim.{workflow_spec.id}"

    # Frontmatter is built directly so the literal output here is what we test
    # against. We could route through `build_skill_content` but pinning the
    # exact bytes makes round-trip tests stricter.
    frontmatter_lines = [
        '"""',
        "---",
        f"id: {sid}",
        "kind: python",
        f"created_by: {created_by}",
        "capability_tags:",
        "  - simulation",
        f"  - {workflow_spec.domain}",
        "retention:",
        "  stateless: true",
        "---",
        '"""',
    ]
    frontmatter = "\n".join(frontmatter_lines)

    extra_imports = sorted(set(plan.imports) - {"json", "random", "__future__"})
    extra_import_lines = [f"import {m}" for m in extra_imports]

    init_body = _indent(plan.init_state_code.rstrip(), 1)
    step_body = _indent(plan.step_code.rstrip(), 1)

    expected_keys = ", ".join(repr(f.name) for f in plan.event_fields)

    # No `from __future__ import annotations` here — `run_pipeline` prepends
    # a prologue (`import json as _ownevo_json; input_data = ...`) before
    # this skill body when the skill executes in the sandbox, so a future-
    # import would no longer be at file start and Python would reject it
    # with SyntaxError. The rendered functions don't use forward-reference
    # type hints anyway, so we don't need it.
    body = f'''\
# Generated by ownevo_kernel.nl_gen.sim_generator from
# WorkflowSpec id={workflow_spec.id!r}, domain={workflow_spec.domain!r}.
# Do not hand-edit. Re-run sim_generator to regenerate.
#
# Description: {plan.description}

import json
import random
{chr(10).join(extra_import_lines)}

WORKFLOW_SPEC_ID = {workflow_spec.id!r}
WORKFLOW_DOMAIN = {workflow_spec.domain!r}
SCHEMA_VERSION = {plan.schema_version!r}
N_STEPS_DEFAULT = {plan.n_steps_default}
SEED_DEFAULT = {plan.seed_default}
EXPECTED_EVENT_KEYS = ({expected_keys},)


def init_state(rng):
    """Construct the simulator's initial state from the RNG."""
{init_body}


def step(rng, state, step_index):
    """Advance one step; return one event dict."""
{step_body}


def run_simulation(seed, n_steps):
    """Run the sim deterministically and return the trajectory.

    A fresh `random.Random(seed)` is constructed each call so two invocations
    with the same seed produce byte-identical trajectories regardless of
    whatever else has run in the same Python process.
    """
    rng = random.Random(seed)
    state = init_state(rng)
    trajectory = []
    expected = set(EXPECTED_EVENT_KEYS)
    for i in range(n_steps):
        event = step(rng, state, i)
        if not isinstance(event, dict):
            raise TypeError(
                f"step(rng, state, {{i}}) must return a dict; got {{type(event).__name__}}"
            )
        missing = expected - set(event.keys())
        if missing:
            raise KeyError(
                f"step(rng, state, {{i}}) returned event missing keys: "
                f"{{sorted(missing)}}"
            )
        trajectory.append(event)
    return {{
        "workflow_spec_id": WORKFLOW_SPEC_ID,
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "n_steps": n_steps,
        "trajectory": trajectory,
    }}


# Skill entrypoint: invoked by `run_pipeline` with `input_data` injected as a
# module-level global. The guard lets tests `exec` or import the module
# without auto-invoking the JSON-print branch.
if "input_data" in globals():
    _seed = int(input_data.get("seed", SEED_DEFAULT))  # noqa: F821
    _n_steps = int(input_data.get("n_steps", N_STEPS_DEFAULT))  # noqa: F821
    _result = run_simulation(_seed, _n_steps)
    print(json.dumps(_result))
'''

    return f"{frontmatter}\n\n{body}"


__all__ = [
    "SimRenderError",
    "render_simulation_module",
]
