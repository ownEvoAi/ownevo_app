"""W6 — 30-day M5 replay across parallel conditions (TODO-8).

Drives the four-condition M5 comparison (A: frozen baseline, C: loop
autonomous, D: loop + approval gate).
Conditions A (frozen baseline), C (loop autonomous), D (loop gated)
are wired in W6; condition B (static frontier LLM) is deferred.

Architecture: a single Postgres backs all conditions; each condition uses
a unique ``workflow_id`` so the merge step is a single ``UNION ALL``.
Within a condition, iterations run sequentially (the gate's
``best_ever_score`` for iteration N depends on iteration N-1). Across
conditions, ``asyncio.gather`` runs them concurrently — sequential =
~150h wall time, 4-way parallel ≈ 37h.

Each iteration is a fresh ``scripts/run_improvement_loop.py`` subprocess.
Process isolation is the existing improvement-loop's responsibility (own
DB connection, own Docker sandbox per call); the orchestrator just
fans out, gathers, and merges.

Exit codes
----------
0  replay completed and any required gates passed
1  one or more ``--require-*`` gates failed
2  ``OWNEVO_DATABASE_URL`` not set
3  could not connect to the DB
4  precondition failure (no supported conditions selected, etc.)

Usage
-----
  make m5-replay-30day                                  # default — all wired conditions
  python scripts/m5_replay_30day.py --conditions a,c    # subset
  python scripts/m5_replay_30day.py --max-iterations 3 --halt-on-error
  python scripts/m5_replay_30day.py --require-lift 0.05 --pretty
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from ownevo_kernel.replay import (  # noqa: E402
    CONDITION_A_FROZEN,
    DEFAULT_WORKFLOW_PREFIX,
    SUPPORTED_CONDITIONS,
    ConditionSpec,
    ThirtyDayReport,
    run_all_conditions_parallel,
    workflow_id_for_condition,
)
from ownevo_kernel.tenant_session import DEFAULT_WORKSPACE_ID, WorkspaceBindError, connect_workspace_conn  # noqa: E402

ENV_DB_URL = "OWNEVO_DATABASE_URL"

DEFAULT_MAX_ITERATIONS = 30
DEFAULT_CONDITIONS = ",".join(c.lower() for c in SUPPORTED_CONDITIONS)
# Keep in sync with run_improvement_loop.py:DEFAULT_JUDGE_MODEL
DEFAULT_JUDGE_MODEL = "claude-opus-4-7"


@dataclass(frozen=True)
class CliArgs:
    conditions: tuple[str, ...]  # uppercase letters, e.g. ("A", "C", "D")
    max_iterations: int
    workflow_prefix: str
    judge_model: str
    iteration_timeout_s: float | None
    halt_on_error: bool
    reset: bool
    pretty: bool
    require_lift: float | None
    extra_loop_args: tuple[str, ...]


def _positive_int(s: str) -> int:
    try:
        v = int(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected integer, got {s!r}") from exc
    if v <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {v}")
    return v


def _positive_float(s: str) -> float:
    try:
        v = float(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected float, got {s!r}") from exc
    if v <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {v}")
    return v


def _conditions_arg(s: str) -> tuple[str, ...]:
    """Parse ``--conditions a,c,d`` into uppercase tuple.

    Validates that every letter is in ``SUPPORTED_CONDITIONS`` (B not yet
    wired). Empty / whitespace-only entries are rejected.
    """
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError(
            "--conditions must list at least one condition (e.g. 'a,c,d')"
        )
    out: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        cond = raw.upper()
        if cond not in SUPPORTED_CONDITIONS:
            raise argparse.ArgumentTypeError(
                f"unknown condition {raw!r}; "
                f"supported: {','.join(c.lower() for c in SUPPORTED_CONDITIONS)}"
            )
        if cond in seen:
            raise argparse.ArgumentTypeError(f"duplicate condition: {raw!r}")
        seen.add(cond)
        out.append(cond)
    return tuple(out)


def _parse_args(argv: list[str] | None = None) -> CliArgs:
    parser = argparse.ArgumentParser(
        description="Drive the 30-day M5 replay across parallel conditions (W6).",
    )
    parser.add_argument(
        "--conditions",
        type=_conditions_arg,
        default=_conditions_arg(DEFAULT_CONDITIONS),
        help=(
            f"Comma-separated condition letters to run "
            f"(default: {DEFAULT_CONDITIONS}). "
            f"A=frozen baseline (no agent), C=loop autonomous, "
            f"D=loop gated (LLM-judge approval). B is deferred."
        ),
    )
    parser.add_argument(
        "--max-iterations",
        type=_positive_int,
        default=DEFAULT_MAX_ITERATIONS,
        help=(
            f"Number of agent-loop iterations per non-A condition "
            f"(default: {DEFAULT_MAX_ITERATIONS}). Condition A ignores this."
        ),
    )
    parser.add_argument(
        "--workflow-prefix",
        default=DEFAULT_WORKFLOW_PREFIX,
        help=(
            f"Prefix for per-condition workflow_ids — full id is "
            f"f'{{prefix}}-{{condition_letter.lower()}}'. "
            f"Default: {DEFAULT_WORKFLOW_PREFIX}."
        ),
    )
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=(
            f"Anthropic model id for the LLM-judge approver in condition D "
            f"(default: {DEFAULT_JUDGE_MODEL}). Forwarded to "
            f"run_improvement_loop's --judge-model."
        ),
    )
    parser.add_argument(
        "--iteration-timeout-s",
        type=_positive_float,
        default=None,
        help=(
            "Per-iteration subprocess timeout in seconds. Default: no "
            "timeout (the loop runs until it completes naturally)."
        ),
    )
    parser.add_argument(
        "--halt-on-error",
        action="store_true",
        help=(
            "Abort the whole replay on the first non-zero subprocess exit. "
            "Default: log + continue (partial DB rows survive for the merge)."
        ),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Drop iterations / proposals / approvals / eval_cases for every "
            "selected condition's workflow before running. audit_entries is "
            "WORM-protected and never cleared."
        ),
    )
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--require-lift",
        type=float,
        default=None,
        help=(
            "Exit 1 if any non-A condition's final_best_ever score is less "
            "than (condition-A baseline + N). Useful as the demo gate; "
            "the M5 spec target is +0.25."
        ),
    )
    parser.add_argument(
        "--",
        dest="separator",
        nargs="?",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "loop_args",
        nargs=argparse.REMAINDER,
        help=(
            "Extra args forwarded verbatim to each `run_improvement_loop.py` "
            "subprocess (e.g. `-- --m5-dir /data/m5 --llm-model devstral-small-2`). "
            "Anything after a leading `--` is passed through."
        ),
    )

    ns = parser.parse_args(argv)

    extra: list[str] = list(ns.loop_args)
    # argparse keeps the leading `--` on REMAINDER; drop it for hygiene.
    if extra and extra[0] == "--":
        extra = extra[1:]

    return CliArgs(
        conditions=ns.conditions,
        max_iterations=ns.max_iterations,
        workflow_prefix=ns.workflow_prefix,
        judge_model=ns.judge_model,
        iteration_timeout_s=ns.iteration_timeout_s,
        halt_on_error=ns.halt_on_error,
        reset=ns.reset,
        pretty=ns.pretty,
        require_lift=ns.require_lift,
        extra_loop_args=tuple(extra),
    )


# ---------------------------------------------------------------------------
# DB reset (mirror m5_replay_7day pattern, condition-scoped)
# ---------------------------------------------------------------------------


_RESET_QUERIES: tuple[str, ...] = (
    """
    DELETE FROM approvals WHERE proposal_id IN (
        SELECT id FROM proposals WHERE iteration_id IN (
            SELECT id FROM iterations WHERE workflow_id = $1
        )
    )
    """,
    """
    DELETE FROM proposals WHERE iteration_id IN (
        SELECT id FROM iterations WHERE workflow_id = $1
    )
    """,
    "DELETE FROM iterations WHERE workflow_id = $1",
    "DELETE FROM eval_cases WHERE workflow_id = $1",
)


async def _reset_condition_workflow(conn, workflow_id: str) -> None:
    """Drop iterations / proposals / approvals / eval_cases for one
    workflow. Skill versions are NOT dropped (they're shared across
    conditions via the seeded baseline). audit_entries is WORM-protected.
    """
    async with conn.transaction():
        for q in _RESET_QUERIES:
            await conn.execute(q, workflow_id)


# ---------------------------------------------------------------------------
# Gate check
# ---------------------------------------------------------------------------


def _check_gates(args: CliArgs, report: ThirtyDayReport) -> list[str]:
    failures: list[str] = []
    if args.require_lift is None:
        return failures

    if CONDITION_A_FROZEN not in report.conditions:
        failures.append(
            "--require-lift needs condition A in --conditions to compute the baseline"
        )
        return failures

    baseline = report.conditions[CONDITION_A_FROZEN].final_best_ever
    if baseline is None:
        failures.append(
            "--require-lift: condition A produced no baseline score "
            "(seed_m5_baseline did not run; rerun with run_improvement_loop --no-seed=False)"
        )
        return failures

    threshold = baseline + args.require_lift
    for cond, res in report.conditions.items():
        if cond == CONDITION_A_FROZEN:
            continue
        score = res.final_best_ever
        if score is None or score < threshold:
            failures.append(
                f"--require-lift: condition {cond} final_best_ever="
                f"{score} < threshold={threshold} "
                f"(baseline={baseline} + lift={args.require_lift})"
            )
    return failures


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


async def main_async(args: CliArgs) -> int:
    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(
            f"error: {ENV_DB_URL} is not set; the replay is DB-driven",
            file=sys.stderr,
        )
        return 2

    if not args.conditions:
        # _conditions_arg already rejects empty input, but defense in depth.
        print("error: no conditions selected", file=sys.stderr)
        return 4

    specs = tuple(
        ConditionSpec(
            condition=cond,
            workflow_id=workflow_id_for_condition(cond, prefix=args.workflow_prefix),
            n_iterations=args.max_iterations,
        )
        for cond in args.conditions
    )

    if args.reset:
        import asyncpg

        try:
            async with connect_workspace_conn(db_url, DEFAULT_WORKSPACE_ID) as conn:
                for spec in specs:
                    await _reset_condition_workflow(conn, spec.workflow_id)
                    print(
                        f"reset: dropped prior rows for workflow={spec.workflow_id}",
                        file=sys.stderr,
                    )
        except (WorkspaceBindError, asyncpg.PostgresError, OSError) as exc:
            print(f"error: could not connect to DB: {exc}", file=sys.stderr)
            return 3

    print(
        f"replay: conditions={','.join(s.condition for s in specs)} "
        f"max_iterations={args.max_iterations} "
        f"halt_on_error={args.halt_on_error}",
        file=sys.stderr,
    )

    _halted: str | None = None
    try:
        report = await run_all_conditions_parallel(
            specs,
            db_url=db_url,
            extra_loop_args=args.extra_loop_args,
            judge_model=args.judge_model,
            iteration_timeout_s=args.iteration_timeout_s,
            halt_on_error=args.halt_on_error,
        )
    except* RuntimeError as eg:
        # halt_on_error=True path — asyncio.TaskGroup wraps in ExceptionGroup.
        # `return` is not allowed inside an except* block (PEP 654 restriction).
        _halted = str(eg.exceptions[0])

    if _halted is not None:
        print(f"error: replay halted: {_halted}", file=sys.stderr)
        return 1

    print(json.dumps(report.to_dict(), indent=2 if args.pretty else None))

    failures = _check_gates(args, report)
    if failures:
        for line in failures:
            print(f"error: {line}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
