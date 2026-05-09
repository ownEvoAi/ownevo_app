"""7-day M5 replay (W5.4).

Drives `run_seven_day_replay` over a real database. The substrate
writes (workflow / skill / iterations / proposals / audit_entries /
eval_cases / approvals) are real; the score signal is synthetic so the
loop runs in seconds without sandbox / Anthropic / LightGBM.

Demo gates surfaced by the CLI:
  * Lift curve climbs over `--cycles` cycles (`--require-climbing`).
  * Audit log gained ≥ N entries this run (`--require-audit-entries`).
  * Eval set grew by ≥ N cluster-derived cases (`--require-eval-growth`).

Exit codes
----------
0  replay completed and any required gates passed
1  one or more `--require-*` gates failed
2  `OWNEVO_DATABASE_URL` not set (required — replay is DB-driven)
3  could not connect to the DB

Usage
-----
  make m5-replay-7day                                # default 7 cycles
  python scripts/m5_replay_7day.py --cycles 7 --pretty
  python scripts/m5_replay_7day.py --require-climbing
  python scripts/m5_replay_7day.py --reset           # drop prior rows
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
    ReplayConfig,
    ReplayReport,
    run_seven_day_replay,
)

ENV_DB_URL = "OWNEVO_DATABASE_URL"


@dataclass(frozen=True)
class CliArgs:
    cycles: int
    workflow_id: str
    n_initial_priors: int
    n_total_tasks: int
    lift_per_cycle: int
    cluster_cases_per_cycle: int
    reset: bool
    pretty: bool
    require_climbing: bool
    require_audit_entries: int | None
    require_eval_growth: int | None


def _positive_int(s: str) -> int:
    try:
        v = int(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected integer, got {s!r}") from exc
    if v <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {v}")
    return v


def _non_negative_int(s: str) -> int:
    try:
        v = int(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected integer, got {s!r}") from exc
    if v < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {v}")
    return v


def _parse_args(argv: list[str] | None = None) -> CliArgs:
    parser = argparse.ArgumentParser(
        description="Drive the 7-day M5 replay loop end-to-end (W5.4).",
    )
    parser.add_argument("--cycles", type=_positive_int, default=7)
    parser.add_argument("--workflow-id", default="m5-replay-7day")
    parser.add_argument("--n-initial-priors", type=_non_negative_int, default=10)
    parser.add_argument("--n-total-tasks", type=_positive_int, default=20)
    parser.add_argument("--lift-per-cycle", type=_non_negative_int, default=1)
    parser.add_argument(
        "--cluster-cases-per-cycle", type=_non_negative_int, default=1
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Drop existing iterations / proposals / eval_cases / approvals / "
            "skill_versions for the workflow + skill before running. "
            "audit_entries are append-only (WORM) and are never cleared. "
            "Useful for clean demo runs."
        ),
    )
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--require-climbing",
        action="store_true",
        help="Exit 1 if the lift curve doesn't end strictly above its start.",
    )
    parser.add_argument(
        "--require-audit-entries",
        type=_positive_int,
        default=None,
        help="Exit 1 if fewer than N audit entries were written this run.",
    )
    parser.add_argument(
        "--require-eval-growth",
        type=_positive_int,
        default=None,
        help=(
            "Exit 1 if the eval set didn't grow by at least N "
            "cluster-derived cases."
        ),
    )
    ns = parser.parse_args(argv)
    return CliArgs(
        cycles=ns.cycles,
        workflow_id=ns.workflow_id,
        n_initial_priors=ns.n_initial_priors,
        n_total_tasks=ns.n_total_tasks,
        lift_per_cycle=ns.lift_per_cycle,
        cluster_cases_per_cycle=ns.cluster_cases_per_cycle,
        reset=ns.reset,
        pretty=ns.pretty,
        require_climbing=ns.require_climbing,
        require_audit_entries=ns.require_audit_entries,
        require_eval_growth=ns.require_eval_growth,
    )


async def _reset_workflow_state(conn, workflow_id: str, skill_id: str) -> None:
    """Drop everything tied to the demo workflow + skill so a re-run starts
    clean. audit_entries is protected by the WORM trigger from 0001_substrate.sql
    (fires BEFORE DELETE, even for superusers) so we skip it — the audit count
    delta in ReplayReport is already since-this-run, so old entries don't
    poison the spec gate."""
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM approvals WHERE proposal_id IN ("
            "SELECT id FROM proposals WHERE iteration_id IN ("
            "SELECT id FROM iterations WHERE workflow_id = $1))",
            workflow_id,
        )
        await conn.execute(
            "DELETE FROM proposals WHERE iteration_id IN ("
            "SELECT id FROM iterations WHERE workflow_id = $1)",
            workflow_id,
        )
        await conn.execute(
            "DELETE FROM iterations WHERE workflow_id = $1", workflow_id
        )
        await conn.execute(
            "DELETE FROM eval_cases WHERE workflow_id = $1", workflow_id
        )
        await conn.execute(
            "UPDATE skills "
            "SET head_version_id = NULL, latest_proposed_version_id = NULL "
            "WHERE id = $1",
            skill_id,
        )
        await conn.execute(
            "DELETE FROM skill_versions WHERE skill_id = $1", skill_id
        )
        await conn.execute("DELETE FROM workflows WHERE id = $1", workflow_id)


def _check_gates(args: CliArgs, report: ReplayReport) -> list[str]:
    failures: list[str] = []
    if args.require_climbing and not report.is_climbing():
        failures.append(
            f"--require-climbing: lift curve {list(report.lift_curve)} "
            "did not end strictly above its start"
        )
    if (
        args.require_audit_entries is not None
        and report.audit_entry_count_after < args.require_audit_entries
    ):
        failures.append(
            f"--require-audit-entries: only {report.audit_entry_count_after} "
            f"entries written (need >= {args.require_audit_entries})"
        )
    growth = report.eval_set_size_final - report.eval_set_size_initial
    if (
        args.require_eval_growth is not None
        and growth < args.require_eval_growth
    ):
        failures.append(
            f"--require-eval-growth: eval set grew by only {growth} cases "
            f"(need >= {args.require_eval_growth})"
        )
    return failures


async def main_async(args: CliArgs) -> int:
    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(
            f"error: {ENV_DB_URL} is not set; the replay is DB-driven",
            file=sys.stderr,
        )
        return 2

    import asyncpg

    try:
        conn = await asyncpg.connect(db_url, timeout=10)
    except (asyncpg.ConnectionFailureError, OSError) as exc:
        print(f"error: could not connect to DB: {exc}", file=sys.stderr)
        return 3

    try:
        cfg = ReplayConfig(
            n_cycles=args.cycles,
            workflow_id=args.workflow_id,
            n_initial_priors=args.n_initial_priors,
            n_total_tasks=args.n_total_tasks,
            lift_per_cycle=args.lift_per_cycle,
            cluster_cases_per_cycle=args.cluster_cases_per_cycle,
        )
        if args.reset:
            await _reset_workflow_state(conn, cfg.workflow_id, cfg.skill_id)
        report = await run_seven_day_replay(conn, config=cfg)
    finally:
        await conn.close()

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
