"""Eval runner — generated eval cases → score (A4.3).

The orchestration seam between the NL-gen artifacts (A3.1 spec, A3.2 sim
plan, A4.1 eval cases, A4.2 metric) and the regression gate. Composes
`replay_set` + `compute_metric` + `check_against_spec` into a single
typed report.

Two callable surfaces:

  * `run_replay(case_set, plan, spec, metric)` — deterministic replay
    against the rendered sim. No model in the loop. This is the
    A4.3 load-bearing path: `make eval-replay WORKFLOW=...` invokes
    this. Every case's `actual_value` comes from the sim's hidden
    ground-truth label, so against the A4.1 fixtures every case passes
    by construction (the fixtures pin replay-equivalence).
  * `build_inspect_task(case_set, plan, spec)` — adapts the trio into
    an `inspect_ai.Task` so an agent (A4.4+) can be wired through
    `inspect_ai.eval(task, model="anthropic/...")`. Lazy import — the
    `inspect-ai` package lives in the `eval` extra so the runtime
    kernel install stays slim.

Why both: A4.3 ships a working score today (`run_replay`) without
requiring an agent or the heavy Inspect AI install. The Task adapter
proves the integration shape and is what A5+ will hit when there's an
agent producing labels instead of the sim. The two paths share the
report dataclass so the gate's downstream consumers don't care which
path fed them.
"""

from __future__ import annotations

from .runner import (
    EvalCaseOutcome,
    EvalRunReport,
    run_replay,
    run_with_agent,
)

__all__ = [
    "EvalCaseOutcome",
    "EvalRunReport",
    "run_replay",
    "run_with_agent",
    "build_inspect_task",
    # A4.4 — re-exported lazily so installs without the `agent` extra
    # don't fail at import time.
    "AgentPrediction",
    "AgentSolverError",
    "NoPredictToolUseError",
    "PredictToolValidationError",
    "predict_one",
    "solve_with_agent",
]


_AGENT_SOLVER_LAZY_NAMES = {
    "AgentPrediction",
    "AgentSolverError",
    "NoPredictToolUseError",
    "PredictToolValidationError",
    "predict_one",
    "solve_with_agent",
}


def __getattr__(name: str):  # pragma: no cover - thin lazy-import shim
    if name == "build_inspect_task":
        from .inspect_task import build_inspect_task as _bit

        return _bit
    if name in _AGENT_SOLVER_LAZY_NAMES:
        from . import agent_solver

        return getattr(agent_solver, name)
    raise AttributeError(name)
