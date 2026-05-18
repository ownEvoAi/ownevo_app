"""MetricDefinition — typed schema for the A4.2 NL-gen artifact.

The metric generator takes a `WorkflowSpec` (A3.1) and emits a
`MetricDefinition` — the full success metric that the regression gate
scores proposed changes against, derived from the spec's
`success_criterion` stub.

Why a structured artifact rather than free-form Python:

  * **Inspect AI integration** (A4.3) needs a typed object it can wrap as
    a scorer; emitting Python directly would push the safety surface
    sim_render solved (AST safety pass, allowlist imports) onto the
    metric path too. A4.1's eval cases are bool x bool today, so a
    closed family of binary-classification metrics covers every workflow
    the spec can express; if the closed family stops fitting we widen
    `MetricFamily`, not the safety surface.
  * **Direction lock** — the gate decides "this iteration improved" by
    comparing a float against `best_ever`. Direction must agree with the
    `WorkflowSpec.success_criterion.direction` set by A3.1, otherwise a
    later regression could silently look like an improvement. A
    cross-spec validator in `metric_compute._check_against_spec` enforces
    this; the per-model validator here only enforces self-consistency
    (lower < upper, target ∈ bounds) because validators on the dataclass
    can't see the workflow spec.
  * **Auditability** — the metric definition surfaces in the audit trail
    next to each gate decision. A typed family + threshold + bounds is
    something the supply chain VP can read; a Python lambda isn't.

The metric value is computed by `metric_compute.compute_metric` over a
list of `eval_replay.ReplayResult`. Each replay result carries the
case's `expected_value` and the sim-emitted `actual_value`; both are
bool by construction (eval cases target bool-typed event fields per
the A4.1 contract).

Schema version is `"0.1"` until the A4-end freeze (mirrors the
A3.1/A3.2 pre-A3.4 pattern + A4.1's `EvalCaseSet`). A snapshot lands
at `nl_gen/schemas/metric_definition.v1.0.json` when the freeze fires;
the freeze-test pattern from `tests/test_nl_gen_schema_freeze.py`
carries over verbatim.

Downstream consumers:
  * `metric_generator.generate_metric_definition` — Anthropic tool-use call
  * `metric_compute.compute_metric` — pure compute over ReplayResults
  * Inspect AI integration (A4.3) — wraps the definition as a scorer
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .spec import Provenance

SCHEMA_VERSION = "0.1"
"""A4.2 schema version. Frozen to "1.0" at the A4-end ritual.

Pre-freeze the version is `0.1`. After freeze the kernel's freeze test
(modeled on `tests/test_nl_gen_schema_freeze.py`) will pin it against
the snapshot at `nl_gen/schemas/metric_definition.v1.0.json`. To
intentionally change the schema, bump this constant + regenerate the
snapshot."""


MetricFamily = Literal[
    "pass_rate",
    "precision",
    "recall",
    "f1",
    "balanced_accuracy",
    "specificity",
]
"""Closed family of binary-classification metrics.

Every supported family is computable from the (expected, actual) bool
pairs an `EvalCaseSet` produces under replay — no probabilistic
calibration, no continuous label, no free-form scorer. Widening this
union is the supported way to add a new metric family (e.g. `mcc`,
`youdens_j`); editing `metric_compute.compute_metric` accordingly is
the only other change required.

  * `pass_rate` — fraction of cases whose actual matched expected
    (== accuracy on a balanced binary task; named separately because
    it's the most aligned with the regression-gate's pass/fail framing).
  * `precision` — TP / (TP + FP). Use when false positives are costly
    (e.g. firing too many markdown alerts).
  * `recall` — TP / (TP + FN). Use when false negatives are costly
    (e.g. missing a winter boot spike).
  * `f1` — harmonic mean of precision + recall. Default for asymmetric
    workflows where both error modes hurt.
  * `balanced_accuracy` — (recall + specificity) / 2. Use when class
    imbalance would let `pass_rate` look good while one class is ignored.
  * `specificity` — TN / (TN + FP). Use when the negative class is the
    expensive one to confuse (e.g. flagging clauses that are actually fine).
