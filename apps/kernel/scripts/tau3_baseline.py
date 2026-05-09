"""τ³-retail Day-1 baseline runner (P1.5 / M6).

Runs the SandboxedTauBenchRunner against the retail test split with the
baked-in `tau3.retail.baseline.v1.agent` skill and (by default) writes
one `iterations` row at gate-pass with the resulting `val_score`. This
is the kernel-native equivalent of running condition A through the
auto-harness scaffolding and matches the P1 (auto-harness) baseline
within ±5pp — that match is the M6 validation gate per
`docs/TAU3_LOCAL_TESTPLAN.md`.

Mirrors `scripts/m5_baseline.py` shape:
  * Run benchmark in process (here: in the τ³ Docker sandbox).
  * Print val_score + summary stats to stdout.
  * Optionally upsert workflow + skill + iterations row when
    `OWNEVO_DATABASE_URL` is set (skip DB write with `--no-db`).

Defaults: Sonnet 4.6 task agent + Haiku 4.5 user simulator, retail test
split (40 tasks), concurrency 3. Override with CLI flags. The script
reads ANTHROPIC_API_KEY from the env or, as a fallback, parses
``ownevo_app/.env`` so the auto-harness env-passing pattern keeps
working.

Exit codes
----------
0  baseline ran successfully
1  benchmark error (sandbox crash, parse failure, etc.)
4  --record requested but DB env or auth failed
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# baselines/ lives outside src/ — same trick m5_baseline.py uses.
_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

ENV_DB_URL = "OWNEVO_DATABASE_URL"
ENV_ANTHROPIC = "ANTHROPIC_API_KEY"
ENV_OPENAI = "OPENAI_API_KEY"
ENV_OLLAMA = "OLLAMA_API_BASE"

DEFAULT_WORKFLOW_ID = "tau3-retail-v1"
DEFAULT_IMAGE = "ownevo-sandbox-tau3:0.1.0"
DEFAULT_AGENT_MODEL = "anthropic/claude-sonnet-4-6"
DEFAULT_USER_MODEL = "anthropic/claude-haiku-4-5-20251001"
DEFAULT_DOMAIN = "retail"
DEFAULT_SPLIT = "test"
DEFAULT_CONCURRENCY = 3
# 30 min for full retail test (~16 min observed via auto-harness P1).
DEFAULT_TIMEOUT_S = 1800.0
DEFAULT_MEMORY_MB = 1024


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliArgs:
    workflow_id: str
    image: str
    domain: str
    split: str
    agent_model: str
    user_model: str
    task_ids: tuple[str, ...] | None
    concurrency: int
    timeout_seconds: float
    memory_mb: int
    skill_override_dir: Path | None
    no_db: bool


def parse_args(argv: list[str]) -> CliArgs:
    parser = argparse.ArgumentParser(
        prog="tau3_baseline",
        description="Run the Day-1 τ³ baseline (sandboxed) and optionally record it.",
    )
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--domain", default=DEFAULT_DOMAIN,
                        choices=["retail", "airline", "telecom",
                                 "telecom_full", "telecom_small"])
    parser.add_argument("--split", default=DEFAULT_SPLIT,
                        choices=["train", "test"])
    parser.add_argument("--agent-model", default=DEFAULT_AGENT_MODEL,
                        help="LiteLLM-style model id for the task agent.")
    parser.add_argument("--user-model", default=DEFAULT_USER_MODEL,
                        help="LiteLLM-style model id for the user simulator. "
                             "Defaults to a separate cheaper model per "
                             "auto-harness convention.")
    parser.add_argument("--task-ids", nargs="+", default=None,
                        help="Subset of task IDs (default: full split).")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--timeout-seconds", type=float,
                        default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--memory-mb", type=int, default=DEFAULT_MEMORY_MB)
    parser.add_argument("--skill-override-dir", type=Path, default=None,
                        help="Bind-mount this dir at /skill_override. Must "
                             "contain agent.py with HarnessAgent class.")
    parser.add_argument("--no-db", action="store_true",
                        help="Skip writing the workflow + skill + iterations "
                             "row. Use for ad-hoc baseline measurements.")

    ns = parser.parse_args(argv)
    return CliArgs(
        workflow_id=ns.workflow_id,
        image=ns.image,
        domain=ns.domain,
        split=ns.split,
        agent_model=ns.agent_model,
        user_model=ns.user_model,
        task_ids=tuple(ns.task_ids) if ns.task_ids else None,
        concurrency=ns.concurrency,
        timeout_seconds=ns.timeout_seconds,
        memory_mb=ns.memory_mb,
        skill_override_dir=ns.skill_override_dir,
        no_db=ns.no_db,
    )


# ---------------------------------------------------------------------------
# Env loading — lift ANTHROPIC_API_KEY etc from .env when not exported
# ---------------------------------------------------------------------------


_DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"  # ownevo_app/.env


def _load_dotenv_into_environ() -> None:
    """Light .env loader for keys we care about. Doesn't replace existing
    values — env wins over file. No external dep (asyncpg-style: keep
    the kernel's runtime install minimal).
    """
    if not _DOTENV_PATH.is_file():
        return
    pattern = re.compile(r"^(?P<key>[A-Z_][A-Z0-9_]*)\s*=\s*(?P<value>.*)$")
    for line in _DOTENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if not m:
            continue
        key = m.group("key")
        if key in os.environ:
            continue
        # Strip surrounding quotes if any.
        val = m.group("value")
        if val and val[0] in {'"', "'"} and val[-1] == val[0]:
            val = val[1:-1]
        os.environ[key] = val


# ---------------------------------------------------------------------------
# Run + record
# ---------------------------------------------------------------------------


async def _run_baseline(args: CliArgs):
    from ownevo_kernel.benchmark.tau3 import SandboxedTauBenchRunner
    from ownevo_kernel.sandbox.local_docker import LocalDockerSandbox

    sandbox = LocalDockerSandbox(
        image=args.image,
        network="bridge",
        cpus=2.0,
        pids_limit=512,
        tmpfs_size_mb=128,
    )
    runner = SandboxedTauBenchRunner(
        domain=args.domain,
        split=args.split,
        agent_model=args.agent_model,
        user_model=args.user_model,
        sandbox=sandbox,
        max_concurrency=args.concurrency,
        timeout_seconds=args.timeout_seconds,
        memory_mb=args.memory_mb,
        skill_override_dir=args.skill_override_dir,
        anthropic_api_key=os.environ.get(ENV_ANTHROPIC),
        openai_api_key=os.environ.get(ENV_OPENAI),
        ollama_api_base=os.environ.get(ENV_OLLAMA),
    )
    result = await runner.run(
        task_ids=list(args.task_ids) if args.task_ids else None,
    )
    return result, runner.last_summary, runner.last_raw_run_dir


async def _record_baseline(args: CliArgs, val_score: float) -> int:
    """Upsert workflow + skill + append a baseline iterations row.

    Mirrors `scripts/m5_baseline.record_baseline` in shape: idempotent
    workflow upsert, idempotent skill registration (no-op if head body
    matches), iteration_index = MAX+1 so re-runs stack cleanly.
    """
    from scripts.tau3_register import seed_tau3_retail

    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(
            f"error: {ENV_DB_URL} is not set; pass --no-db for ad-hoc runs.",
            file=sys.stderr,
        )
        return 4

    import asyncpg

    try:
        conn = await asyncpg.connect(db_url, timeout=10)
    except (asyncpg.PostgresError, OSError) as exc:
        print(f"error: could not connect to DB: {exc}", file=sys.stderr)
        return 4

    try:
        async with conn.transaction():
            # Reuse the M5 register-style seed (workflow + skill +
            # eval cases). Idempotent.
            await seed_tau3_retail(
                conn, workflow_id=args.workflow_id,
                domain=args.domain,
                seed_eval_cases=(args.domain == "retail"),
            )
            # Lock workflow row before iteration insert (matches m5_baseline).
            await conn.execute(
                "SELECT id FROM workflows WHERE id = $1 FOR UPDATE",
                args.workflow_id,
            )
            next_idx = await conn.fetchval(
                "SELECT COALESCE(MAX(iteration_index), -1) + 1 "
                "FROM iterations WHERE workflow_id = $1",
                args.workflow_id,
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
                args.workflow_id,
                next_idx,
                val_score,
            )
    finally:
        await conn.close()

    print(
        f"\nrecorded baseline iteration for workflow={args.workflow_id} "
        f"val_score={val_score:.6f} "
        f"(iteration_index={next_idx}).",
    )
    return 0


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


async def main_async(args: CliArgs) -> int:
    _load_dotenv_into_environ()

    print(f"τ³ baseline — image={args.image}", file=sys.stderr)
    print(
        f"  workflow={args.workflow_id} domain={args.domain} split={args.split}",
        file=sys.stderr,
    )
    print(
        f"  agent_model={args.agent_model} user_model={args.user_model}",
        file=sys.stderr,
    )
    print(
        f"  concurrency={args.concurrency} timeout={args.timeout_seconds}s",
        file=sys.stderr,
    )

    try:
        result, summary, raw_run_dir = await _run_baseline(args)
    except Exception as exc:  # noqa: BLE001
        print(f"error: τ³ baseline failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    print(json.dumps({
        "val_score": round(result.val_score, 6),
        "n_tasks": result.n_tasks,
        "n_passed": result.n_passed,
        "n_no_result": result.n_no_result,
        "summary": summary,
        "raw_run_dir": raw_run_dir,
    }, indent=2))

    if args.no_db:
        return 0

    return await _record_baseline(args, val_score=result.val_score)


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
