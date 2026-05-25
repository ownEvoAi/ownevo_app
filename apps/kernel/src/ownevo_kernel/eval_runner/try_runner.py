"""Try-it: execute one eval case against a workflow without any writes.

Backs `POST /api/workflows/{id}/try` (PLAN 8.5.2 — competitive-parity
floor for end-user agent-builder surfaces vs. M365 Agent Builder +
Gemini Designer). The reviewer on the new-workflow Step 2 page picks
one eval case, sees the agent execute end-to-end, reads the structured
output + trace + cost, then decides whether to confirm.

Reuses `predict_one()` from `agent_solver` — the only single-case
execution path in the kernel. Bypasses every iteration-level write
(`iterations`, `proposals`, `failure_clusters`, `audit_entries`); the
DB connection is only used to read the workflow row + one eval case.

Out of scope (deferred):
  * `free_form_input`: the agent solver requires a `GeneratedEvalCase`
    with `sim_seed` / `n_steps` / `target_step_index` that drives a
    trajectory through the sim. Free-form text doesn't fit that shape
    without inventing a synthetic case. First-cut supports `eval_case_id`
    only; the API surfaces 400 on `free_form_input` until this is wired.
  * Multi-turn agentic loop: `run_agent_turn` is per-iteration; Try-it
    is single-turn forced tool-use (the same path `predict_one` uses).
  * Streaming: trace is returned as one `[start, result]` pair after
    the call returns. No SSE.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import asyncpg

from ..eval_cases.registry import get_eval_case
from ..nl_gen.eval_case_set import GeneratedEvalCase
from ..nl_gen.eval_replay import exec_sim_module
from ..nl_gen.metric_def import MetricDefinition
from ..nl_gen.sim_plan import SimulationPlan
from ..nl_gen.spec import Provenance, WorkflowSpec
from ..tenant_session import acquire_workspace_conn
from .agent_solver import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    AgentSolverError,
    predict_one,
)
from .token_budget import TokenBudget

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


# Anthropic pricing per million tokens, USD. Refresh as the provider
# revises rates. Unknown models (local LLMs, future releases) fall
# through to 0.0 so the UI renders "—" rather than a falsely-precise
# number. Source: anthropic.com pricing page snapshot 2026-Q2.
_PRICING_PER_M: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
    "claude-opus-4-6": (15.00, 75.00),
}


def compute_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Convert token counts to a USD estimate against the known rates.

    Returns 0.0 for any model not in the pricing table — keeps the UI
    honest about local-LLM and unknown-frontier paths.
    """
    rate = _PRICING_PER_M.get(model)
    if rate is None:
        return 0.0
    in_rate, out_rate = rate
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


@dataclass(frozen=True)
class TryItResult:
    """One Try-it execution result. Mirrors the API response shape."""

    case_id: str
    expected_value: Any
    actual_value: Any
    rationale: str
    passed: bool
    model: str
    duration_ms: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    trace: list[dict[str, Any]]


class TryRunnerError(Exception):
    """Base for try-runner failures the API maps to non-500 HTTP codes."""


class WorkflowNotReadyError(TryRunnerError):
    """Workflow row missing the spec / sim_plan / metric NL-gen produces."""


class EvalCaseNotFoundError(TryRunnerError):
    """No eval case with that id, or it belongs to a different workflow."""


