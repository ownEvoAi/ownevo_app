"""Day-1 M5 baseline runner — `make m5-baseline`.

What this does
--------------
1. Loads the M5 catalog (path from `--m5-dir` or `OWNEVO_M5_DIR`, default
   `./data/m5`).
2. Builds the held-out fold per Phase 0 lock (28-day val + 28-day test).
3. Runs the v1 seasonal-naive baseline pipeline through
   `M5BenchmarkRunner` and prints RMSE / WRMSSE / val_score.
4. If `OWNEVO_DATABASE_URL` is set (and `--no-db` is not passed):
     * Idempotently upserts the `m5-demand-prediction` workflow row.
     * Registers each of the 6 skill source files; if a skill's head
       version content already matches what's on disk, skip — keeps
       `version_seq` from drifting on every CI re-run.
     * Inserts one `iterations` row recording the baseline `val_score`.
       This is the floor the gate compares against in W4 onward.

Why DB writes are optional
--------------------------
Local dev without compose should still produce a usable RMSE number for
the agent to look at. The DB write path activates automatically when
`OWNEVO_DATABASE_URL` points at a migrated database, and the script
exits 0 either way.

Exit codes
----------
0  baseline ran; numbers printed
2  M5 data dir missing or malformed (delegates to M5DatasetError)
3  fold construction failed (e.g., not enough day columns)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# `apps/kernel/baselines/` lives outside `src/`. Make it importable.
_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from baselines.m5_lightgbm import (  # noqa: E402
    SKILL_FILES,
    run_baseline,
    skill_files_dir,
)
from ownevo_kernel.benchmark import M5BenchmarkRunner  # noqa: E402
from ownevo_kernel.datasets import (  # noqa: E402
    M5DatasetError,
    load_m5,
    make_held_out_fold,
)

ENV_M5_DIR = "OWNEVO_M5_DIR"
ENV_DB_URL = "OWNEVO_DATABASE_URL"
DEFAULT_WORKFLOW_ID = "m5-demand-prediction"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliArgs:
    m5_dir: Path
    val_days: int
    test_days: int
    workflow_id: str
    no_db: bool


def parse_args(argv: list[str]) -> CliArgs:
    parser = argparse.ArgumentParser(
        prog="m5_baseline",
        description="Day-1 M5 seasonal-naive baseline (W2.6).",
    )
    parser.add_argument(
        "--m5-dir",
        type=Path,
        default=Path(os.environ.get(ENV_M5_DIR, "data/m5")),
        help=f"Path to the M5 CSVs (default: ${ENV_M5_DIR} or ./data/m5).",
    )
    parser.add_argument("--val-days", type=int, default=28)
    parser.add_argument("--test-days", type=int, default=28)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Skip DB writes even if OWNEVO_DATABASE_URL is set.",
    )
    ns = parser.parse_args(argv)
    return CliArgs(
        m5_dir=ns.m5_dir,
        val_days=ns.val_days,
        test_days=ns.test_days,
        workflow_id=ns.workflow_id,
        no_db=ns.no_db,
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def main_async(args: CliArgs) -> int:
    try:
        catalog = load_m5(args.m5_dir)
    except M5DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        fold = make_held_out_fold(
            catalog,
            val_days=args.val_days,
            test_days=args.test_days,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    runner = M5BenchmarkRunner(catalog=catalog, fold=fold, pipeline_fn=run_baseline)
    result = await runner.run()
    arts = runner.last_artifacts
    if arts is None:
        raise RuntimeError("M5BenchmarkRunner.run() did not populate last_artifacts")

    summary = {
        "val_score": round(result.val_score, 6),
        "rmse": round(arts.rmse, 6),
        "wrmsse": round(arts.wrmsse, 6),
        "n_series": len(arts.series_ids),
        "n_test_days": int(arts.predictions.shape[1]),
    }
    print(json.dumps(summary, indent=2))

    db_url = os.environ.get(ENV_DB_URL)
    if args.no_db or not db_url:
        if not db_url and not args.no_db:
            print(
                f"\nnote: {ENV_DB_URL} not set — skipping DB recording. "
                "Skill registry + iterations row not written.",
                file=sys.stderr,
            )
        return 0

    import asyncpg

    try:
        conn = await asyncpg.connect(db_url, timeout=10)
    except (asyncpg.PostgresConnectionFailureError, OSError) as exc:
        print(f"error: could not connect to DB: {exc}", file=sys.stderr)
        return 4
    try:
        await record_baseline(
            conn,
            workflow_id=args.workflow_id,
            val_score=result.val_score,
        )
    finally:
        await conn.close()
    print(
        f"\nrecorded baseline iteration for workflow={args.workflow_id} "
        f"val_score={result.val_score:.6f}",
    )
    return 0


# ---------------------------------------------------------------------------
# DB recording — workflow + skills + iterations row
# ---------------------------------------------------------------------------


async def record_baseline(
    conn,
    *,
    workflow_id: str,
    val_score: float,
) -> None:
    """Persist the Day-1 baseline state into the substrate tables.

    Public so an integration test can drive it against an in-flight
    connection rather than re-opening one. Run order:

      1. Upsert the workflow row (idempotent on `id`).
      2. Register each of the 6 v1 skill files. Skip the registry call
         when the head version's parsed body already matches the file —
         keeps `version_seq` from incrementing on every replay.
      3. Append one `iterations` row at `MAX(iteration_index)+1` so
         re-runs cleanly stack alongside prior replays.

    All three steps run inside a single transaction. The workflow row is
    locked (`SELECT … FOR UPDATE`) before the skill registration and
    iteration insert, serializing concurrent re-runs so they don't both
    read MAX(iteration_index)=-1 and collide on the UNIQUE constraint.
    """
    from ownevo_kernel.skills.registry import get_head, register_skill

    async with conn.transaction():
        await _ensure_workflow_row(conn, workflow_id)
        # Serialize concurrent runs: lock the workflow row before reads that
        # feed subsequent inserts (skill version_seq + iteration_index).
        await conn.execute(
            "SELECT id FROM workflows WHERE id = $1 FOR UPDATE",
            workflow_id,
        )
        skill_dir = skill_files_dir()
        for fname in SKILL_FILES:
            content = (skill_dir / fname).read_text()
            existing = await _existing_head_for_content(conn, get_head, content)
            if existing is None:
                await register_skill(
                    conn,
                    content,
                    created_by="bootstrap-m5-baseline",
                    diff_summary=f"bootstrap: {fname}",
                )
        await _insert_baseline_iteration(
            conn,
            workflow_id=workflow_id,
            val_score=val_score,
        )


async def _ensure_workflow_row(conn, workflow_id: str) -> None:
    """Idempotent insert of the demand-prediction workflow.

    `spec` is left as `{}` — the W3 NL-gen spec slots in here once it
    exists. The Day-1 row exists so the iterations FK can resolve.
    """
    await conn.execute(
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, $2, '{}'::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        workflow_id,
        "M5 demand prediction (Day-1 seasonal-naive baseline)",
    )


async def _existing_head_for_content(conn, get_head, content: str):
    """If the registered head's body already matches `content`, return it
    so we skip a no-op re-registration. Comparing parsed body, not raw
    bytes, so frontmatter whitespace doesn't trigger spurious bumps."""
    from ownevo_kernel.skills.format import parse_skill

    record = parse_skill(content)
    head = await get_head(conn, record.frontmatter.id)
    if head is None:
        return None
    head_record = parse_skill(head.content)
    if head_record.body == record.body:
        return head
    return None


async def _insert_baseline_iteration(
    conn,
    *,
    workflow_id: str,
    val_score: float,
) -> None:
    """Append a Day-1 iteration row.

    `iteration_index` is `MAX(existing) + 1` per workflow so re-running
    the script (e.g., after a code change) doesn't violate the
    UNIQUE(workflow_id, iteration_index) constraint. The first run
    starts at 0 (the literal Day-0 baseline); subsequent runs append.
    """
    next_idx = await conn.fetchval(
        "SELECT COALESCE(MAX(iteration_index), -1) + 1 "
        "FROM iterations WHERE workflow_id = $1",
        workflow_id,
    )
    await conn.execute(
        """
        INSERT INTO iterations (
            workflow_id, iteration_index, state,
            val_score, best_ever_score_after,
            ended_at
        )
        VALUES ($1, $2, 'gate-pass'::iteration_state, $3, $3, now())
        """,
        workflow_id,
        next_idx,
        val_score,
    )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
