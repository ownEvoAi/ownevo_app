"""W6 — 30-day M5 replay across parallel conditions (TODO-8).

Orchestrates the four M5 conditions per
[`ownevo_docs/benchmarks/m5-code-gen-loop.md`](../../../../../ownevo_docs/benchmarks/m5-code-gen-loop.md):

  * **A — Frozen baseline.** Skill files baked into the sandbox image, no
    agent. Lift curve is flat at the baseline `val_score`.
  * **B — Static frontier LLM.** Single-shot prediction without LightGBM.
    Deferred for W6 (sanity check, not load-bearing for the YC demo —
    see ``SUPPORTED_CONDITIONS``).
  * **C — ownEvo loop, autonomous.** Agent proposes → gate → auto-approve
    every gate-pass via ``approver_mode='autonomous'``. NeoSigma-shaped
    setup.
  * **D — ownEvo loop, gated.** Agent proposes → gate → LLM-judge
    approver decides admit/reject (``approver_mode='llm-judge'``). The
    actual product surface.

Why parallel: sequential 30-day replay is ~150h wall time (4 conditions ×
~30 iterations × ~75 min/iter on the agent loop). 4-way parallel ≈ 37h
via ``asyncio.gather`` over the conditions. Within a single condition
the iterations run sequentially because the gate's ``best_ever_score``
depends on the prior iteration — only the cross-condition fan-out is
parallel.

Why a shared Postgres (vs four Docker Compose stacks): the schema is
already keyed by ``workflow_id``; four conditions use four different IDs
in the same DB and the merge step issues one parameterized query per condition. Process
isolation is provided per-iteration by ``run_improvement_loop.py`` (each
invocation creates its own asyncpg connection + Docker sandbox).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from ownevo_kernel.approvers import APPROVER_AUTONOMOUS, APPROVER_LLM_JUDGE, APPROVER_NONE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — condition labels
# ---------------------------------------------------------------------------

CONDITION_A_FROZEN = "A"
CONDITION_B_STATIC_LLM = "B"  # deferred, see module docstring
CONDITION_C_LOOP_AUTONOMOUS = "C"
CONDITION_D_LOOP_GATED = "D"

#: The conditions wired into the W6 30-day replay infra. B is deferred.
SUPPORTED_CONDITIONS: tuple[str, ...] = (
    CONDITION_A_FROZEN,
    CONDITION_C_LOOP_AUTONOMOUS,
    CONDITION_D_LOOP_GATED,
)

_STDERR_TAIL_CHARS = 500

#: Default workflow_id prefix; full id is f"{prefix}-{condition_letter.lower()}".
DEFAULT_WORKFLOW_PREFIX = "m5-condition"

#: Default approver mode per condition. Maps onto run_improvement_loop's
#: ``--approver`` flag.
_CONDITION_APPROVER: dict[str, str] = {
    CONDITION_A_FROZEN: APPROVER_NONE,
    CONDITION_C_LOOP_AUTONOMOUS: APPROVER_AUTONOMOUS,
    CONDITION_D_LOOP_GATED: APPROVER_LLM_JUDGE,
}


def workflow_id_for_condition(
    condition: str,
    *,
    prefix: str = DEFAULT_WORKFLOW_PREFIX,
) -> str:
    """Convention-bound workflow_id for a condition (``A`` → ``m5-condition-a``)."""
    if condition not in (
        CONDITION_A_FROZEN,
        CONDITION_B_STATIC_LLM,
        CONDITION_C_LOOP_AUTONOMOUS,
        CONDITION_D_LOOP_GATED,
    ):
        raise ValueError(f"unknown condition: {condition!r}")
    return f"{prefix}-{condition.lower()}"


def approver_mode_for_condition(condition: str) -> str:
    """Default approver mode per condition. Used to default ``ConditionSpec.approver_mode``."""
    if condition not in _CONDITION_APPROVER:
        raise ValueError(
            f"approver mode not defined for condition {condition!r}; "
            f"supported: {sorted(_CONDITION_APPROVER)}",
        )
    return _CONDITION_APPROVER[condition]


# ---------------------------------------------------------------------------
# Type schema — per-iteration outcomes, per-condition results, full report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConditionSpec:
    """Per-condition configuration for the parallel orchestrator.

    ``condition`` is one of ``SUPPORTED_CONDITIONS``. ``workflow_id`` is
    typically derived via ``workflow_id_for_condition`` but explicit so
    the caller can namespace runs (e.g. ``m5-condition-c-2026-05-08``).
    ``n_iterations`` is the number of agent-loop cycles for C/D; condition
    A ignores it (the frozen baseline runs once and is plotted flat).
    ``approver_mode`` overrides the default mapping when set.
    """

    condition: str
    workflow_id: str
    n_iterations: int
    approver_mode: str | None = None

    def __post_init__(self) -> None:
        if self.condition not in SUPPORTED_CONDITIONS:
            raise ValueError(
                f"condition {self.condition!r} not supported in W6 replay; "
                f"supported: {SUPPORTED_CONDITIONS}",
            )
        if self.n_iterations < 1:
            raise ValueError(
                f"n_iterations must be ≥ 1, got {self.n_iterations}",
            )
        if not self.workflow_id or not self.workflow_id.strip():
            raise ValueError("workflow_id must be a non-empty string")
        if self.approver_mode is not None and self.approver_mode not in (
            "none",
            "autonomous",
            "llm-judge",
        ):
            raise ValueError(
                f"approver_mode must be one of "
                f"('none', 'autonomous', 'llm-judge'), got {self.approver_mode!r}",
            )

    @property
    def effective_approver_mode(self) -> str:
        """Resolve the approver mode — explicit override wins; else condition default."""
        return self.approver_mode or approver_mode_for_condition(self.condition)


@dataclass(frozen=True)
class IterationOutcome:
    """One iteration's row, projected for the lift chart + analytics."""

    iteration_index: int
    decision: str  # "gate-pass" | "gate-blocked-regression" | "gate-blocked-no-improvement" | "sandbox-error" | "running"
    val_score: float | None
    best_ever_score_before: float | None
    best_ever_score_after: float | None
    approval_decision: str | None  # "approve" | "reject" | None
    approver_type: str | None  # "human" | "llm-judge" | "autonomous" | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration_index": self.iteration_index,
            "decision": self.decision,
            "val_score": self.val_score,
            "best_ever_score_before": self.best_ever_score_before,
            "best_ever_score_after": self.best_ever_score_after,
            "approval_decision": self.approval_decision,
            "approver_type": self.approver_type,
        }


