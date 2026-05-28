"""Re-analyze τ³ per-task traces from the DB.

Use this when val_score moved and you want to know *which* task changed
and *why*. The gate stores one ``traces`` row per simulation per
iteration (see ``persist_gate_run`` § 7b), so we can compare the same
task across skill versions without re-running anything.

Usage::

    # List all per-task results across iterations for a workflow
    python scripts/tau3_inspect_task.py --workflow-id tau3-retail-v1

    # Show full message history for one task at one iteration
    python scripts/tau3_inspect_task.py \\
        --workflow-id tau3-retail-v1 --task-id 49 --iteration 11

    # Diff the same task across two iterations (where did it regress?)
    python scripts/tau3_inspect_task.py \\
        --workflow-id tau3-retail-v1 --task-id 33 --compare 5,11
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ENV_DB_URL = "OWNEVO_DATABASE_URL"
_DOTENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _load_dotenv_into_environ() -> None:
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
        val = m.group("value")
        if val and val[0] in {'"', "'"} and val[-1] == val[0]:
            val = val[1:-1]
        os.environ[key] = val


async def _list_tasks(conn, workflow_id: str, task_id: str | None) -> None:
    rows = await conn.fetch(
        """
        SELECT i.iteration_index,
               t.events ->> 'task_id'                  AS task_id,
               (t.metric_outputs ->> 'reward')::float  AS reward,
               t.events ->> 'termination_reason'       AS termination_reason,
               jsonb_array_length(COALESCE(t.events -> 'messages', '[]'::jsonb)) AS n_messages,
               i.val_score, i.state
        FROM traces t
        JOIN iterations i ON i.id = t.iteration_id
        WHERE i.workflow_id = $1
          AND ($2::text IS NULL OR (t.events ->> 'task_id') = $2)
        ORDER BY i.iteration_index, (t.events ->> 'task_id')
        """,
        workflow_id, task_id,
    )
    if not rows:
        print(f"No traces found for workflow={workflow_id!r}"
              + (f" task={task_id!r}" if task_id else ""))
        print("(Iterations run before the trace-persistence fix have no per-task traces.)")
        return
    print(f"{'iter':<5} {'task':<6} {'reward':<7} {'msgs':<5} {'term':<28} {'val_score':<10} state")
    print("-" * 100)
    for r in rows:
        reward = f"{r['reward']:.2f}" if r["reward"] is not None else "—"
        val = f"{r['val_score']:.4f}" if r["val_score"] is not None else "—"
        term = (r["termination_reason"] or "—")[:28]
        print(f"{r['iteration_index']:<5} {r['task_id']:<6} {reward:<7} "
              f"{r['n_messages']:<5} {term:<28} {val:<10} {r['state']}")


async def _show_task(conn, workflow_id: str, task_id: str, iteration_index: int) -> None:
    row = await conn.fetchrow(
        """
        SELECT t.events, t.metric_outputs
        FROM traces t
        JOIN iterations i ON i.id = t.iteration_id
        WHERE i.workflow_id = $1
          AND i.iteration_index = $2
          AND (t.events ->> 'task_id') = $3
        """,
        workflow_id, iteration_index, task_id,
    )
    if row is None:
        print(f"No trace for workflow={workflow_id!r} iter={iteration_index} task={task_id!r}")
        return
    events = row["events"]
    if isinstance(events, str):
        events = json.loads(events)
    metrics = row["metric_outputs"]
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    print(f"task {task_id} @ iter {iteration_index}")
    print(f"  reward: {metrics.get('reward')}")
    print(f"  termination: {events.get('termination_reason')}")
    print(f"  duration: {metrics.get('duration_seconds')}")
    rinfo = metrics.get("reward_info") or {}
    if isinstance(rinfo, dict):
        for k, v in rinfo.items():
            if k == "reward":
                continue
            preview = str(v)[:200]
            print(f"  {k}: {preview}")
    print(f"  messages ({len(events.get('messages') or [])}):")
    for i, m in enumerate(events.get("messages") or []):
        if not isinstance(m, dict):
            print(f"    [{i}] {m}")
            continue
        role = m.get("role", "?")
        content = (m.get("content") or "")[:300]
        if isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
            tc = m["tool_calls"][0]
            name = tc.get("name") or (tc.get("function") or {}).get("name", "?")
            args = (tc.get("arguments") or (tc.get("function") or {}).get("arguments") or "")
            print(f"    [{i}] {role}: tool_call={name}({str(args)[:200]})")
        else:
            print(f"    [{i}] {role}: {content}")


async def _compare(conn, workflow_id: str, task_id: str, iters: list[int]) -> None:
    rows = await conn.fetch(
        """
        SELECT i.iteration_index,
               (t.metric_outputs ->> 'reward')::float AS reward,
               t.events ->> 'termination_reason'      AS termination_reason,
               jsonb_array_length(COALESCE(t.events -> 'messages', '[]'::jsonb)) AS n_messages,
               t.events, t.metric_outputs
        FROM traces t
        JOIN iterations i ON i.id = t.iteration_id
        WHERE i.workflow_id = $1
          AND (t.events ->> 'task_id') = $2
          AND i.iteration_index = ANY($3::int[])
        ORDER BY i.iteration_index
        """,
        workflow_id, task_id, iters,
    )
    if not rows:
        print(f"No traces for task {task_id} at iterations {iters}")
        return
    print(f"Compare task {task_id} across iterations {iters}\n")
    for r in rows:
        reward = f"{r['reward']:.2f}" if r["reward"] is not None else "—"
        print(f"  iter {r['iteration_index']}: reward={reward} "
              f"msgs={r['n_messages']} term={r['termination_reason']}")
    if len(rows) >= 2:
        print("\n--- last assistant message diff ---")
        for r in rows:
            ev = r["events"]
            if isinstance(ev, str):
                ev = json.loads(ev)
            msgs = [m for m in (ev.get("messages") or []) if isinstance(m, dict)]
            asst = [m for m in msgs if m.get("role") == "assistant"]
            last = asst[-1] if asst else None
            content = ((last or {}).get("content") or "")[:500]
            print(f"\niter {r['iteration_index']} last assistant:\n  {content}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="tau3_inspect_task")
    p.add_argument("--workflow-id", required=True)
    p.add_argument("--task-id", default=None,
                   help="Filter to one task (for --iteration / --compare).")
    p.add_argument("--iteration", type=int, default=None,
                   help="Show full trace for --task-id at this iteration.")
    p.add_argument("--compare", default=None,
                   help="Comma-separated iteration indices to compare for --task-id.")
    return p.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> int:
    _load_dotenv_into_environ()
    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(f"error: {ENV_DB_URL} is not set", file=sys.stderr)
        return 4
    # Bind the workspace before any read: under enforced RLS an unbound
    # connection sees zero rows in `traces` / `iterations`, so the inspect
    # commands would silently return "no results".
    import asyncpg  # noqa: PLC0415
    from ownevo_kernel.tenant_session import (  # noqa: PLC0415
        DEFAULT_WORKSPACE_ID,
        WorkspaceBindError,
        connect_workspace_conn,
    )
    try:
        async with connect_workspace_conn(db_url, DEFAULT_WORKSPACE_ID) as conn:
            if args.iteration is not None and args.task_id:
                await _show_task(conn, args.workflow_id, args.task_id, args.iteration)
            elif args.compare and args.task_id:
                iters = [int(x) for x in args.compare.split(",")]
                await _compare(conn, args.workflow_id, args.task_id, iters)
            else:
                await _list_tasks(conn, args.workflow_id, args.task_id)
    except (WorkspaceBindError, asyncpg.PostgresError, OSError) as exc:
        print(f"error: could not connect to DB: {exc}", file=sys.stderr)
        return 4
    return 0


def main() -> int:
    return asyncio.run(_main_async(_parse_args(sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
