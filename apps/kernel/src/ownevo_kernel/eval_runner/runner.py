"""Deterministic replay runner (A4.3).

`run_replay(case_set, plan, spec, metric)` is the load-bearing entrypoint:

  1. Cross-check the trio agrees on `workflow_spec_id` (per
     `eval_replay.replay_set`).
  2. Cross-check `metric.workflow_spec_id` + `metric.direction`
     against the spec via `metric_compute._check_against_spec`.
  3. Replay every case against the rendered sim once
     (single exec'd namespace per `replay_set` semantics).
  4. Compute the metric over the result list.
  5. Pack everything — per-case outcomes, the metric value,
     meets_target, degenerate flag, the raw confusion counts —
     into an `EvalRunReport` the CLI and the gate both consume.

The report is intentionally JSON-serializable (no Pydantic validators
on the way out — the gate trusts the runner's output by construction).
`EvalRunReport.to_dict()` produces a dict suitable for
`json.dumps(..., sort_keys=True, default=str)` so the CLI can stream
to stdout and the audit chain can canonicalize without extra adapters.

`EvalRunnerError` distinguishes orchestration failures from the typed
exceptions the underlying primitives already raise. Cross-check
failures bubble up as the underlying `ValueError` from `replay_set` /
`_check_against_spec` — they're caller-bug surfaces, not gate signal.
Sim execution failures bubble up as `EvalReplayError` for the same
reason.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet
from ownevo_kernel.nl_gen.eval_replay import ReplayResult, replay_set
from ownevo_kernel.nl_gen.metric_compute import (
    MetricResult,
    check_against_spec,
    compute_metric,
)
from ownevo_kernel.nl_gen.metric_def import MetricDefinition
from ownevo_kernel.nl_gen.sim_plan import SimulationPlan
from ownevo_kernel.nl_gen.spec import WorkflowSpec

if TYPE_CHECKING:  # pragma: no cover - import only for static type-check
    from anthropic import AsyncAnthropic


class EvalRunnerError(RuntimeError):
    """Orchestration-level failure the typed primitives didn't already raise.

    Reserved for future use (e.g. an empty case set after filtering by
    `is_test_fold`). The current happy path doesn't raise this — callers
    see the underlying `ValueError` / `EvalReplayError` /
    `MetricComputeError` directly so the failure category stays sharp.
    """


@dataclass(frozen=True)
class EvalCaseOutcome:
    """One row in the eval report. Mirrors `ReplayResult` plus a
    `is_test_fold` carry-through so the gate can split train/test
    without re-joining against the source case set."""

    case_id: str
    expected_value: bool
    actual_value: Any
    passed: bool
    is_test_fold: bool


@dataclass(frozen=True)
class EvalRunReport:
    """The structured score the gate (and the CLI) consume.

    Keys are stable — additions go at the end so audit-chain
    canonicalization stays diff-friendly. The dict the CLI prints is
    `to_dict()`; the gate consumes the dataclass directly.
    """

    workflow_spec_id: str
    metric_name: str
    metric_family: str
    direction: str
    value: float
    target_value: float
    meets_target: bool
    degenerate: bool
    n_total: int
    n_pass: int
    tp: int
    tn: int
    fp: int
    fn: int
    outcomes: tuple[EvalCaseOutcome, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view. `outcomes` becomes a list of dicts."""
        d = asdict(self)
        d["outcomes"] = list(d["outcomes"])
        return d


def _outcome_for(result: ReplayResult, *, is_test_fold: bool) -> EvalCaseOutcome:
    return EvalCaseOutcome(
        case_id=result.case_id,
        expected_value=result.expected_value,
        actual_value=result.actual_value,
        passed=result.passed,
        is_test_fold=is_test_fold,
    )