@dataclass(frozen=True)
class ConditionResult:
    """All iterations for one condition, ordered by ``iteration_index``."""

    condition: str
    workflow_id: str
    iterations: tuple[IterationOutcome, ...]

    @property
    def n_iterations(self) -> int:
        return len(self.iterations)

    @property
    def best_ever_curve(self) -> tuple[float | None, ...]:
        """Per-iteration best_ever_score_after — the lift chart Y-axis."""
        return tuple(it.best_ever_score_after for it in self.iterations)

    @property
    def final_best_ever(self) -> float | None:
        """Last non-null ``best_ever_score_after`` (the published headline number)."""
        for it in reversed(self.iterations):
            if it.best_ever_score_after is not None:
                return it.best_ever_score_after
        return None

    @property
    def n_gate_passes(self) -> int:
        return sum(1 for it in self.iterations if it.decision == "gate-pass")

    @property
    def n_gate_blocked_regression(self) -> int:
        return sum(1 for it in self.iterations if it.decision == "gate-blocked-regression")

    @property
    def n_gate_blocked_no_improvement(self) -> int:
        return sum(
            1 for it in self.iterations if it.decision == "gate-blocked-no-improvement"
        )

    @property
    def n_sandbox_errors(self) -> int:
        return sum(1 for it in self.iterations if it.decision == "sandbox-error")

    @property
    def n_approvals(self) -> int:
        return sum(1 for it in self.iterations if it.approval_decision == "approve")

    @property
    def n_rejections(self) -> int:
        return sum(1 for it in self.iterations if it.approval_decision == "reject")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "workflow_id": self.workflow_id,
            "n_iterations": self.n_iterations,
            "n_gate_passes": self.n_gate_passes,
            "n_gate_blocked_regression": self.n_gate_blocked_regression,
            "n_gate_blocked_no_improvement": self.n_gate_blocked_no_improvement,
            "n_sandbox_errors": self.n_sandbox_errors,
            "n_approvals": self.n_approvals,
            "n_rejections": self.n_rejections,
            "final_best_ever": self.final_best_ever,
            "best_ever_curve": list(self.best_ever_curve),
            "iterations": [it.to_dict() for it in self.iterations],
        }


