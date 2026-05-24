"""Deterministic mock agent solver — drop-in for `solve_with_agent`.

Track 9.0.2 Slice A. When `workflows.sim_tier='mock'`, iteration_runner
swaps `solve_with_agent` (which calls an LLM per case) for
`solve_with_mock_agent` (which synthesizes predictions per a config).

Use cases this unlocks:
  * Fast inner-loop dev — run the full iteration_runner end-to-end in
    under a second per iteration, without an API key or Docker daemon.
  * CI integration tests of the loop's control logic (proposer
    cadence, clustering wiring, regression-gate verdict) without the
    LLM as a flake source.
  * Determinism for regression-gate regression tests — same workflow
    state + same iteration_index → byte-identical EvalRunReport.
  * Cost-free experimentation with the loop's state machine, e.g.
    "what does the UI look like after 20 iterations?" without spending
    20 × $0.30 per loop pass.

Accuracy semantics: given `accuracy_for(iteration_index) = a` and
`n_cases = n`, the solver picks `round(n * a)` cases to mark correct
and the rest incorrect — the observed val_score equals `a` exactly
(modulo rounding), not just in expectation. The choice of *which*
cases are correct is a seeded shuffle keyed by
`(config.seed, iteration_index)`, so the same workflow + same
iteration produces identical predictions across machines and runs.

This file mirrors `agent_solver.solve_with_agent`'s signature so
`runner.run_with_mock_agent` is a near-copy of `runner.run_with_agent`
— substitution at one call site, no shape churn downstream.
"""

from __future__ import annotations

import random

from ..nl_gen.eval_case_set import EvalCaseSet
from ..nl_gen.eval_replay import ReplayResult
from ..nl_gen.metric_def import MetricDefinition
from ..nl_gen.sim_plan import SimulationPlan
from ..nl_gen.spec import WorkflowSpec
from ..sim_tier import MockSimConfig

_MOCK_RATIONALE = (
    "[mock] Prediction synthesized by MockAgentSolver — "
    "workflow.sim_tier='mock'. No LLM call was made."
)


async def solve_with_mock_agent(
    case_set: EvalCaseSet,
    plan: SimulationPlan,
    spec: WorkflowSpec,
    metric: MetricDefinition,
    *,
    mock_config: MockSimConfig,
    iteration_index: int,
) -> list[ReplayResult]:
    """Synthesize per-case predictions matching a target accuracy.

    Same signature spine as `agent_solver.solve_with_agent` (case_set,
    plan, spec, metric, plus solver-specific kwargs) so the upstream
    `run_with_*` wrappers are interchangeable.

    Args:
        case_set: Cases to predict. Each case's `expected_value` is
            treated as ground truth (the per-iteration target accuracy
            is the fraction of cases that get this value returned;
            the rest get its complement).
        plan / spec / metric: Carried for cross-check parity with
            `solve_with_agent`. The mock solver doesn't execute the
            sim trajectory — predictions come from the curve — but
            workflow_spec_id agreement is still enforced so a
            mis-stitched trio fails fast on mock tier the same way it
            does on real tier.
        mock_config: Per-workflow `MockSimConfig` (read from
            `workflows.mock_sim_config`).
        iteration_index: 0-indexed iteration counter from
            `workflows.iterations`. Drives both the accuracy lookup
            and the shuffle seed so each iteration's predictions
            differ even at the same target accuracy.

    Returns:
        `ReplayResult` per case, in `case_set.cases` order. Rationale
        carries a `[mock]` marker so the trace makes the mock origin
        obvious.

    Raises:
        ValueError: workflow_spec_id mismatch between case_set / plan
            / metric and spec. Mirrors `solve_with_agent`'s checks.
    """
    if case_set.workflow_spec_id != spec.id:
        raise ValueError(
            f"case_set.workflow_spec_id={case_set.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}",
        )
    if plan.workflow_spec_id != spec.id:
        raise ValueError(
            f"plan.workflow_spec_id={plan.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}",
        )
    if metric.workflow_spec_id != spec.id:
        raise ValueError(
            f"metric.workflow_spec_id={metric.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}",
        )

    target_accuracy = mock_config.accuracy_for(iteration_index)
    n_cases = len(case_set.cases)

    # Empty case set is a real-tier no-op too — match that shape
    # rather than divide by zero on the rounding step below.
    if n_cases == 0:
        return []

    # Pick which cases get the correct answer. Sort by case_id first
    # so the shuffle is reproducible across runs even if the case_set
    # ordering changes upstream. Seed = config.seed XOR iteration so
    # the same workflow's iterations don't all pick the same cases.
    case_ids_sorted = sorted(c.case_id for c in case_set.cases)
    rng = random.Random(mock_config.seed ^ iteration_index)
    shuffled = list(case_ids_sorted)
    rng.shuffle(shuffled)
    n_correct = round(n_cases * target_accuracy)
    correct_ids = set(shuffled[:n_correct])

    # Walk the original case_set order to keep result order matching
    # what upstream callers (compute_metric, the gate) expect.
    results: list[ReplayResult] = []
    for case in case_set.cases:
        expected = case.expected_value
        actual = expected if case.case_id in correct_ids else not expected
        results.append(
            ReplayResult(
                case_id=case.case_id,
                passed=(actual == expected),
                actual_value=actual,
                expected_value=expected,
                rationale=_MOCK_RATIONALE,
                output_payload=None,
            ),
        )
    return results


__all__ = [
    "solve_with_mock_agent",
]
