"""EvalCaseSet — typed schema for the A4.1 NL-gen artifact.

The eval-case generator takes a `WorkflowSpec` (A3.1) + a `SimulationPlan`
(A3.2) and emits an `EvalCaseSet` — 10-30 deterministic test cases pinned
against specific events in the rendered sim's trajectory.

Each `GeneratedEvalCase` carries everything the replay helper needs to
reproduce a single labeled observation:

  * `sim_seed` + `n_steps` + `target_step_index` — replay parameters that
    pick out one event from `run_simulation(seed, n_steps).trajectory[i]`.
  * `target_label_field` — name of the hidden ground-truth key in that
    event (e.g. `alert_correct_label`, `default_label`, `is_problematic`).
    Must appear in the SimulationPlan's `event_fields` with `type="bool"`.
  * `expected_value: bool` — what that hidden label should be. v0.1 is
    binary-classification only; multi-class lands at the A4-end freeze
    if generated metrics need it.

The generator's job is to pick a mix of cases that:

  * cover both expected_value classes (so the gate has signal in both
    directions, not just "the agent says True every time"),
  * include cases derived from each `known_past_misses` phrase the user
    flagged (these are the highest-value tests — they're literal user
    asks),
  * vary the seed so a deterministic seed flake doesn't produce a single-
    physics suite,
  * mark ~20% of cases `is_test_fold=True` for held-out evaluation.

Schema version is `"0.1"` until the A4-end freeze (mirrors the A3.1/A3.2
pre-A3.4 pattern). A snapshot lands at
`nl_gen/schemas/eval_case_set.v1.0.json` when the freeze fires; the
freeze-test pattern from `tests/test_nl_gen_schema_freeze.py` carries
over verbatim.

Downstream consumers:
  * `eval_generator.generate_eval_case_set` — Anthropic tool-use call
  * `eval_replay.replay_case` — runs the rendered sim and decides pass/fail
  * `eval_persistence.persist_eval_case_set` — converts each case into
    an `add_eval_case(provenance=NL_GEN, ...)` DB row
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .spec import Provenance

SCHEMA_VERSION = "0.1"
"""A4.1 schema version. Frozen to "1.0" at the A4-end ritual.

