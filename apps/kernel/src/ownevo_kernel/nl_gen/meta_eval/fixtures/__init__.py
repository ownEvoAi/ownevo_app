"""Minimal-but-valid fixtures for the A4.6 meta-eval set.

Seven new (description, WorkflowSpec, SimulationPlan, EvalCaseSet,
MetricDefinition) bundles. Domains span supply-chain, credit-risk,
legal, support, labour, and other so the judge has to actually read
the description to score sim_coverage / eval_case_coverage /
metric_alignment — not pattern-match on a single domain.

These fixtures are NOT replayable end-to-end the way the A4.1-A4.4
production fixtures are. They are structurally valid (every artifact
round-trips through Pydantic) and semantically coherent (the
description, spec, sim, eval cases, and metric all describe the same
workflow), but the sim's `step_code` is plausible-looking pseudocode
rather than a calibrated simulator.

Why minimal: the A4.6 deliverable is "≥10 descriptions × {good, bad}
pairs". The judge consumes JSON-serialized artifacts, so what matters
is that the bundle is structurally valid + semantically coherent
enough that a human labeler can call it `good`. Authoring 7 fully
calibrated fixtures (the A4.1-A4.4 production-fixture cost is ~250
LOC each) would 5x the PR scope without improving judge calibration.

The 3 production fixtures (demand-prediction, credit-risk-line-
recalibration, union-contract-review) join these 7 as the eval set
in `eval_set.py`.
"""

from .minimal_fixtures import (
    MINIMAL_BUNDLES,
    MINIMAL_DESCRIPTIONS,
    MinimalBundle,
)

__all__ = [
    "MINIMAL_BUNDLES",
    "MINIMAL_DESCRIPTIONS",
    "MinimalBundle",
]
