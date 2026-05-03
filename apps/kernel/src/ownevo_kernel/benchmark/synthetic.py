"""SyntheticBenchmarkRunner — gate self-test substrate (W2.2a).

A trivial in-process benchmark used by the gate self-test harness.
Each task is a `(input, expected)` pair plus an optional judge; the
runner invokes a `skill` callable, compares the output, and returns
1.0 / 0.0. Useful because:

  1. The gate self-test must be independent of M5 — testing the gate
     against M5 itself is circular.
  2. Tests run in milliseconds (no Docker, no DB, no LLM).
  3. The Protocol shape exercised here is the same one the
     M5BenchmarkRunner will implement, so a green self-test means the
     gate's runner-consumption path is correct.

Skill failures are caught and scored as 0.0 — the synthetic runner
mirrors how the real M5 runner will treat tool-call exceptions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .types import BenchmarkResult


@dataclass(frozen=True)
class SyntheticTask:
    """One pass/fail synthetic case.

    The default judge is `==`. Pass a custom `judge` for tolerance-style
    matching (e.g., `abs(out - exp) < 0.01`). Custom judges return True
    for full credit; numeric partial credit is not modeled here (real
    benchmarks compute partial credit inside the runner).
    """

    id: str
    input: Any
    expected: Any
    judge: Callable[[Any, Any], bool] | None = None


SkillFn = Callable[[Any], Any]
"""A synthetic skill — Python callable that takes a task input and
returns an output. Real workflows put the agent's code in the sandbox;
here we keep it in-process so the self-test isolates gate logic from
sandbox behavior."""


def _default_judge(output: Any, expected: Any) -> bool:
    return output == expected


@dataclass
class SyntheticBenchmarkRunner:
    """In-process benchmark runner over a fixed task set.

    `task_ids=None` runs every task in declaration order. The runner is
    deterministic — same skill + same tasks → same `BenchmarkResult`.
    """

    tasks: tuple[SyntheticTask, ...]
    skill: SkillFn
    _by_id: dict[str, SyntheticTask] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len({t.id for t in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate task IDs in SyntheticBenchmarkRunner")
        # Use object.__setattr__ to populate the cache field even though
        # the dataclass is frozen-by-convention from the caller's view.
        object.__setattr__(self, "_by_id", {t.id: t for t in self.tasks})

    async def run(
        self,
        task_ids: list[str] | None = None,
    ) -> BenchmarkResult:
        ids = self._resolve_ids(task_ids)
        rewards: dict[str, float | None] = {}
        for tid in ids:
            task = self._by_id[tid]
            rewards[tid] = self._score_one(task)
        return BenchmarkResult(rewards=rewards)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_ids(self, requested: Iterable[str] | None) -> list[str]:
        if requested is None:
            return [t.id for t in self.tasks]
        unknown = [tid for tid in requested if tid not in self._by_id]
        if unknown:
            raise KeyError(f"Unknown task IDs: {unknown}")
        return list(requested)

    def _score_one(self, task: SyntheticTask) -> float | None:
        judge = task.judge or _default_judge
        try:
            output = self.skill(task.input)
        except BaseException:
            # Mirror real benchmarks: any uncaught exception in the skill
            # scores as a failure (0.0). We don't return None for
            # exceptions because we DID get a result — the result is
            # "the skill crashed", which is a definite failure, not a
            # missing measurement.
            return 0.0
        return 1.0 if judge(output, task.expected) else 0.0
