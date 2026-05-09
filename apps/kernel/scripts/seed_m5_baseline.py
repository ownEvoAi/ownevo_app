"""Bootstrap seed for the M5 demand-prediction workflow (BL.1).

Idempotently registers the 6 baseline skill files into `skills` /
`skill_versions` and creates the `m5-demand-prediction` workflow row.
This is the prerequisite the bootstrap improvement loop (BL.3) relies
on: by the time the agent runs, the workflow exists and the head skill
versions point at the v1 LightGBM bodies on disk.

Difference from `m5_baseline.py`
--------------------------------
`m5_baseline.py` does the same workflow + skills upsert AND records a
baseline `iterations` row at `MAX(iteration_index)+1`. That iterations
row carries `val_score` so subsequent gate runs have a `best_ever_score`
to beat.

The bootstrap loop (BL.3) deliberately wants the OPPOSITE: no prior
iterations row, so `persist_gate_run`'s DB-authoritative best-ever
lookup returns NULL and the very first gate call uses
`best_ever_score=None` (improvement check skipped — bootstrap rule).
This script writes only the workflow + skills; the loop's first
iteration is its own row.

Idempotence rules
-----------------
* Workflow upsert: `INSERT ... ON CONFLICT (id) DO NOTHING`.
* Skill register: parses the file, fetches the head, compares parsed
  bodies. If unchanged, skip — keeps `version_seq` from drifting on
  every CI re-run. Same logic as `m5_baseline.py:_existing_head_for_content`.

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
from dataclasses import dataclass
from pathlib import Path

# `apps/kernel/baselines/` lives outside `src/`. Make it importable —
# same trick `scripts/m5_baseline.py` uses.
_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from baselines.m5_lightgbm import SKILL_FILES, skill_files_dir  # noqa: E402

ENV_DB_URL = "OWNEVO_DATABASE_URL"
DEFAULT_WORKFLOW_ID = "m5-demand-prediction"
DEFAULT_WORKFLOW_DESCRIPTION = (
    "M5 demand prediction (Day-1 LightGBM baseline; bootstrap-seeded for BL.3)"
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliArgs:
    workflow_id: str
    description: str
    skill_version: str


def parse_args(argv: list[str]) -> CliArgs:
    parser = argparse.ArgumentParser(
        prog="seed_m5_baseline",
        description="Bootstrap seed for the M5 workflow + 6 baseline skills (BL.1).",
    )
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--description", default=DEFAULT_WORKFLOW_DESCRIPTION)
    parser.add_argument(
        "--skill-version",
        choices=("v1", "v2"),
        default="v1",
        help=(
            "Which baseline skill version to seed as the workflow's parent "
            "skills. v1 (default) is the deliberately-minimal Day-1 baseline. "
            "v2 is the tuned-LightGBM stronger baseline (Tweedie + ~14 features)."
        ),
    )
    ns = parser.parse_args(argv)
    return CliArgs(
        workflow_id=ns.workflow_id,
        description=ns.description,
        skill_version=ns.skill_version,
    )


# ---------------------------------------------------------------------------
# Public seed function — exported so the integration test + BL.3 can call it
# ---------------------------------------------------------------------------


async def seed_baseline(
    conn,
    *,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    description: str = DEFAULT_WORKFLOW_DESCRIPTION,
    skill_version: str = "v1",
) -> SeedResult:
    """Upsert the workflow row + register the 6 baseline skills idempotently.

    Single transaction. Locks the workflow row before the skill loop so
    concurrent re-runs don't race on `version_seq` allocation.

    ``skill_version`` selects which baseline skill bodies to seed
    (defaults to ``v1`` for backwards compat with the original
    bootstrap path). The registered skill IDs follow the version's
    own frontmatter (``m5.baseline.v1.*`` or ``m5.baseline.v2.*``).

    Returns:
        `SeedResult` with which skills were freshly registered vs.
        already-current. Useful in tests and for printing a summary.
    """
    from ownevo_kernel.skills.format import parse_skill
    from ownevo_kernel.skills.registry import get_head, register_skill

    registered: list[str] = []
    skipped: list[str] = []

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

        skill_dir = skill_files_dir(skill_version)
        for fname in SKILL_FILES:
            content = (skill_dir / fname).read_text()
            record = parse_skill(content)
            head = await get_head(conn, record.frontmatter.id)
            if head is not None and parse_skill(head.content).body == record.body:
                skipped.append(record.frontmatter.id)
                continue
            await register_skill(
                conn,
                content,
                created_by=f"bootstrap-m5-baseline-{skill_version}",
                diff_summary=f"bootstrap: {fname} ({skill_version})",
            )
            registered.append(record.frontmatter.id)

    return SeedResult(
        workflow_id=workflow_id,
        registered=tuple(registered),
        skipped=tuple(skipped),
    )


@dataclass(frozen=True)
class SeedResult:
    workflow_id: str
    registered: tuple[str, ...]
    skipped: tuple[str, ...]


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
        result = await seed_baseline(
            conn,
            workflow_id=args.workflow_id,
            description=args.description,
            skill_version=args.skill_version,
        )
    finally:
        await conn.close()

    n_total = len(result.registered) + len(result.skipped)
    print(
        f"seeded workflow={result.workflow_id} "
        f"registered={len(result.registered)}/{n_total} "
        f"skipped={len(result.skipped)}/{n_total}",
    )
    if result.registered:
        print("  registered:")
        for sid in result.registered:
            print(f"    + {sid}")
    if result.skipped:
        print("  skipped (head already current):")
        for sid in result.skipped:
            print(f"    = {sid}")
    return 0


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
