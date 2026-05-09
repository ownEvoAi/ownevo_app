"""Bootstrap seed for the τ³-retail workflow (P1.5 / M5).

Idempotently registers:
  * `tau3-retail-v1` workflow row
  * `tau3.retail.baseline.v1.agent` skill (HarnessAgent baseline) into
    `skills` / `skill_versions`
  * Optionally: 40 eval-case rows pointing at retail test-split tau-bench
    task IDs, seeded from the P1 baseline run's pass/fail pattern. These
    feed the gate's regression check — any future iteration that breaks
    a previously-passing task (e.g., task 0) is gate-rejected.

Companion of `seed_m5_baseline.py`. Same idempotence rules:
  * Workflow upsert: `INSERT ... ON CONFLICT (id) DO NOTHING`.
  * Skill register: parses the file, fetches the head, compares parsed
    body. Skips if unchanged so `version_seq` doesn't drift.
  * Eval cases: dedup by (workflow_id, input.task_id) — adding a tau-bench
    task that's already seeded is a no-op.

Exit codes
----------
0  seed succeeded (or was already in place)
4  could not connect to the DB at OWNEVO_DATABASE_URL
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# `apps/kernel/baselines/` lives outside `src/`. Make it importable.
_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

ENV_DB_URL = "OWNEVO_DATABASE_URL"
DEFAULT_WORKFLOW_ID = "tau3-retail-v1"
DEFAULT_WORKFLOW_DESCRIPTION = (
    "Sierra τ³-bench retail (LLM customer-service agent; 114 tasks "
    "split 74 train / 40 test). Gate runs against the test split."
)
DEFAULT_DOMAIN = "retail"

# Path to the τ³ retail baseline skill (M4). The script reads this file
# verbatim and registers it as the head of `tau3.retail.baseline.v1.agent`.
BASELINE_SKILL_PATH = _KERNEL_ROOT / "baselines" / "tau3_retail_v1" / "agent.py"

# tau-bench retail test-split task IDs (40 of them). Sourced from
# tau2_data/tau2/domains/retail/split_tasks.json — pinned here so the
# seed doesn't depend on a tau2 install on the host.
RETAIL_TEST_TASK_IDS: tuple[str, ...] = (
    "5", "9", "12", "17", "18", "26", "27", "32", "33", "36",
    "38", "39", "40", "42", "45", "49", "51", "53", "55", "56",
    "60", "61", "62", "64", "65", "68", "70", "71", "74", "77",
    "79", "86", "90", "94", "97", "100", "101", "102", "108", "111",
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliArgs:
    workflow_id: str
    description: str
    domain: str
    seed_eval_cases: bool


def parse_args(argv: list[str]) -> CliArgs:
    parser = argparse.ArgumentParser(
        prog="tau3_register",
        description="Bootstrap seed for the τ³ retail workflow + baseline skill.",
    )
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--description", default=DEFAULT_WORKFLOW_DESCRIPTION)
    parser.add_argument(
        "--domain",
        default=DEFAULT_DOMAIN,
        choices=["retail", "airline", "telecom"],
    )
    parser.add_argument(
        "--no-eval-cases",
        action="store_false",
        dest="seed_eval_cases",
        default=True,
        help="Skip seeding the 40 retail test-split eval cases.",
    )
    ns = parser.parse_args(argv)
    return CliArgs(
        workflow_id=ns.workflow_id,
        description=ns.description,
        domain=ns.domain,
        seed_eval_cases=ns.seed_eval_cases,
    )


@dataclass(frozen=True)
class SeedResult:
    workflow_id: str
    skill_registered: bool
    skill_skipped: bool
    eval_cases_added: tuple[str, ...] = field(default_factory=tuple)
    eval_cases_skipped: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Public seed function
# ---------------------------------------------------------------------------


async def seed_tau3_retail(
    conn,
    *,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    description: str = DEFAULT_WORKFLOW_DESCRIPTION,
    domain: str = DEFAULT_DOMAIN,
    seed_eval_cases: bool = True,
) -> SeedResult:
    """Upsert workflow + baseline skill + retail-test eval cases idempotently.

    Single transaction. Locks the workflow row before the skill upsert so
    concurrent re-runs don't race on `version_seq` allocation (mirrors
    `seed_m5_baseline.seed_baseline`).
    """
    from ownevo_kernel.eval_cases.registry import add_eval_case
    from ownevo_kernel.skills.format import parse_skill
    from ownevo_kernel.skills.registry import get_head, register_skill

    skill_registered = False
    skill_skipped = False
    cases_added: list[str] = []
    cases_skipped: list[str] = []

    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO workflows (id, description, spec)
            VALUES ($1, $2, '{}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            workflow_id,
            description,
        )
        await conn.execute(
            "SELECT id FROM workflows WHERE id = $1 FOR UPDATE",
            workflow_id,
        )

        # Skill: parse + compare against current head.
        content = BASELINE_SKILL_PATH.read_text()
        record = parse_skill(content)
        head = await get_head(conn, record.frontmatter.id)
        if head is not None and parse_skill(head.content).body == record.body:
            skill_skipped = True
        else:
            await register_skill(
                conn,
                content,
                created_by="bootstrap-tau3-retail",
                diff_summary="bootstrap: tau3.retail.baseline.v1.agent",
            )
            skill_registered = True

        # Eval cases: 40 retail test-split tasks. Each row points at a
        # tau-bench task ID; the runner takes task_ids=[id, id, ...] from
        # the gate's regression-suite step. Provenance = hand-authored
        # since these come from the published Sierra split, not from a
        # cluster derivation. Mark is_test_fold=true so the gate uses
        # them for val_score (not for the proposer's failure analysis).
        if seed_eval_cases and domain == "retail":
            for task_id in RETAIL_TEST_TASK_IDS:
                exists = await conn.fetchval(
                    """
                    SELECT id FROM eval_cases
                    WHERE workflow_id = $1
                      AND input @> jsonb_build_object('task_id', $2::text)
                    LIMIT 1
                    """,
                    workflow_id,
                    task_id,
                )
                if exists is not None:
                    cases_skipped.append(task_id)
                    continue
                await add_eval_case(
                    conn,
                    provenance="hand-authored",
                    workflow_id=workflow_id,
                    input={
                        "task_id": task_id,
                        "domain": domain,
                        "split": "test",
                    },
                    expected_behavior={
                        "min_reward": 1.0,
                        "source": (
                            "Sierra tau-bench retail test split; "
                            "tau2 evaluator scores 1.0 on full task completion + "
                            "DB match, 0.0 otherwise."
                        ),
                    },
                    is_test_fold=True,
                )
                cases_added.append(task_id)

    return SeedResult(
        workflow_id=workflow_id,
        skill_registered=skill_registered,
        skill_skipped=skill_skipped,
        eval_cases_added=tuple(cases_added),
        eval_cases_skipped=tuple(cases_skipped),
    )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


async def main_async(args: CliArgs) -> int:
    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(
            f"error: {ENV_DB_URL} is not set; the bootstrap seed needs a "
            "migrated Postgres to write to.",
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
        result = await seed_tau3_retail(
            conn,
            workflow_id=args.workflow_id,
            description=args.description,
            domain=args.domain,
            seed_eval_cases=args.seed_eval_cases,
        )
    finally:
        await conn.close()

    skill_state = (
        "registered" if result.skill_registered
        else ("already-current" if result.skill_skipped else "no-op")
    )
    print(
        f"seeded workflow={result.workflow_id} skill={skill_state}",
    )
    n_cases = len(result.eval_cases_added) + len(result.eval_cases_skipped)
    if n_cases:
        print(
            f"  eval cases: added {len(result.eval_cases_added)}/{n_cases}, "
            f"skipped {len(result.eval_cases_skipped)}/{n_cases} (already present)",
        )
    return 0


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
