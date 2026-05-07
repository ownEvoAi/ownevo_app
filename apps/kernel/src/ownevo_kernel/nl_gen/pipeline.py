"""End-to-end NL-gen pipeline: description → typed quartet (A4.4 + W5.5).

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
place; the W5.5 meta-eval gate also plugs in here as an optional
fifth call after the metric_definition lands.

Cost: one full pipeline = 4 live Anthropic calls (5 with the meta-eval
gate enabled). Defaults to the generators' own `DEFAULT_MODEL`s
(opus 4.7) — pass `model=` to override all four uniformly when running
on a cheaper tier. The meta-eval judge takes its own optional override.

W5.5 — Meta-eval as quality gate
================================

Per `docs/PLAN.md` § W5 § 5.5: "every generated workflow runs through
meta-eval BEFORE the agent loop starts". The gate is opt-in via
`meta_eval_gate=True` so existing A4.4 callers (smoke test default,
unit tests, eval-replay) keep their 4-call shape. When enabled:

  * After step 4, `judge_artifacts` is called on the bundle.
  * The returned `MetaEvalJudgment` is attached to the result so the
    UI / smoketest CLI can surface coverage % + per-dimension verdicts.
  * The gate passes iff `overall_verdict == "good"` AND (when set)
    `aggregate_score >= meta_eval_min_aggregate_score`.
  * On gate failure, `MetaEvalGateFailedError` is raised with the
    judgment attached so the caller can still log the diagnostic.

The gate uses a binary `overall_verdict` rather than a soft score
because the judge has discretion baked in: it can call `good` on a
(pass, pass, partial) bundle. A numeric floor is exposed as an
optional belt-and-braces guard (e.g. `--meta-eval-min-aggregate-score
0.66` rejects `partial / partial / partial`).
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
from .workflow_spec_generator import NLGenError, generate_workflow_spec

if TYPE_CHECKING:  # pragma: no cover - import only for static type-check
    from anthropic import AsyncAnthropic

    from .meta_eval import MetaEvalJudgment


@dataclass(frozen=True)
class NLGenPipelineResult:
    """The four typed artifacts produced by one pipeline run.

    The dataclass is frozen so a downstream consumer can't mutate
    intermediate state; if a step needs to be re-derived (e.g. the
    smoke test wants to override `metric.target_value` for a probe
    run), the consumer copies via `dataclasses.replace`.

    `meta_eval_judgment` is set only when the W5.5 gate ran and
    passed; `None` otherwise (gate disabled, or — on the fail path
    — surfaced via `MetaEvalGateFailedError.judgment` instead).
    """

    workflow_spec: WorkflowSpec
    simulation_plan: SimulationPlan
    eval_case_set: EvalCaseSet
    metric_definition: MetricDefinition
    meta_eval_judgment: MetaEvalJudgment | None = None


class MetaEvalGateFailedError(NLGenError):
    """The W5.5 meta-eval gate ran and rejected the bundle.

    The judgment is attached so the caller can log the per-dimension
    verdicts + rationales without re-running the judge. Audit-log
    consumers should record the judgment alongside the failure so the
    "why didn't this workflow reach the agent loop" question is
    answerable from the trace alone.
    """

    def __init__(
        self,
        message: str,
        *,
        judgment: MetaEvalJudgment,
        min_aggregate_score: float | None,
    ) -> None:
        super().__init__(message)
        self.judgment = judgment
        self.min_aggregate_score = min_aggregate_score


async def generate_full_pipeline(
    client: AsyncAnthropic,
    description: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    meta_eval_gate: bool = False,
    meta_eval_model: str | None = None,
    meta_eval_max_tokens: int | None = None,
    meta_eval_min_aggregate_score: float | None = None,
) -> NLGenPipelineResult:
    """Run all four NL-gen generators in sequence; return the typed quartet.

    Args:
        client: AsyncAnthropic client passed to every step (including
            the meta-eval judge when the gate is enabled).
        description: The user-typed plain-English workflow description
            (the textarea content from the W7 "New Workflow" mock).
        model: Optional model override applied uniformly to all four
            generator steps. None → each generator's `DEFAULT_MODEL`.
        max_tokens: Optional max-tokens override applied uniformly to
            all four generator steps. None → each generator's
            `DEFAULT_MAX_TOKENS` (varies — eval-gen needs more headroom
            than metric-gen).
        meta_eval_gate: When True, run the W5.5 meta-eval judge after
            the four generators and gate on `overall_verdict == "good"`
            (plus `meta_eval_min_aggregate_score` if set). Default
            False — the four-generator A4.4 shape preserved.
        meta_eval_model: Optional model override for the meta-eval
            judge only. None → judge's `DEFAULT_MODEL` (opus 4.7,
            the calibration anchor). Independent of `model=` so a
            cheap-NL-gen-+-frontier-judge configuration is one flag.
        meta_eval_max_tokens: Optional max-tokens override for the
            judge call. None → judge's `DEFAULT_MAX_TOKENS` (6k —
            wider than the metric generator's 4k for the rationales).
        meta_eval_min_aggregate_score: Optional numeric floor on the
            judgment's `aggregate_score()` (mean of pass=1.0,
            partial=0.5, fail=0.0). When set, the gate also requires
            the score to be ≥ this value. Belt-and-braces guard for
            the (partial, partial, partial) failure mode the judge
            might call `good` on. Ignored when `meta_eval_gate=False`.

    Returns:
        `NLGenPipelineResult`. `meta_eval_judgment` is the judgment
        when the gate ran and passed; `None` when the gate was
        disabled.

    Raises:
        Whatever the underlying generators raise — they're already
        typed (`NLGenError` subclasses), and the pipeline doesn't
        wrap them so the smoke test's error class stays sharp.
        Plus `MetaEvalGateFailedError` when the gate is enabled and
        the judgment fails the threshold.
    """
    kwargs = _kwargs_for(model, max_tokens)
    workflow_spec = await generate_workflow_spec(
        client, description, **kwargs
    )

    simulation_plan = await generate_simulation_plan(
        client, workflow_spec, **kwargs
    )

    eval_case_set = await generate_eval_case_set(
        client, workflow_spec, simulation_plan, **kwargs
    )

    metric_definition = await generate_metric_definition(
        client, workflow_spec, **kwargs
    )

    judgment: MetaEvalJudgment | None = None
    if meta_eval_gate:
        judgment = await _run_meta_eval_gate(
            client,
            description,
            workflow_spec,
            simulation_plan,
            eval_case_set,
            metric_definition,
            model=meta_eval_model,
            max_tokens=meta_eval_max_tokens,
            min_aggregate_score=meta_eval_min_aggregate_score,
        )

    return NLGenPipelineResult(
        workflow_spec=workflow_spec,
        simulation_plan=simulation_plan,
        eval_case_set=eval_case_set,
        metric_definition=metric_definition,
        meta_eval_judgment=judgment,
    )


async def _run_meta_eval_gate(
    client: AsyncAnthropic,
    description: str,
    spec: WorkflowSpec,
    plan: SimulationPlan,
    case_set: EvalCaseSet,
    metric: MetricDefinition,
    *,
    model: str | None,
    max_tokens: int | None,
    min_aggregate_score: float | None,
) -> MetaEvalJudgment:
    """Run the meta-eval judge and apply the W5.5 gate logic.

    Imported lazily so callers without the `agent` extra (anthropic dep)
    can still import and use the four-generator path. The judge module
    only references `anthropic` under `TYPE_CHECKING`, so this is
    defense-in-depth — but the lazy boundary is the meta_eval package's
    documented contract, so we honour it here too.
    """
    from .meta_eval import judge_artifacts

    judge_kwargs: dict = {}
    if model is not None:
        judge_kwargs["model"] = model
    if max_tokens is not None:
        judge_kwargs["max_tokens"] = max_tokens

    judgment = await judge_artifacts(
        client, description, spec, plan, case_set, metric, **judge_kwargs
    )

    if judgment.overall_verdict != "good":
        raise MetaEvalGateFailedError(
            f"Meta-eval gate rejected bundle for spec {spec.id!r}: "
            f"overall_verdict={judgment.overall_verdict!r} "
            f"(sim_coverage={judgment.sim_coverage.verdict}, "
            f"eval_case_coverage={judgment.eval_case_coverage.verdict}, "
            f"metric_alignment={judgment.metric_alignment.verdict}, "
            f"aggregate_score={judgment.aggregate_score():.3f})",
            judgment=judgment,
            min_aggregate_score=min_aggregate_score,
        )

    if (
        min_aggregate_score is not None
        and judgment.aggregate_score() < min_aggregate_score
    ):
        raise MetaEvalGateFailedError(
            f"Meta-eval gate rejected bundle for spec {spec.id!r}: "
            f"aggregate_score={judgment.aggregate_score():.3f} < "
            f"min_aggregate_score={min_aggregate_score:.3f} "
            f"(overall_verdict={judgment.overall_verdict!r})",
            judgment=judgment,
            min_aggregate_score=min_aggregate_score,
        )

    return judgment


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
    "MetaEvalGateFailedError",
    "NLGenPipelineResult",
    "generate_full_pipeline",
]