def _coerce_jsonb(value: object) -> dict[str, Any] | None:
    """Same shape-coercion as iteration_runner — asyncpg returns jsonb
    as dict or str depending on codec wiring."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        return json.loads(value)
    raise TypeError(f"unexpected JSONB payload type: {type(value).__name__}")


def _eval_case_to_generated(case: Any, workflow_spec_id: str) -> GeneratedEvalCase:
    """Mirror `iteration_runner._eval_cases_to_set` for a single case.

    Inlined here (rather than importing the loop helper) so the Try-it
    path has zero dependency on iteration_runner internals.
    """
    inp = case.input or {}
    eb = case.expected_behavior or {}
    prov = eb.get("provenance") or {}
    return GeneratedEvalCase(
        case_id=str(eb.get("case_id") or case.id),
        provenance=Provenance(
            kind=prov.get("kind", "inferred"),
            source=prov.get("source", "try-runner-fallback"),
        ),
        sim_seed=int(inp.get("sim_seed", 0)),
        n_steps=int(inp.get("n_steps", 1)),
        target_step_index=int(inp.get("target_step_index", 0)),
        target_label_field=str(eb.get("target_label_field") or "label"),
        expected_value=bool(eb.get("expected_value", False)),
        rationale=str(eb.get("rationale") or "(no rationale recorded)"),
        is_test_fold=case.is_test_fold,
    )


def _trace_pair(
    *,
    case_id: str,
    model: str,
    duration_ms: int,
    passed: bool,
    error_class: str | None,
    error_message: str | None,
    output: Any,
) -> list[dict[str, Any]]:
    """Minimal [tool_call_start, tool_call_result] pair around the call.

    The trace-format SPEC discriminates on `type`; the iteration runner
    synthesizes similar pairs in `_trace_events_for_outcome`. Keeping the
    shape small here avoids importing the full Pydantic AgentEvent model
    (the API serializes these dicts directly via FastAPI's response_model
    on the route — see TryItResponse for the wire shape).
    """
    trace_id = uuid4()
    call_id = f"try-{case_id}-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    start = {
        "type": "tool_call_start",
        "event_id": str(uuid4()),
        "trace_id": str(trace_id),
        "iteration_id": None,
        "timestamp": now,
        "call_id": call_id,
        "name": "predict_label",
        "args": {"case_id": case_id, "model": model},
    }
    result = {
        "type": "tool_call_result",
        "event_id": str(uuid4()),
        "trace_id": str(trace_id),
        "iteration_id": None,
        "timestamp": now,
        "call_id": call_id,
        "name": "predict_label",
        "status": "error" if error_class else "ok",
        "duration_ms": duration_ms,
        "output": {"value": output, "passed": passed},
        "error": error_message,
        "error_class": error_class,
    }
    return [start, result]


async def try_one_eval_case(
    pool: asyncpg.Pool,
    workflow_id: str,
    eval_case_id: UUID,
    *,
    workspace_id: str,
    client: "AsyncAnthropic",
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> TryItResult:
    """Run one eval case end-to-end. No iteration / proposal / audit writes.

    Raises:
        WorkflowNotReadyError: spec / sim_plan / metric missing on the
            workflow row (UI should point user at the gen endpoint).
        EvalCaseNotFoundError: the case doesn't exist or belongs to a
            different workflow.
        AgentSolverError: the agent call failed (LLM error, tool-use
            validation, etc.) — caller maps to 502.
    """
    async with acquire_workspace_conn(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT spec, simulation_plan, metric_definition
            FROM workflows WHERE id = $1
            """,
            workflow_id,
        )
        if row is None:
            raise WorkflowNotReadyError(f"workflow {workflow_id!r} not found")
        spec_dict = _coerce_jsonb(row["spec"])
        sim_dict = _coerce_jsonb(row["simulation_plan"])
        metric_dict = _coerce_jsonb(row["metric_definition"])
        if not spec_dict:
            raise WorkflowNotReadyError(
                f"workflow {workflow_id!r} has no spec — generate one first."
            )
        if not sim_dict:
            raise WorkflowNotReadyError(
                f"workflow {workflow_id!r} has no simulation_plan — "
                "re-run POST /api/nl-gen/generate to populate it."
            )
        if not metric_dict:
            raise WorkflowNotReadyError(
                f"workflow {workflow_id!r} has no metric_definition — "
                "re-run POST /api/nl-gen/generate to populate it."
            )

        case = await get_eval_case(conn, eval_case_id)
        if case is None or case.workflow_id != workflow_id:
            raise EvalCaseNotFoundError(
                f"eval case {eval_case_id} not found on workflow "
                f"{workflow_id!r}"
            )

    spec = WorkflowSpec.model_validate(spec_dict)
    sim_plan = SimulationPlan.model_validate(sim_dict)
    metric = MetricDefinition.model_validate(metric_dict)
    generated_case = _eval_case_to_generated(case, spec.id)

    # Sim namespace exec happens outside the DB call so the connection
    # is released before the LLM round-trip. Caller-tag matches the
    # `caller=` convention from eval_replay.
    namespace = exec_sim_module(sim_plan, spec, caller="try-runner")

    # Single-call budget — Try-it never recurses or retries, so the cap
    # is effectively "never trip"; we use TokenBudget purely as a
    # token-counter on the predict_one path.
    budget = TokenBudget(max_tokens=10**9)
    started = time.perf_counter()
    error_class: str | None = None
    error_message: str | None = None
    prediction = None
    try:
        prediction = await predict_one(
            client,
            generated_case,
            spec=spec,
            metric=metric,
            namespace=namespace,
            model=model,
            max_tokens=max_tokens,
            budget=budget,
        )
    except AgentSolverError as exc:
        error_class = type(exc).__name__
        error_message = str(exc)
    duration_ms = int((time.perf_counter() - started) * 1000)

    in_tok = budget.used_input
    out_tok = budget.used_output
    cost = compute_cost_usd(model, in_tok, out_tok)

    if prediction is None:
        # Agent call failed — surface a Result with the error in the
        # trace so the UI can render the failure inline without a
        # separate error branch. The route layer also maps the original
        # exception to HTTP 502 for callers that want structured errors.
        trace = _trace_pair(
            case_id=generated_case.case_id,
            model=model,
            duration_ms=duration_ms,
            passed=False,
            error_class=error_class,
            error_message=error_message,
            output=None,
        )
        return TryItResult(
            case_id=generated_case.case_id,
            expected_value=generated_case.expected_value,
            actual_value=None,
            rationale=error_message or "(no rationale — agent call failed)",
            passed=False,
            model=model,
            duration_ms=duration_ms,
            cost_usd=cost,
            input_tokens=in_tok,
            output_tokens=out_tok,
            trace=trace,
        )

    passed = prediction.value == generated_case.expected_value
    trace = _trace_pair(
        case_id=generated_case.case_id,
        model=model,
        duration_ms=duration_ms,
        passed=passed,
        error_class=None,
        error_message=None,
        output=prediction.value,
    )
    return TryItResult(
        case_id=generated_case.case_id,
        expected_value=generated_case.expected_value,
        actual_value=prediction.value,
        rationale=prediction.rationale,
        passed=passed,
        model=prediction.model,
        duration_ms=duration_ms,
        cost_usd=cost,
        input_tokens=in_tok,
        output_tokens=out_tok,
        trace=trace,
    )


__all__ = [
    "TryItResult",
    "TryRunnerError",
    "WorkflowNotReadyError",
    "EvalCaseNotFoundError",
    "compute_cost_usd",
    "try_one_eval_case",
]
