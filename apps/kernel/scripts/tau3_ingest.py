"""τ³-bench trace-dir ingest (P1.5 / M8).

Backfill helper: read one or many tau2 ``results.json`` files and insert
one ``iterations`` row per file (val_score = mean reward, state =
gate-pass when val_score > best-ever, else gate-blocked-no-improvement).

Use cases:
  * **P1 baseline backfill.** The auto-harness P1 run on 2026-05-08
    produced a results.json at val_score=0.8000 — ingest brings that
    history into ownEvo's iterations table without re-running tau2.
  * **Multi-run history.** All seven prior τ³ trace dirs (Sonnet,
    qwen3-coder, gemma4 variants) can be ingested as historical
    iteration rows so the workspace nav surfaces the full attempt
    history once the M10 web-UI surface is wired.

Optional side-effects:
  * ``--dump-failures`` writes one JSON per failure into the workspace
    dir (for downstream clustering by `make m5-cluster-failures` style
    pipelines, once a τ³-flavored clusterer exists). Pure data dump,
    no DB writes for failures yet — the clusterer + failure_clusters
    insert path is still M7-adjacent work that hasn't shipped.

CLI:
  ``--results <path> [<path>...]`` — one or more results.json files.
  ``--workflow-id <id>`` — defaults to tau3-retail-v1.
  ``--no-db`` — skip insert, just print summary.
  ``--dump-failures <dir>`` — write failure-snapshot JSONs.

Exit codes: 0 = success, 1 = parse/validation error, 4 = DB env or auth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from ownevo_kernel.benchmark.tau3 import (
    Tau3FailureAnalyzerError,
    analyze_tau3_failures,
)

ENV_DB_URL = "OWNEVO_DATABASE_URL"
DEFAULT_WORKFLOW_ID = "tau3-retail-v1"


@dataclass(frozen=True)
class CliArgs:
    results_paths: tuple[Path, ...]
    workflow_id: str
    domain: str
    split: str
    no_db: bool
    dump_failures: Path | None


def parse_args(argv: list[str]) -> CliArgs:
    parser = argparse.ArgumentParser(
        prog="tau3_ingest",
        description="Ingest tau2 results.json files into ownEvo's iterations table.",
    )
    parser.add_argument("--results", nargs="+", required=True, type=Path,
                        help="One or more tau2 results.json file paths.")
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--domain", default="retail",
                        help="Used as a hint when results.json doesn't carry "
                             "the domain in its info block. Default 'retail' "
                             "matches the baseline workflow.")
    parser.add_argument("--split", default="test",
                        help="Same shape as --domain.")
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--dump-failures", type=Path, default=None,
                        help="Directory where to write per-failure JSONs.")
    ns = parser.parse_args(argv)
    return CliArgs(
        results_paths=tuple(ns.results),
        workflow_id=ns.workflow_id,
        domain=ns.domain,
        split=ns.split,
        no_db=ns.no_db,
        dump_failures=ns.dump_failures,
    )


def _summarize(results_path: Path) -> dict:
    """Read results.json and compute val_score + failure breakdown.

    Tolerant on tau2 schema additions; strict on the keys we read."""
    data = json.loads(results_path.read_text())
    sims = data.get("simulations") or []
    n_total = len(sims)
    rewards: list[float | None] = []
    for sim in sims:
        ri = sim.get("reward_info")
        if ri is None:
            rewards.append(None)
        else:
            try:
                rewards.append(float(ri.get("reward", 0.0)))
            except (TypeError, ValueError):
                rewards.append(None)
    n_no_result = sum(1 for r in rewards if r is None)
    n_passed = sum(1 for r in rewards if r is not None and r >= 0.5)
    val_score = (
        sum((0.0 if r is None else r) for r in rewards) / n_total
        if n_total else 0.0
    )
    info = data.get("info") or {}
    agent_info = info.get("agent_info") or {}
    env_info = info.get("environment_info") or {}
    return {
        "val_score": val_score,
        "n_total": n_total,
        "n_passed": n_passed,
        "n_no_result": n_no_result,
        "agent_model": agent_info.get("llm"),
        "user_model": (info.get("user_info") or {}).get("llm"),
        "domain": env_info.get("domain_name"),
        "timestamp": data.get("timestamp"),
    }


async def _insert_iteration(
    conn, *, workflow_id: str, val_score: float,
    summary: dict, results_path: Path,
) -> int:
    """Insert one iterations row at MAX(iteration_index)+1.

    State: gate-pass when val_score >= existing best_ever, else
    gate-blocked-no-improvement. Mirrors how M5's m5_baseline records a
    bootstrap iteration but doesn't touch best_ever_score_after on
    non-improvement (matches gate.run_gate semantics).
    """
    next_idx = await conn.fetchval(
        "SELECT COALESCE(MAX(iteration_index), -1) + 1 "
        "FROM iterations WHERE workflow_id = $1",
        workflow_id,
    )
    best = await conn.fetchval(
        "SELECT MAX(best_ever_score_after) FROM iterations "
        "WHERE workflow_id = $1",
        workflow_id,
    )
    is_first_or_better = best is None or val_score > float(best)
    state = "gate-pass" if is_first_or_better else "gate-blocked-no-improvement"
    new_best = val_score if is_first_or_better else float(best)

    await conn.execute(
        """
        INSERT INTO iterations (
            workflow_id, iteration_index, state,
            val_score, best_ever_score_after,
            ended_at
        )
        VALUES ($1, $2, $3::iteration_state, $4, $5, now())
        """,
        workflow_id,
        next_idx,
        state,
        val_score,
        new_best,
    )
    return next_idx


def _dump_failures(dump_dir: Path, results_path: Path,
                   args: CliArgs) -> int:
    """Write one JSON per failure under dump_dir/<run_id>/<task>.json.

    No clustering yet — that's a follow-up that the
    `clustering/` pipeline can consume."""
    try:
        failures = analyze_tau3_failures(
            results_path,
            domain_hint=args.domain,
            split_hint=args.split,
        )
    except Tau3FailureAnalyzerError as exc:
        print(f"warning: could not analyze {results_path}: {exc}",
              file=sys.stderr)
        return 0
    run_id = results_path.parent.name
    out_dir = dump_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in failures:
        (out_dir / f"task_{f.task_id}.json").write_text(
            json.dumps(asdict(f), indent=2),
        )
    return len(failures)


async def main_async(args: CliArgs) -> int:
    if any(not p.is_file() for p in args.results_paths):
        for p in args.results_paths:
            if not p.is_file():
                print(f"error: not a file: {p}", file=sys.stderr)
        return 1

    summaries: list[tuple[Path, dict]] = []
    for p in args.results_paths:
        try:
            summaries.append((p, _summarize(p)))
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            print(f"error: could not summarize {p}: {exc}", file=sys.stderr)
            return 1

    print(json.dumps([
        {"path": str(p), **s} for p, s in summaries
    ], indent=2, default=str))

    if args.dump_failures is not None:
        total = 0
        for p, _ in summaries:
            total += _dump_failures(args.dump_failures, p, args)
        print(f"\ndumped {total} failure snapshots to {args.dump_failures}")

    if args.no_db:
        return 0

    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(f"error: {ENV_DB_URL} is not set; pass --no-db for ad-hoc.",
              file=sys.stderr)
        return 4
    import asyncpg  # noqa: PLC0415
    try:
        conn = await asyncpg.connect(db_url, timeout=10)
    except (asyncpg.PostgresError, OSError) as exc:
        print(f"error: could not connect to DB: {exc}", file=sys.stderr)
        return 4

    try:
        # Ensure workflow + baseline skill exist before inserting iterations.
        from scripts.tau3_register import seed_tau3_retail  # noqa: PLC0415
        async with conn.transaction():
            await seed_tau3_retail(
                conn, workflow_id=args.workflow_id,
                domain=args.domain, seed_eval_cases=False,
            )
            for p, s in summaries:
                idx = await _insert_iteration(
                    conn, workflow_id=args.workflow_id,
                    val_score=s["val_score"],
                    summary=s, results_path=p,
                )
                print(
                    f"  → ingested {p.name}: "
                    f"workflow={args.workflow_id} "
                    f"iteration_index={idx} val_score={s['val_score']:.4f}",
                )
    finally:
        await conn.close()
    return 0


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
