"""Track 9.0.2 smoketest — runs the MockAgentSolver across iterations.

Drives `run_with_mock_agent` over a fixture trio (demand-prediction)
for N iterations against a scripted accuracy curve, asserts that the
observed val_score per iteration matches the target curve (modulo
rounding), and reports wall-clock + zero-LLM-call status.

Invoke via `make sim-mock-smoketest` (or directly:
`uv run python apps/kernel/scripts/sim_mock_smoketest.py`).

Doesn't touch the DB and doesn't go through iteration_runner — that's
covered by the DB-gated integration tests in `tests/`. The point of
this script is to make Slice A's contract observable from one command
without standing up a Postgres instance.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from ownevo_kernel.eval_runner.runner import run_with_mock_agent
from ownevo_kernel.nl_gen.fixtures import (
    DEMAND_PREDICTION_METRIC,
    DEMAND_PREDICTION_SIM_PLAN,
    DEMAND_PREDICTION_SPEC,
)
from ownevo_kernel.nl_gen.fixtures.eval_case_sets import (
    DEMAND_PREDICTION_EVAL_CASE_SET,
)
from ownevo_kernel.sim_tier import MockSimConfig

_ACCURACY_CURVE = [0.50, 0.65, 0.77, 0.80]
_DEFAULT_ACCURACY = 0.80
_WALL_CLOCK_BUDGET_S = 5.0


async def _main() -> int:
    # Belt-and-braces: nuke ANTHROPIC_API_KEY so the smoketest can't
    # silently fall back to a real LLM call if something ever broke
    # the mock dispatch. If a future regression routes through
    # solve_with_agent instead, the test fails loudly with a "no API
    # key" error rather than spending money.
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)

    case_set = DEMAND_PREDICTION_EVAL_CASE_SET
    n_cases = len(case_set.cases)
    config = MockSimConfig(
        accuracy_per_iteration=_ACCURACY_CURVE,
        default_accuracy=_DEFAULT_ACCURACY,
        seed=42,
    )

    print(
        f"sim-mock smoketest: workflow={DEMAND_PREDICTION_SPEC.id} "
        f"n_cases={n_cases} curve={_ACCURACY_CURVE} "
        f"default={_DEFAULT_ACCURACY}",
    )

    started = time.monotonic()
    observed: list[float] = []
    failures: list[str] = []

    for iteration_index in range(len(_ACCURACY_CURVE) + 1):
        target = config.accuracy_for(iteration_index)
        report = await run_with_mock_agent(
            case_set,
            DEMAND_PREDICTION_SIM_PLAN,
            DEMAND_PREDICTION_SPEC,
            DEMAND_PREDICTION_METRIC,
            mock_config=config,
            iteration_index=iteration_index,
        )
        # `report.value` is the metric's measure of the predictions.
        # For an accuracy-family metric (which the demand-prediction
        # fixture uses) this equals the fraction of cases that passed.
        # Per-iteration "exact match modulo rounding" is the contract:
        # round(n_cases * target) must equal n_pass.
        expected_n_pass = round(n_cases * target)
        observed.append(report.value)
        ok = report.n_pass == expected_n_pass
        marker = "OK " if ok else "FAIL"
        print(
            f"  iter {iteration_index}: target={target:.2f} "
            f"value={report.value:.4f} n_pass={report.n_pass}/"
            f"{report.n_total} (expected n_pass={expected_n_pass}) "
            f"meets_target={report.meets_target} [{marker}]",
        )
        if not ok:
            failures.append(
                f"iter {iteration_index}: expected n_pass="
                f"{expected_n_pass}, got {report.n_pass}",
            )

    elapsed = time.monotonic() - started
    print(f"wall clock: {elapsed * 1000:.1f}ms (budget {_WALL_CLOCK_BUDGET_S}s)")

    if failures:
        print("FAIL — accuracy contract violations:")
        for line in failures:
            print(f"  {line}")
        return 1
    if elapsed > _WALL_CLOCK_BUDGET_S:
        print(
            f"FAIL — wall clock {elapsed:.2f}s exceeds "
            f"{_WALL_CLOCK_BUDGET_S}s budget",
        )
        return 1

    print("PASS — every iteration hit its accuracy target; LLM-free + budget met.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
