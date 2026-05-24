"""Eval runner — generated eval cases → score (A4.3+).

The orchestration seam between the NL-gen artifacts (A3.1 spec, A3.2 sim
plan, A4.1 eval cases, A4.2 metric) and the regression gate. Composes
`replay_set` + `compute_metric` + `check_against_spec` into a single
typed report.

Five callable surfaces:

  * `run_replay(case_set, plan, spec, metric)` — deterministic replay
    against the rendered sim. No model in the loop. A4.3 load-bearing
    path (`make eval-replay WORKFLOW=...`).
  * `run_with_agent(case_set, plan, spec, metric, *, client, ...)` —
    same shape as `run_replay` but `actual_value`s come from a Claude
    agent (single-turn forced tool-use). A4.4 smoke-test entrypoint.
  * `run_with_mock_agent(case_set, plan, spec, metric, *, mock_config,
    iteration_index)` — zero-LLM counterpart to `run_with_agent`.
    Predictions come from a deterministic accuracy curve; returns the
    same `EvalRunReport` shape. Used when `workflows.sim_tier='mock'`.
  * `verify_determinism(case_set, plan, spec, metric)` — runs
    `run_replay` twice and asserts identical outcomes; raises
    `NondeterminismError` on the first divergence. A4.5 guardrail.
  * `build_inspect_task(case_set, plan, spec)` — adapts the trio into
    an `inspect_ai.Task`. Lazy import (requires the `eval` extra).

The non-Inspect paths share `EvalRunReport` so the gate's downstream
consumers don't care which path fed them.
"""

from __future__ import annotations

from .runner import (
    EvalCaseOutcome,
    EvalRunReport,
    run_replay,
    run_with_agent,
    run_with_mock_agent,
)
from .determinism import (
    NondeterminismError,
    verify_determinism,
)

__all__ = [
    "EvalCaseOutcome",
    "EvalRunReport",
    "run_replay",
    "run_with_agent",
    "run_with_mock_agent",
    "build_inspect_task",
    # A4.4 — re-exported lazily so installs without the `agent` extra
    # don't fail at import time.
    "AgentPrediction",
    "AgentSolverError",
    "NoPredictToolUseError",
    "PredictToolValidationError",
    "predict_one",
    "solve_with_agent",
    # A4.5 — guardrails. `TokenBudget` is defined in `token_budget.py`;
    # it imports `AgentSolverError` from `agent_solver` (which lives in
    # the `agent` extra), so both are lazy-shimmed together. `NondeterminismError`
    # and `verify_determinism` are sync and eagerly imported above.
    "TokenBudget",
    "TokenBudgetExceededError",
    "NondeterminismError",
    "verify_determinism",
]


_AGENT_SOLVER_LAZY_NAMES = {
    "AgentPrediction",
    "AgentSolverError",
    "NoPredictToolUseError",
    "PredictToolValidationError",
    "predict_one",
    "solve_with_agent",
}

_TOKEN_BUDGET_LAZY_NAMES = {
    "TokenBudget",
    "TokenBudgetExceededError",
}


def __getattr__(name: str):  # pragma: no cover - thin lazy-import shim
    if name == "build_inspect_task":
        from .inspect_task import build_inspect_task as _bit

        return _bit
    if name in _AGENT_SOLVER_LAZY_NAMES:
        from . import agent_solver

        return getattr(agent_solver, name)
    if name in _TOKEN_BUDGET_LAZY_NAMES:
        from . import token_budget

        return getattr(token_budget, name)
    raise AttributeError(name)
