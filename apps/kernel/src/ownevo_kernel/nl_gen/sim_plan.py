"""SimulationPlan — typed schema for the A3.2 NL-gen artifact.

The simulator-generation step takes a `WorkflowSpec` (A3.1) and emits a
`SimulationPlan` describing the deterministic Python sim that drives the
workflow's environment. The plan is the LLM-fillable artifact; the renderer
(`sim_render.render_simulation_module`) turns it into a SKILL_FORMAT-compliant
Python module that runs unchanged inside the substrate sandbox (A3.3).

The plan deliberately constrains LLM output to two function bodies (`init_state`
and `step`) plus a small whitelist of imports. Determinism is guaranteed at the
template layer — the renderer wires `random.Random(seed)` as the only RNG, and
the AST safety pass in `sim_render` rejects any code that imports off the
whitelist or reaches for `os` / `subprocess` / `eval` / `__import__` / etc.

**Frozen at `schema_version: "1.0"` per the A3.4 ritual at end of W3
(2026-05-04, tag `v1.0-frozen-2026-W3`).** Structural changes are caught
by `tests/test_nl_gen_schema_freeze.py` against the snapshot at
`nl_gen/schemas/simulation_plan.v1.0.json` — same rule as `WorkflowSpec`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"
"""Frozen at A3.4 (2026-05-04, end of W3) per docs/PLAN.md schema-freeze.

Tag: `v1.0-frozen-2026-W3`. Structural drift detected by
`tests/test_nl_gen_schema_freeze.py` against the snapshot at
`nl_gen/schemas/simulation_plan.v1.0.json`. To intentionally change the
schema, bump this constant + regenerate via `scripts/regen_nl_gen_schemas.py`."""

ALLOWED_IMPORTS = frozenset(
    {"random", "math", "statistics", "datetime", "json"}
)
"""Stdlib modules the generated sim is allowed to import.

`random` is the only RNG source — the renderer threads `random.Random(seed)`
through both `init_state` and `step` so determinism is structural, not a
property the LLM has to maintain. `math` / `statistics` are needed for
seasonality (sin / cos / mean), `datetime` for date-stamped events, `json`
for the entrypoint that serializes the trajectory.

`__future__` is intentionally excluded: `run_pipeline` prepends a 2-line
prologue before the skill body so a `from __future__` line would no longer be
at file start and Python would reject it with SyntaxError.

Everything else (os, sys, subprocess, urllib, socket, pathlib, requests,
numpy, pandas, ...) is rejected by `sim_render._ast_safety_check`."""


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventField(_Base):
    """One named field on the trajectory event the simulator emits per step.

    Used by the renderer to emit a per-step shape assertion in the rendered
    module — if the LLM's `step` body forgets to populate a declared field,
    the sim raises `KeyError` on the first step rather than silently emitting
    truncated trajectories that downstream tests have to detect.
    """

    name: str = Field(min_length=1)
    type: Literal["int", "float", "str", "bool", "list", "dict"]
    description: str = ""


class SimulationPlan(_Base):
    """Plan for a deterministic, seedable simulator.

    Round-trip identity is the contract: a plan written by the generator
    must JSON-serialize, store as JSONB, and round-trip back to an
    identical Python object — same as `WorkflowSpec`.

    The plan does not carry the full WorkflowSpec; it carries `workflow_spec_id`
    as a back-pointer. The generator and renderer both take the WorkflowSpec
    separately so we don't risk drift between two stored copies.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    workflow_spec_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
        description="WorkflowSpec.id this plan was generated from.",
    )
    description: str = Field(
        min_length=1,
        description=(
            "One-line plain-English summary of what this simulator produces. "
            "Surfaces in the audit trail and the W7 UI. "
            "Must not contain newlines (they would escape the rendered comment)."
        ),
    )

    @field_validator("description")
    @classmethod
    def _description_no_newlines(cls, v: str) -> str:
        if "\n" in v or "\r" in v:
            raise ValueError(
                "description must not contain newlines — it is interpolated "
                "into a Python comment in the rendered module"
            )
        return v

    n_steps_default: int = Field(
        default=100,
        ge=1,
        le=10_000,
        description=(
            "Default trajectory length when input_data does not override. "
            "Capped at 10,000 to keep deterministic runs under the sandbox "
            "60-second budget on commodity hardware."
        ),
    )
    seed_default: int = Field(
        default=42,
        ge=0,
        le=2**31 - 1,
        description="Default RNG seed when input_data does not override.",
    )

    imports: list[str] = Field(
        default_factory=list,
        description=(
            "Stdlib modules the generated sim imports beyond the renderer's "
            "always-on `json` + `random`. Must be a subset of ALLOWED_IMPORTS."
        ),
    )

    init_state_code: str = Field(
        min_length=1,
        description=(
            "Body of `def init_state(rng): ...`. Must `return` the state dict. "
            "Receives a `random.Random` instance as `rng`. No leading indent — "
            "the renderer indents to 4 spaces. Must NOT import the `random` "
            "module itself; use the passed `rng`."
        ),
    )
    step_code: str = Field(
        min_length=1,
        description=(
            "Body of `def step(rng, state, step_index): ...`. Must `return` "
            "an event dict whose keys are the names of `event_fields`. "
            "Receives a `random.Random` instance as `rng`, the state dict "
            "from `init_state`, and a 0-based `step_index`. No leading indent."
        ),
    )

    event_fields: list[EventField] = Field(
        min_length=1,
        description=(
            "Shape of the dict that `step` returns. The renderer emits a "
            "per-step shape check in the trajectory loop so violations fail "
            "fast and loud rather than producing malformed trajectories."
        ),
    )


__all__ = [
    "SCHEMA_VERSION",
    "ALLOWED_IMPORTS",
    "EventField",
    "SimulationPlan",
]
