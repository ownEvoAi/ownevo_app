"""sim_tier discriminator + MockSim configuration schema.

Track 9.0.2. Workflows pick how their iterations run via
`workflows.sim_tier`:

  * `'real'`   — existing behaviour. The LLM-backed agent_solver runs
                 against the case set; benchmark workflows execute
                 code in LocalDockerSandbox.
  * `'mock'`   — MockAgentSolver (NL-gen workflows) or MockSimSandbox
                 (benchmark workflows). Deterministic scripted outputs
                 driven by `workflows.mock_sim_config`. Zero LLM cost,
                 sub-second per case.
  * `'replay'` — reserved for Track 9.0.3. Replay against captured
                 production traces.

This module owns the *schema* of the discriminator and the mock config
JSONB; the *behaviour* lives in the per-layer mock implementations
(`eval_runner/mock_solver.py`, `sandbox/mock_sim.py`).

Why a top-level module instead of co-locating in `eval_runner/` or
`sandbox/`: the same `MockSimConfig` is read by both layers depending
on whether the workflow's agent goes through `agent_solver` (NL-gen) or
through `SandboxRuntime` (M5/τ³ benchmarks). A neutral home avoids
importing one layer from the other.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SimTier(StrEnum):
    """Operational tier picked per workflow.

    The string values match the `workflows.sim_tier` text column +
    CHECK constraint (migration 0018). Storing the string verbatim
    keeps the DB column human-readable; the enum is for type-safety
    inside the kernel.
    """

    REAL = "real"
    MOCK = "mock"
    REPLAY = "replay"


class MockSimConfig(BaseModel):
    """JSONB stored in `workflows.mock_sim_config`.

    Required when `workflows.sim_tier = 'mock'` (DB-enforced via the
    migration 0018 CHECK constraint). Carries two independent scripting
    sections — workflows use whichever matches their agent shape:

      * `accuracy_per_iteration` + `default_accuracy` + `seed` —
        for NL-gen workflows whose agent goes through `agent_solver`.
        The MockAgentSolver deterministically chooses which fraction
        of cases get the correct prediction per iteration so the
        observed val_score matches `accuracy_for(iteration_index)`
        exactly (modulo case-count rounding).

      * `sandbox_script` — for benchmark workflows (M5/τ³) whose agent
        goes through SandboxRuntime. Each entry is a canned
        SandboxResult plan keyed by an opaque script id; the
        MockSimSandbox dispatches on a sequence.

    Both sections are optional individually; a config with neither is
    valid but useless (a `sim_tier='mock'` workflow with this config
    would no-op every prediction).
    """

    accuracy_per_iteration: list[float] = Field(
        default_factory=list,
        description=(
            "Per-iteration target accuracy in [0, 1]. The NL-gen "
            "MockAgentSolver chooses which fraction of cases get the "
            "correct prediction so the observed val_score matches "
            "`accuracy_per_iteration[N]` for iteration N. When N is "
            "past the end of the list, `default_accuracy` applies."
        ),
    )
    default_accuracy: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "Accuracy applied for iterations past the end of "
            "`accuracy_per_iteration`. Lets a workflow run "
            "indefinitely on mock tier without the curve growing."
        ),
    )
    seed: int = Field(
        default=42,
        ge=0,
        description=(
            "Seeds the per-iteration shuffle that picks which cases "
            "get the correct prediction. Same seed + same case_ids + "
            "same iteration_index → identical predictions across runs."
        ),
    )
    sandbox_script: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional per-invocation canned SandboxResult plan for "
            "MockSimSandbox. Schema is owned by `sandbox/mock_sim.py`."
        ),
    )

    @field_validator("accuracy_per_iteration")
    @classmethod
    def _accuracy_in_unit_interval(cls, v: list[float]) -> list[float]:
        for i, a in enumerate(v):
            if not 0.0 <= a <= 1.0:
                raise ValueError(
                    f"accuracy_per_iteration[{i}]={a} out of range — "
                    "must be in [0, 1]",
                )
        return v

    def accuracy_for(self, iteration_index: int) -> float:
        """Resolve target accuracy for a given iteration.

        Negative indexes return `default_accuracy` rather than raising;
        a negative iteration_index can only mean the runner hasn't
        wired it correctly, and silently degrading to the default
        keeps `sim_tier='mock'` runs from hard-failing the loop.
        """
        if 0 <= iteration_index < len(self.accuracy_per_iteration):
            return self.accuracy_per_iteration[iteration_index]
        return self.default_accuracy


__all__ = [
    "SimTier",
    "MockSimConfig",
]
