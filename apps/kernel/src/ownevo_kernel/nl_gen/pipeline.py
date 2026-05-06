"""End-to-end NL-gen pipeline: description → typed quartet (A4.4).

Sequences the four single-turn generators into one orchestrator:

  1. `generate_workflow_spec(description)`  → A3.1 `WorkflowSpec`
  2. `generate_simulation_plan(spec)`       → A3.2 `SimulationPlan`
  3. `generate_eval_case_set(spec, plan)`   → A4.1 `EvalCaseSet`
  4. `generate_metric_definition(spec)`     → A4.2 `MetricDefinition`

Each step's input is the previous step's typed output. Cross-checks
are enforced by the underlying generators (e.g. eval_generator
pre-flights `simulation_plan.workflow_spec_id == workflow_spec.id`),
so the pipeline itself is just sequencing.

Why a separate orchestrator (instead of the smoke test inlining the
four calls): the same trio of artifacts is used by:

  * `make nl-gen-smoketest`  (A4.4 — agent-solve gate, this commit)
  * `make eval-replay --regenerate`  (A4.3 follow-on)
  * Future cached-pipeline cache poisoning tests (future)

Centralizing the orchestration keeps the cross-step contract in one
place; if A5+ inserts a meta-eval gate between steps 3 and 4, that
plug-in lives here.

Cost: one full pipeline = 4 live Anthropic calls. Defaults to the
generators' own `DEFAULT_MODEL`s (opus 4.7) — pass `model=` to override
all four uniformly when running on a cheaper tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .eval_case_set import EvalCaseSet
from .eval_generator import generate_eval_case_set
from .metric_def import MetricDefinition
from .metric_generator import generate_metric_definition
from .sim_generator import generate_simulation_plan
from .sim_plan import SimulationPlan
from .spec import WorkflowSpec
from .workflow_spec_generator import generate_workflow_spec

if TYPE_CHECKING:  # pragma: no cover - import only for static type-check
    from anthropic import AsyncAnthropic


@dataclass(frozen=True)
class NLGenPipelineResult:
    """The four typed artifacts produced by one pipeline run.

    The dataclass is frozen so a downstream consumer can't mutate
    intermediate state; if a step needs to be re-derived (e.g. the
    smoke test wants to override `metric.target_value` for a probe
    run), the consumer copies via `dataclasses.replace`.
    """

    workflow_spec: WorkflowSpec
    simulation_plan: SimulationPlan
    eval_case_set: EvalCaseSet
    metric_definition: MetricDefinition


async def generate_full_pipeline(
    client: "AsyncAnthropic",
    description: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> NLGenPipelineResult:
    """Run all four NL-gen generators in sequence; return the typed quartet.

    Args:
        client: AsyncAnthropic client passed to every step.
        description: The user-typed plain-English workflow description
            (the textarea content from the W7 "New Workflow" mock).
        model: Optional model override applied uniformly to all four
            steps. None → each generator's `DEFAULT_MODEL`.
        max_tokens: Optional max-tokens override applied uniformly to
            all four steps. None → each generator's `DEFAULT_MAX_TOKENS`
            (varies — eval-gen needs more headroom than metric-gen).

    Returns:
        `NLGenPipelineResult` with the four typed artifacts.

    Raises:
        Whatever the underlying generators raise — they're already
        typed (`NLGenError` subclasses), and the pipeline doesn't
        wrap them so the smoke test's error class stays sharp.
    """
    spec_kwargs = _kwargs_for(model, max_tokens)
    workflow_spec = await generate_workflow_spec(
        client, description, **spec_kwargs
    )

    plan_kwargs = _kwargs_for(model, max_tokens)
    simulation_plan = await generate_simulation_plan(
        client, workflow_spec, **plan_kwargs
    )

    eval_kwargs = _kwargs_for(model, max_tokens)
    eval_case_set = await generate_eval_case_set(
        client, workflow_spec, simulation_plan, **eval_kwargs
    )

    metric_kwargs = _kwargs_for(model, max_tokens)
    metric_definition = await generate_metric_definition(
        client, workflow_spec, **metric_kwargs
    )

    return NLGenPipelineResult(
        workflow_spec=workflow_spec,
        simulation_plan=simulation_plan,
        eval_case_set=eval_case_set,
        metric_definition=metric_definition,
    )


def _kwargs_for(
    model: str | None, max_tokens: int | None
) -> dict:
    """Build the kwargs dict for a generator call, omitting None overrides."""
    out: dict = {}
    if model is not None:
        out["model"] = model
    if max_tokens is not None:
        out["max_tokens"] = max_tokens
    return out


__all__ = [
    "NLGenPipelineResult",
    "generate_full_pipeline",
]