Pre-freeze the version is `0.1`. After freeze the kernel's freeze test
(modeled on `tests/test_nl_gen_schema_freeze.py`) will pin it against
the snapshot at `nl_gen/schemas/eval_case_set.v1.0.json`. To intentionally
change the schema, bump this constant + regenerate the snapshot."""
MIN_CLASS_COUNT = 3
"""Minimum number of True and False expected_value entries required per EvalCaseSet."""


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GeneratedEvalCase(_Base):
    """One eval case pinned against a deterministic event in the sim trajectory.

    The replay helper runs `run_simulation(sim_seed, n_steps)` and reads
    `trajectory[target_step_index][target_label_field]` — pass if the read
    equals `expected_value`, fail otherwise. Determinism comes from the
    rendered sim (`sim_render` wires `random.Random(seed)` as the only
    RNG); A4.1 only adds the targeting layer.
    """

    case_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
        description=(
            "Kebab-case identifier the audit trail references. Must be unique "
            "within an EvalCaseSet."
        ),
    )
    provenance: Provenance = Field(
        description=(
            "How this case traces back to the user's description. "
            "`kind=derived` source = verbatim `known_past_misses` phrase; "
            "`kind=inferred` source = named domain pattern."
        ),
    )
    sim_seed: int = Field(
        ge=0,
        le=2**31 - 1,
        description="Seed for `random.Random(seed)` in the rendered sim.",
    )
    n_steps: int = Field(
        ge=1,
        le=10_000,
        description=(
            "How many trajectory steps to run when replaying this case. "
            "Bounded by SimulationPlan.n_steps_default's ceiling."
        ),
    )
    target_step_index: int = Field(
        ge=0,
        description=(
            "0-based index into `trajectory` whose hidden label this case "
            "asserts against. Must be < n_steps."
        ),
    )
    target_label_field: str = Field(
        min_length=1,
        description=(
            "Name of the hidden ground-truth key on the targeted event. "
            "Must appear in the SimulationPlan's `event_fields` with "
            "`type=bool`."
        ),
    )
    expected_value: bool = Field(
        description=(
            "What the hidden label at the targeted step should be. v0.1 "
            "is binary classification only — pass/fail compares against "
            "this exact value."
        ),
    )
    rationale: str = Field(
        min_length=1,
        description=(
            "One-line plain-English summary of why this case is interesting "
            "(e.g. quotes the past-miss phrase, names the domain pattern). "
            "Surfaces in the audit trail and the W7 UI."
        ),
    )
    is_test_fold: bool = Field(
        default=False,
        description=(
            "Held-out evaluation. The gate runner refuses to use test-fold "
            "rows for training (W2.3 train/test discipline)."
        ),
    )

    @model_validator(mode="after")
    def _step_index_within_n_steps(self) -> GeneratedEvalCase:
        if self.target_step_index >= self.n_steps:
            raise ValueError(
                f"target_step_index={self.target_step_index} must be < "
                f"n_steps={self.n_steps}"
            )
        return self


class EvalCaseSet(_Base):
    """A4.1 artifact: 10-30 generated eval cases for one workflow.

    Round-trip identity is the contract — same as `WorkflowSpec` and
    `SimulationPlan`. Validators pin the contract the generator's prompt
    is steering toward so a regression in steering shows up loudly here
    rather than silently in the agent loop.
    """

    schema_version: Literal["0.1"] = SCHEMA_VERSION
    workflow_spec_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
        description="WorkflowSpec.id this case set was generated from.",
    )
    simulation_plan_workflow_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
        description=(
            "SimulationPlan.workflow_spec_id — must equal workflow_spec_id. "
            "Carried explicitly so the validator can catch drift between a "
            "case set and the wrong sim plan when both are stored."
        ),
    )
    cases: list[GeneratedEvalCase] = Field(
        min_length=10,
        max_length=30,
        description=(
            "10-30 cases per A4.1's PLAN.md spec. Lower bound prevents "
            "single-fixture suites; upper bound caps replay cost when the "
            "loop turns over many sim variants."
        ),
    )

    @model_validator(mode="after")
    def _back_pointers_agree(self) -> EvalCaseSet:
        if self.simulation_plan_workflow_id != self.workflow_spec_id:
            raise ValueError(
                f"simulation_plan_workflow_id="
                f"{self.simulation_plan_workflow_id!r} must equal "
                f"workflow_spec_id={self.workflow_spec_id!r}"
            )
        return self

    @model_validator(mode="after")
    def _case_ids_unique(self) -> EvalCaseSet:
        ids = [c.case_id for c in self.cases]
        if len(ids) != len(set(ids)):
            dupes = sorted(k for k, v in Counter(ids).items() if v > 1)
            raise ValueError(f"cases contains duplicate case_ids: {dupes}")
        return self

    @model_validator(mode="after")
    def _balanced_classes(self) -> EvalCaseSet:
        """Both expected_value classes must appear at least 3 times.

        Catches the failure mode where the LLM emits a one-class suite
        (always True or always False), which would let a "the agent says
        True every time" skill silently pass the gate.
        """
        true_count = sum(1 for c in self.cases if c.expected_value is True)
        false_count = len(self.cases) - true_count
        if true_count < MIN_CLASS_COUNT or false_count < MIN_CLASS_COUNT:
            raise ValueError(
                f"cases must include >={MIN_CLASS_COUNT} True and "
                f">={MIN_CLASS_COUNT} False expected_value "
                f"entries (got True={true_count}, False={false_count})"
            )
        return self


__all__ = [
    "SCHEMA_VERSION",
    "MIN_CLASS_COUNT",
    "GeneratedEvalCase",
    "EvalCaseSet",
]
