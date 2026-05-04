"""LabourBenchmarkRunner — non-M5 substrate proof (W2.7).

A second `BenchmarkRunner` implementation that exercises the same
substrate primitives (skill registry → sandbox → eval cases → gate →
audit) on a workflow distinct from M5. Confirms the kernel is
domain-agnostic before Phase 2 starts.

Domain shape
------------
Each task is one proposed shift assignment. The skill validates the
assignment against two hand-coded rules drawn from the Labour
management failure-mode taxonomy in `ownEvo_MVP_mocks.md`:

  * weekly hours over the cap → reject(`overtime_cap`)
  * required skill not in the worker's skill set → reject(`skill_mismatch`)
  * else → approve(`clean`)

The runner batches all cases into one sandbox call (matches the M5
shape: one `run_pipeline` per `run()`, the skill iterates internally).
The skill body is registered through `register_skill` like any other
agent-mutable skill — the substrate doesn't know the domain.

Why this lives in `benchmark/`
------------------------------
Same reason `m5.py` and `synthetic.py` do: it implements the
`BenchmarkRunner` Protocol. Future workflow proofs (union review,
customer support) will land here too if/when they need a runner with a
non-M5 scoring shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..sandbox import LocalDockerSandbox
from .types import BenchmarkResult


@dataclass(frozen=True)
class LabourCase:
    """One shift assignment to validate.

    `expected` is the full decision dict the skill should emit for this
    case — the runner scores 1.0 on exact match, 0.0 otherwise. The
    runner does not interpret `reason` semantically; the skill body owns
    the reason vocabulary.
    """

    task_id: str
    shift_hours: int
    weekly_hours_so_far: int
    required_skill: str
    worker_skills: tuple[str, ...]
    expected: dict[str, Any]


class LabourBenchmarkError(RuntimeError):
    """The sandboxed skill ran but did not produce a parseable per-case
    decision.

    Distinct from a sandbox `error_class` (Timeout / OOM / Crash) — those
    bubble up through `run_pipeline` and surface as `None` rewards via
    the gate's SANDBOX_ERROR short-circuit. This one means the run
    completed (`status="ok"`) but the output JSON is missing keys or has
    wrong shape — the substrate's wire contract was violated.
    """


@dataclass
class LabourBenchmarkRunner:
    """`BenchmarkRunner` over a fixed list of `LabourCase`s.

    Attributes:
        cases: Tuple of cases. Order is preserved; `task_ids` filtering
            mirrors `SyntheticBenchmarkRunner`.
        skill_content: The skill body (with frontmatter docstring) the
            sandbox should execute. Caller registers via
            `register_skill` before constructing the runner.
        sandbox: A `LocalDockerSandbox`. Reuses the M5 image — the skill
            is stdlib-only so any image with Python 3 works; M5 image is
            already built by `make sandbox-image-m5`.
        timeout_seconds / memory_mb: Per-call resource limits passed
            through to `run_pipeline`. Defaults are generous for a
            stdlib workload.
    """

    cases: tuple[LabourCase, ...]
    skill_content: str
    sandbox: LocalDockerSandbox
    timeout_seconds: float = 30.0
    memory_mb: int = 256
    last_outputs: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if len({c.task_id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate task_id in LabourBenchmarkRunner.cases")

    async def run(
        self,
        task_ids: list[str] | None = None,
    ) -> BenchmarkResult:
        from ..agent_tools.run_pipeline import run_pipeline

        cases = self._select_cases(task_ids)
        input_data = {
            "cases": [
                {
                    "task_id": c.task_id,
                    "shift_hours": c.shift_hours,
                    "weekly_hours_so_far": c.weekly_hours_so_far,
                    "required_skill": c.required_skill,
                    "worker_skills": list(c.worker_skills),
                }
                for c in cases
            ],
        }

        result = await run_pipeline(
            self.sandbox,
            skill_content=self.skill_content,
            input_data=input_data,
            timeout_seconds=self.timeout_seconds,
            memory_mb=self.memory_mb,
        )

        if not result.ok:
            # Mirror M5SandboxError: surface enough context to diagnose
            # without re-running. The gate runner's try/except converts
            # this into a SANDBOX_ERROR decision — D3 behavior preserved.
            raise LabourBenchmarkError(
                f"Sandboxed labour skill did not return ok: status={result.status}, "
                f"error_class={result.error_class}, error={result.error!r}, "
                f"stderr={(result.raw_stderr or '')[-500:]!r}",
            )

        outputs = result.outputs
        if outputs is None:
            tail = (result.raw_stdout or "").rstrip().splitlines()[-1:] or ["<empty>"]
            raise LabourBenchmarkError(
                "Sandboxed labour skill did not emit a JSON object on the last "
                f"stdout line. Last line: {tail[0][:500]!r}",
            )
        self.last_outputs = outputs

        decisions = _index_decisions(outputs)
        rewards: dict[str, float | None] = {}
        for case in cases:
            decision = decisions.get(case.task_id)
            if decision is None:
                # Skill silently dropped this task — score as 0.0 (skill
                # crashed for this case), not None (which would poison the
                # whole gate run via the SANDBOX_ERROR short-circuit).
                rewards[case.task_id] = 0.0
                continue
            rewards[case.task_id] = 1.0 if decision == case.expected else 0.0
        return BenchmarkResult(rewards=rewards)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_cases(self, task_ids: list[str] | None) -> list[LabourCase]:
        if task_ids is None:
            return list(self.cases)
        by_id = {c.task_id: c for c in self.cases}
        unknown = [tid for tid in task_ids if tid not in by_id]
        if unknown:
            raise KeyError(f"Unknown task_ids: {unknown}")
        return [by_id[tid] for tid in task_ids]


def _index_decisions(outputs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pull `{task_id: decision}` out of the skill's `{"results": [...]}`.

    Tolerates malformed entries: rows missing `task_id` or `decision`
    are dropped (the runner scores those cases as 0.0 above). Entirely
    missing `results` raises — that's a wire-contract failure, not a
    per-task miss.
    """
    rows = outputs.get("results")
    if not isinstance(rows, list):
        raise LabourBenchmarkError(
            f"Sandboxed labour skill output missing 'results' list; "
            f"got keys: {sorted(outputs.keys())!r}",
        )
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        tid = row.get("task_id")
        decision = row.get("decision")
        if isinstance(tid, str) and isinstance(decision, dict):
            indexed[tid] = decision
    return indexed


__all__ = [
    "LabourBenchmarkError",
    "LabourBenchmarkRunner",
    "LabourCase",
]