"""


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricDefinition(_Base):
    """A4.2 artifact: the full success metric for one workflow.

    Round-trip identity is the contract — same as the rest of the NL-gen
    schemas. Validators pin the contract the generator's prompt is
    steering toward so a regression in steering shows up loudly here
    rather than silently in the gate.

    Cross-checks against the source `WorkflowSpec` (id matches,
    `direction` matches `success_criterion.direction`) live in
    `metric_compute._check_against_spec` — the per-model validator
    can't see the spec, and shoving the spec into the model would
    duplicate workflow data on every persisted metric.
    """

    schema_version: Literal["0.1"] = SCHEMA_VERSION
    workflow_spec_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
        description="WorkflowSpec.id this metric was generated for.",
    )
    name: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
        description=(
            "Kebab-case identifier the audit trail and lift chart reference. "
            "May rename the spec's `success_criterion.target_metric_name` if "
            "the chosen family makes a more precise name available."
        ),
    )
    family: MetricFamily = Field(
        description=(
            "Which binary-classification metric to compute. See `MetricFamily` "
            "for the closed family + when to use each."
        ),
    )
    direction: Literal["maximize", "minimize"] = Field(
        description=(
            "Improvement direction. MUST equal the source "
            "`WorkflowSpec.success_criterion.direction` — enforced by "
            "`metric_compute._check_against_spec`. All currently-supported "
            "families are naturally maximize; `minimize` is reserved for "
            "future error-rate families (e.g. false_positive_rate)."
        ),
    )
    lower_bound: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Lower bound of the metric's value range. 0.0 for every currently "
            "supported family. Carried explicitly so a future family with a "
            "different range (e.g. MCC ∈ [-1, 1]) doesn't quietly break the "
            "gate's range-check."
        ),
    )
    upper_bound: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Upper bound of the metric's value range. 1.0 for every currently "
            "supported family."
        ),
    )
    target_value: float = Field(
        description=(
            "Success threshold the gate compares against. For `direction=maximize` "
            "the gate requires `value >= target_value`; for `direction=minimize` "
            "the gate requires `value <= target_value`. Must lie inside "
            "[lower_bound, upper_bound]."
        ),
    )
    description: str = Field(
        min_length=1,
        description=(
            "Plain-English summary of what the metric measures. Surfaces in "
            "the audit trail and on the W7 metric card — the reviewer reads "
            "this, not the formula."
        ),
    )
    rationale: str = Field(
        min_length=1,
        description=(
            "One line explaining why this family was chosen for this workflow "
            "(e.g. quotes the past-miss phrase, names the asymmetric error "
            "cost). Surfaces in the audit trail next to the metric definition."
        ),
    )
    provenance: Provenance = Field(
        description=(
            "How this metric traces back to the user's description. "
            "`kind=derived` source = verbatim phrase from the spec's "
            "`success_criterion.description` or `known_past_misses`; "
            "`kind=inferred` source = named domain pattern."
        ),
    )

    @model_validator(mode="after")
    def _bounds_ordered(self) -> MetricDefinition:
        if self.lower_bound >= self.upper_bound:
            raise ValueError(
                f"lower_bound={self.lower_bound} must be strictly less than "
                f"upper_bound={self.upper_bound}"
            )
        return self

    @model_validator(mode="after")
    def _target_in_bounds(self) -> MetricDefinition:
        if not (self.lower_bound <= self.target_value <= self.upper_bound):
            raise ValueError(
                f"target_value={self.target_value} must lie inside "
                f"[{self.lower_bound}, {self.upper_bound}]"
            )
        return self


__all__ = [
    "SCHEMA_VERSION",
    "MetricFamily",
    "MetricDefinition",
]