def run_replay(
    case_set: EvalCaseSet,
    plan: SimulationPlan,
    spec: WorkflowSpec,
    metric: MetricDefinition,
) -> EvalRunReport:
    """Replay every case against the rendered sim and score with `metric`.

    Args:
        case_set: A4.1 EvalCaseSet — the cases to replay.
        plan: A3.2 SimulationPlan whose trajectory the cases target.
        spec: A3.1 WorkflowSpec the plan was rendered against.
        metric: A4.2 MetricDefinition that scores the replay results.

    Returns:
        An `EvalRunReport` with per-case outcomes + the computed metric.

    Raises:
        ValueError: cross-check failure between case_set / plan / spec
            (via `replay_set`), or between metric / spec (via
            `_check_against_spec`).
        EvalReplayError: a case is structurally broken — non-bool
            label_field, label_field absent from plan, step_index past
            trajectory's end, or sim execution raised.
        MetricComputeError: empty result list, non-bool label values,
            or computed value fell outside the metric's advertised bounds.
    """
    check_against_spec(metric, spec)

    results = replay_set(case_set, plan, spec)
    metric_result = compute_metric(metric, results)

    is_test_fold_by_id = {c.case_id: c.is_test_fold for c in case_set.cases}
    outcomes = tuple(
        _outcome_for(r, is_test_fold=is_test_fold_by_id[r.case_id]) for r in results
    )

    return _pack_report(spec, metric, metric_result, outcomes)


def _pack_report(
    spec: WorkflowSpec,
    metric: MetricDefinition,
    metric_result: MetricResult,
    outcomes: tuple[EvalCaseOutcome, ...],
) -> EvalRunReport:
    return EvalRunReport(
        workflow_spec_id=spec.id,
        metric_name=metric.name,
        metric_family=metric_result.family,
        direction=metric.direction,
        value=metric_result.value,
        target_value=metric.target_value,
        meets_target=metric_result.meets_target,
        degenerate=metric_result.degenerate,
        n_total=metric_result.n_total,
        n_pass=metric_result.n_pass,
        tp=metric_result.tp,
        tn=metric_result.tn,
        fp=metric_result.fp,
        fn=metric_result.fn,
        outcomes=outcomes,
    )


async def run_with_agent(
    case_set: EvalCaseSet,
    plan: SimulationPlan,
    spec: WorkflowSpec,
    metric: MetricDefinition,
    *,
    client: "AsyncAnthropic",
    model: str | None = None,
    max_tokens: int | None = None,
) -> EvalRunReport:
    """Same shape as `run_replay`, but `actual_value`s come from a Claude agent.

    The A4.4 smoke-test entrypoint. The agent (single-turn forced
    tool-use, see `agent_solver.solve_with_agent`) predicts the
    redacted bool label per case; predictions feed `compute_metric`
    via the same `ReplayResult` shape `replay_set` produces, so the
    `EvalRunReport` is byte-equivalent regardless of which solver
    populated it.

    Cross-checks (workflow_spec_id agreement, metric direction match)
    fire before any API call so a mis-stitched trio fails fast.

    Args:
        case_set / plan / spec / metric: same as `run_replay`.
        client: AsyncAnthropic client.
        model: Override `agent_solver.DEFAULT_MODEL`.
        max_tokens: Override `agent_solver.DEFAULT_MAX_TOKENS`.

    Returns:
        An `EvalRunReport` whose `outcomes[i].actual_value` is the
        agent's prediction (not the sim's ground truth).

    Raises:
        ValueError: cross-check failure (workflow_spec_id or direction).
        AgentSolverError / NoPredictToolUseError /
            PredictToolValidationError: from the agent solver.
        MetricComputeError: from compute_metric.
    """
    # Lazy import — agent_solver lives in the `agent` extra (anthropic).
    # Importing at module top would force every consumer of run_replay
    # to install the `agent` extra.
    from .agent_solver import (
        DEFAULT_MAX_TOKENS as _DEFAULT_MAX_TOKENS,
    )
    from .agent_solver import (
        DEFAULT_MODEL as _DEFAULT_MODEL,
    )
    from .agent_solver import solve_with_agent

    _check_against_spec(metric, spec)

    results = await solve_with_agent(
        client,
        case_set,
        plan,
        spec,
        model=model or _DEFAULT_MODEL,
        max_tokens=max_tokens or _DEFAULT_MAX_TOKENS,
    )
    metric_result = compute_metric(metric, results)

    is_test_fold_by_id = {c.case_id: c.is_test_fold for c in case_set.cases}
    outcomes = tuple(
        _outcome_for(r, is_test_fold=is_test_fold_by_id[r.case_id]) for r in results
    )
    return _pack_report(spec, metric, metric_result, outcomes)


__all__ = [
    "EvalCaseOutcome",
    "EvalRunReport",
    "run_replay",
    "run_with_agent",
]