@dataclass(frozen=True)
class ThirtyDayReport:
    """Full multi-condition replay outcome."""

    conditions: dict[str, ConditionResult]
    started_at: datetime
    ended_at: datetime

    @property
    def wall_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()

    def lift_over_baseline(self, baseline_condition: str = CONDITION_A_FROZEN) -> dict[str, float | None]:
        """Per-condition (final_best_ever - baseline_final). Useful for the gate."""
        baseline = self.conditions.get(baseline_condition)
        if baseline is None or baseline.final_best_ever is None:
            return {}
        baseline_score = baseline.final_best_ever
        return {
            cond: (
                None
                if res.final_best_ever is None
                else res.final_best_ever - baseline_score
            )
            for cond, res in self.conditions.items()
            if cond != baseline_condition
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "wall_seconds": self.wall_seconds,
            "conditions": {
                cond: res.to_dict() for cond, res in self.conditions.items()
            },
            "lift_over_baseline": self.lift_over_baseline(),
        }


# ---------------------------------------------------------------------------
# DB read — merge results from all conditions' workflow_ids
# ---------------------------------------------------------------------------


_MERGE_QUERY = """
SELECT
    i.iteration_index,
    i.state::text                    AS decision,
    i.val_score,
    i.best_ever_score_before,
    i.best_ever_score_after,
    a.decision                       AS approval_decision,
    a.approver_type::text            AS approver_type
FROM iterations i
LEFT JOIN proposals p ON p.iteration_id = i.id
LEFT JOIN approvals a ON a.proposal_id  = p.id
WHERE i.workflow_id = $1
ORDER BY i.iteration_index ASC
"""


async def merge_results(
    conn: asyncpg.Connection,
    *,
    condition_workflow_ids: dict[str, str],
) -> dict[str, ConditionResult]:
    """Aggregate iterations per workflow_id into typed ``ConditionResult``\\ s.

    Reads ``iterations`` LEFT JOIN ``proposals`` LEFT JOIN ``approvals``
    for each condition's workflow. Returns one ``ConditionResult`` per
    requested condition; conditions with zero iterations get an empty
    ``iterations`` tuple. Order of conditions in the output matches the
    insertion order of ``condition_workflow_ids``.
    """
    results: dict[str, ConditionResult] = {}
    for condition, workflow_id in condition_workflow_ids.items():
        rows = await conn.fetch(_MERGE_QUERY, workflow_id)
        outcomes = tuple(
            IterationOutcome(
                iteration_index=row["iteration_index"],
                decision=row["decision"],
                val_score=_to_float(row["val_score"]),
                best_ever_score_before=_to_float(row["best_ever_score_before"]),
                best_ever_score_after=_to_float(row["best_ever_score_after"]),
                approval_decision=row["approval_decision"],
                approver_type=row["approver_type"],
            )
            for row in rows
        )
        results[condition] = ConditionResult(
            condition=condition,
            workflow_id=workflow_id,
            iterations=outcomes,
        )
    return results


def _to_float(value: Any) -> float | None:
    """Coerce numeric-or-None DB values (Decimal / int / float) to float | None."""
    if value is None:
        return None
    return float(value)


# ---------------------------------------------------------------------------
# Subprocess driver — invoke run_improvement_loop.py once per iteration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubprocessResult:
    """Outcome of one iteration subprocess. ``summary`` is the JSON payload
    parsed from the loop's stdout when exit_code == 0; ``None`` on failure."""

    exit_code: int
    summary: dict[str, Any] | None
    stderr_tail: str  # last ~500 chars; for diagnostics


