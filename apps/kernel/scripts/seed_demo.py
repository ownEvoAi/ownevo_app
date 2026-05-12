"""Demo seed — inserts sample workflows so the workspace UI has something to show.

PLAN row 8.4.2. Writes `credit-risk` and `contract-review` as real workflow
rows using the existing NL-gen fixtures (CREDIT_RISK_SPEC, CONTRACT_REVIEW_SPEC)
plus their hand-authored descriptions. No skills are registered — the workflow
spec enumerates the agent's *tools*, but skill bodies are written when an
iteration actually runs (8.4.5). So a seeded workflow looks like a real
customer's first five minutes: description + spec, no iterations yet.

Idempotent: `INSERT ... ON CONFLICT (id) DO UPDATE` so re-running this after
the fixtures change refreshes the spec without duplicating rows.

To go to a clean DB: don't run this. Nothing in runtime code depends on it.

Exit codes
----------
0  seed succeeded
4  could not connect to the DB at OWNEVO_DATABASE_URL
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

ENV_DB_URL = "OWNEVO_DATABASE_URL"


@dataclass(frozen=True)
class SeededWorkflow:
    id: str
    description: str
    inserted: bool  # False if the row already existed and was just refreshed


async def _upsert_workflow(
    conn,
    *,
    workflow_id: str,
    description: str,
    spec_json: str,
) -> SeededWorkflow:
    # Use xmax = 0 to distinguish INSERT (new) from UPDATE (refreshed).
    row = await conn.fetchrow(
        """
        INSERT INTO workflows (id, description, spec)
        VALUES ($1, $2, $3::jsonb)
        ON CONFLICT (id) DO UPDATE
          SET description = EXCLUDED.description,
              spec = EXCLUDED.spec
        RETURNING (xmax = 0) AS inserted
        """,
        workflow_id,
        description,
        spec_json,
    )
    return SeededWorkflow(
        id=workflow_id,
        description=description,
        inserted=bool(row["inserted"]),
    )


async def seed_demo(conn) -> list[SeededWorkflow]:
    """Seed the demo workflows. Returns one entry per workflow touched."""
    from ownevo_kernel.nl_gen.fixtures import (
        CONTRACT_REVIEW_DESCRIPTION,
        CONTRACT_REVIEW_SPEC,
        CREDIT_RISK_DESCRIPTION,
        CREDIT_RISK_SPEC,
    )

    pairs = [
        ("credit-risk", CREDIT_RISK_DESCRIPTION, CREDIT_RISK_SPEC),
        ("contract-review", CONTRACT_REVIEW_DESCRIPTION, CONTRACT_REVIEW_SPEC),
    ]

    seeded: list[SeededWorkflow] = []
    async with conn.transaction():
        for workflow_id, description, spec in pairs:
            spec_json = spec.model_dump_json()
            seeded.append(
                await _upsert_workflow(
                    conn,
                    workflow_id=workflow_id,
                    description=description,
                    spec_json=spec_json,
                )
            )
    return seeded


async def main_async() -> int:
    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(
            f"error: {ENV_DB_URL} is not set; seed_demo needs a migrated "
            "Postgres to write to.",
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
        seeded = await seed_demo(conn)
    finally:
        await conn.close()

    inserted = sum(1 for s in seeded if s.inserted)
    refreshed = len(seeded) - inserted
    print(
        f"seeded demo workflows: {inserted} inserted, {refreshed} refreshed",
    )
    for s in seeded:
        marker = "+" if s.inserted else "="
        print(f"  {marker} {s.id} — {s.description[:60]}{'…' if len(s.description) > 60 else ''}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
