"""Benchmark runner contract (W2.2 prerequisite).

A `BenchmarkRunner` knows how to drive one benchmark — load its tasks,
run an agent or skill against them, and report per-task rewards. The
gate consumes `BenchmarkResult` to compute val_score; the gate self-test
(W2.2a) consumes a SyntheticBenchmarkRunner that doesn't need any
external dataset.

Reward conventions:
  * Per-task reward is a float in [0.0, 1.0]. 1.0 = full credit.
  * `None` = no verifier result (timeout / crash / no answer).
    `None` counts as 0.0 in val_score so the agent can't game the
    aggregate by causing tasks to drop out.
  * val_score = arithmetic mean across all tasks the runner was asked
    to score (denominator = len(rewards), not len(non-None)).

Future runners (M5BenchmarkRunner in W2.6, Tau3BenchmarkRunner in W7-8)
implement the same Protocol with workflow-specific scoring inside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BenchmarkResult:
    """Per-task rewards from one benchmark run.

    `rewards` keys are task IDs (strings); values are normalized rewards
    in [0,1] or None for non-result. The result is intentionally
    minimal — gate-level summarization is the gate's job, not the
    runner's.
    """

    rewards: dict[str, float | None] = field(default_factory=dict)
    """task_id → reward in [0,1] or None for timeout/crash/no-result."""

    @property
    def val_score(self) -> float:
        """Mean reward across all tasks, treating None as 0.0.

        Empty result → 0.0 (no tasks attempted = no credit).
        """
        if not self.rewards:
            return 0.0
        return sum(0.0 if v is None else v for v in self.rewards.values()) / len(
            self.rewards,
        )

    @property
    def n_tasks(self) -> int:
        return len(self.rewards)

    @property
    def n_passed(self) -> int:
        """Tasks at full credit (reward >= 1.0). Used by the gate's
        suite-pass-rate step."""
        return sum(1 for v in self.rewards.values() if v is not None and v >= 1.0)

    @property
    def n_no_result(self) -> int:
        """Tasks that returned None — typically timeouts. Tracked so the
        gate can decide whether a regression is "real failure" vs
        "infrastructure noise"."""
        return sum(1 for v in self.rewards.values() if v is None)


@runtime_checkable
class BenchmarkRunner(Protocol):
    """Minimum surface for any benchmark."""

    async def run(
        self,
        task_ids: list[str] | None = None,
    ) -> BenchmarkResult:
        """Execute the benchmark on the given tasks.

        `task_ids=None` runs the full benchmark (the runner's notion of
        "all tasks"). `task_ids=[...]` runs a specific subset — used by
        the gate's regression-suite step.
        """
        ...