async def run_improvement_loop_subprocess(
    *,
    workflow_id: str,
    approver_mode: str,
    extra_args: Sequence[str] = (),
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    judge_model: str | None = None,
    timeout_s: float | None = None,
) -> SubprocessResult:
    """Invoke ``scripts/run_improvement_loop.py`` as a subprocess for one iteration.

    Returns a ``SubprocessResult`` rather than raising on non-zero exit
    codes — the orchestrator decides whether to halt or continue. Each
    invocation creates its own DB connection + Docker sandbox; this function
    is the trust seam between the parallel orchestrator and the loop.

    ``cwd`` defaults to ``apps/kernel`` (auto-detected from this file's
    location). ``env`` is merged onto ``os.environ``; pass an override here
    to give a condition its own ``OWNEVO_DATABASE_URL`` (Phase-2; today all
    conditions share one DB and isolate via ``workflow_id``).
    """
    if cwd is None:
        # this file lives at apps/kernel/src/ownevo_kernel/replay/thirty_day.py
        cwd = Path(__file__).resolve().parents[3]

    cmd: list[str] = [
        sys.executable,
        "scripts/run_improvement_loop.py",
        "--workflow-id",
        workflow_id,
        "--approver",
        approver_mode,
        "--no-seed",
        *list(extra_args),
    ]
    if judge_model is not None and approver_mode == "llm-judge":
        cmd.extend(["--judge-model", judge_model])

    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env=proc_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return SubprocessResult(
            exit_code=-1,
            summary=None,
            stderr_tail=f"timed out after {timeout_s}s",
        )
    except (asyncio.CancelledError, BaseException):
        # Task cancelled (e.g. TaskGroup abort) or any unexpected error —
        # kill the OS subprocess so it doesn't become an orphan.
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        raise

    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")
    stderr_tail = stderr[-_STDERR_TAIL_CHARS:] if stderr else ""

    if proc.returncode != 0:
        return SubprocessResult(
            exit_code=proc.returncode or -1,
            summary=None,
            stderr_tail=stderr_tail,
        )

    summary = _parse_loop_summary(stdout)
    return SubprocessResult(
        exit_code=0,
        summary=summary,
        stderr_tail=stderr_tail,
    )


def _parse_loop_summary(stdout: str) -> dict[str, Any] | None:
    """Extract the trailing JSON summary from run_improvement_loop's stdout.

    The loop ends with ``print(json.dumps(summary, indent=2))``. Find the
    last balanced ``{...}`` block — bracket counter from the rightmost
    ``}`` walking backwards. Returns ``None`` if no JSON object is found.
    """
    if not stdout:
        return None
    # Walk back from end, finding the matching brace pair.
    end = stdout.rfind("}")
    if end < 0:
        return None
    depth = 0
    start = -1
    for i in range(end, -1, -1):
        ch = stdout[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start < 0:
        return None
    try:
        return json.loads(stdout[start : end + 1])
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-condition drivers
# ---------------------------------------------------------------------------


ProgressCallback = Callable[[str, int, int, SubprocessResult], None]
"""Optional progress hook: ``(condition, iter_index, n_total, result)``."""


async def run_condition_loop(
    spec: ConditionSpec,
    *,
    extra_args: Sequence[str] = (),
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    judge_model: str | None = None,
    iteration_timeout_s: float | None = None,
    halt_on_error: bool = False,
    progress: ProgressCallback | None = None,
) -> list[SubprocessResult]:
    """Run ``spec.n_iterations`` iterations of the agent loop sequentially
    for one condition. Returns the per-iteration ``SubprocessResult`` list.

    Sequential within a condition (not parallel): the gate's
    ``best_ever_score`` for iteration N depends on the iteration N-1 row.
    Parallelism happens *across* conditions in
    ``run_all_conditions_parallel``.

    ``halt_on_error=False`` (default) means a failed iteration is logged
    and the loop continues — partial progress survives in the DB, the
    merge step picks up whatever rows landed. ``halt_on_error=True``
    raises ``RuntimeError`` on the first non-zero exit (useful for tests).
    """
    if spec.condition == CONDITION_A_FROZEN:
        # Condition A is the frozen baseline — no agent. The seeded
        # baseline iteration already lives in the DB; the lift curve
        # for A is plotted flat at that score. No subprocess work.
        logger.info(
            "condition A: frozen baseline; skipping agent loop (lift curve flat)",
        )
        return []

    approver_mode = spec.effective_approver_mode
    results: list[SubprocessResult] = []

    for i in range(spec.n_iterations):
        logger.info(
            "condition %s iter %d/%d: launching subprocess (workflow=%s, approver=%s)",
            spec.condition,
            i + 1,
            spec.n_iterations,
            spec.workflow_id,
            approver_mode,
        )
        result = await run_improvement_loop_subprocess(
            workflow_id=spec.workflow_id,
            approver_mode=approver_mode,
            extra_args=extra_args,
            cwd=cwd,
            env=env,
            judge_model=judge_model,
            timeout_s=iteration_timeout_s,
        )
        results.append(result)

        if progress is not None:
            try:
                progress(spec.condition, i, spec.n_iterations, result)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "progress callback raised (continuing)",
                    exc_info=True,
                )

        if result.exit_code != 0:
            logger.warning(
                "condition %s iter %d: exit=%d stderr=%s",
                spec.condition,
                i,
                result.exit_code,
                result.stderr_tail,
            )
            if halt_on_error:
                raise RuntimeError(
                    f"condition {spec.condition} iter {i}: "
                    f"exit={result.exit_code}, stderr_tail={result.stderr_tail!r}",
                )

    return results


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


async def run_all_conditions_parallel(
    specs: Sequence[ConditionSpec],
    *,
    db_url: str | None = None,
    extra_loop_args: Sequence[str] = (),
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    judge_model: str | None = None,
    iteration_timeout_s: float | None = None,
    halt_on_error: bool = False,
    progress: ProgressCallback | None = None,
) -> ThirtyDayReport:
    """Drive every spec in parallel via ``asyncio.gather``, then merge.

    Parallelism is across conditions only — within a condition, iterations
    run sequentially because of the gate's best-ever dependency.

    ``db_url`` defaults to ``OWNEVO_DATABASE_URL`` (matches the loop's
    convention). The merge step opens its own short-lived connection.
    """
    if not specs:
        raise ValueError("specs must contain at least one ConditionSpec")

    # Validate uniqueness of (condition, workflow_id) so the merge step
    # doesn't double-count.
    seen_conds: set[str] = set()
    seen_workflows: set[str] = set()
    for spec in specs:
        if spec.condition in seen_conds:
            raise ValueError(f"duplicate condition in specs: {spec.condition!r}")
        if spec.workflow_id in seen_workflows:
            raise ValueError(f"duplicate workflow_id in specs: {spec.workflow_id!r}")
        seen_conds.add(spec.condition)
        seen_workflows.add(spec.workflow_id)

    started_at = datetime.now(timezone.utc)

    async with asyncio.TaskGroup() as tg:
        for spec in specs:
            tg.create_task(
                run_condition_loop(
                    spec,
                    extra_args=extra_loop_args,
                    cwd=cwd,
                    env=env,
                    judge_model=judge_model,
                    iteration_timeout_s=iteration_timeout_s,
                    halt_on_error=halt_on_error,
                    progress=progress,
                )
            )

    ended_at = datetime.now(timezone.utc)

    resolved_db_url = db_url or os.environ.get("OWNEVO_DATABASE_URL")
    if not resolved_db_url:
        raise RuntimeError(
            "merge step needs OWNEVO_DATABASE_URL or an explicit db_url",
        )

    condition_workflow_ids = {spec.condition: spec.workflow_id for spec in specs}
    conn = await asyncpg.connect(resolved_db_url, timeout=10)
    try:
        merged = await merge_results(conn, condition_workflow_ids=condition_workflow_ids)
    finally:
        await conn.close()

    return ThirtyDayReport(
        conditions=merged,
        started_at=started_at,
        ended_at=ended_at,
    )


__all__ = [
    "CONDITION_A_FROZEN",
    "CONDITION_B_STATIC_LLM",
    "CONDITION_C_LOOP_AUTONOMOUS",
    "CONDITION_D_LOOP_GATED",
    "DEFAULT_WORKFLOW_PREFIX",
    "SUPPORTED_CONDITIONS",
    "ConditionSpec",
    "ConditionResult",
    "IterationOutcome",
    "ProgressCallback",
    "SubprocessResult",
    "ThirtyDayReport",
    "approver_mode_for_condition",
    "merge_results",
    "run_all_conditions_parallel",
    "run_condition_loop",
    "run_improvement_loop_subprocess",
    "workflow_id_for_condition",
]
