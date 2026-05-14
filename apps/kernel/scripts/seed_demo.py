"""Demo seed — inserts sample workflows so the workspace UI has something to show.

PLAN row 8.4.2 (extended). Writes `credit-risk` and `contract-review` as
real workflow rows using the existing NL-gen fixtures (CREDIT_RISK_SPEC,
CONTRACT_REVIEW_SPEC) plus their hand-authored descriptions. Without
`--with-iterations`, no agent runs — the seed mimics a customer's first
five minutes (description + spec + eval cases, no iterations yet).

With `--with-iterations`, the script ALSO runs one iteration per seeded
workflow so the operator pages light up immediately: case-outputs
populate the TableView / AlertList / KanbanBoard primitives without a
manual "Run iteration" click. Requires `ANTHROPIC_API_KEY` — the
iteration calls the agent + proposer LLM. Skipped silently when the
key is missing (the workflow rows still seed cleanly).

Idempotent: `INSERT ... ON CONFLICT (id) DO UPDATE` so re-running this
after the fixtures change refreshes the spec without duplicating rows.
Re-running with `--with-iterations` ADDS another iteration each time;
inspect `/api/workflows/{id}/iterations` first if you only want one.

To go to a clean DB: don't run this. Nothing in runtime code depends on it.

Exit codes
----------
0  seed succeeded
4  could not connect to the DB at OWNEVO_DATABASE_URL
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

ENV_DB_URL = "OWNEVO_DATABASE_URL"
ENV_API_KEY = "ANTHROPIC_API_KEY"


@dataclass(frozen=True)
class SeededWorkflow:
    id: str
    description: str
    inserted: bool  # False if the row already existed and was just refreshed
    iteration_state: str | None = None  # set when --with-iterations runs an iteration
    iteration_val: float | None = None


async def _upsert_workflow(
    conn,
    *,
    workflow_id: str,
    description: str,
    spec_json: str,
    sim_plan_json: str,
    metric_json: str,
) -> SeededWorkflow:
    # Use xmax = 0 to distinguish INSERT (new) from UPDATE (refreshed).
    row = await conn.fetchrow(
        """
        INSERT INTO workflows (id, description, spec,
                               simulation_plan, metric_definition)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb)
        ON CONFLICT (id) DO UPDATE
          SET description = EXCLUDED.description,
              spec = EXCLUDED.spec,
              simulation_plan = EXCLUDED.simulation_plan,
              metric_definition = EXCLUDED.metric_definition
        RETURNING (xmax = 0) AS inserted
        """,
        workflow_id,
        description,
        spec_json,
        sim_plan_json,
        metric_json,
    )
    return SeededWorkflow(
        id=workflow_id,
        description=description,
        inserted=bool(row["inserted"]),
    )


async def seed_demo(
    conn,
    *,
    with_iterations: bool = False,
    _pool=None,
) -> list[SeededWorkflow]:
    """Seed the demo workflows. Returns one entry per workflow touched.

    `with_iterations=True` also runs one iteration per seeded workflow so
    the operator pages have case-output data on first load. Requires
    ANTHROPIC_API_KEY; iterations are skipped (with a printed note) when
    the key is missing.
    """
    from ownevo_kernel.nl_gen.eval_persistence import persist_eval_case_set
    from ownevo_kernel.nl_gen.fixtures import (
        CONTRACT_REVIEW_DESCRIPTION,
        CONTRACT_REVIEW_EVAL_CASE_SET,
        CONTRACT_REVIEW_METRIC,
        CONTRACT_REVIEW_SIM_PLAN,
        CONTRACT_REVIEW_SPEC,
        CREDIT_RISK_DESCRIPTION,
        CREDIT_RISK_EVAL_CASE_SET,
        CREDIT_RISK_METRIC,
        CREDIT_RISK_SIM_PLAN,
        CREDIT_RISK_SPEC,
    )

    bundles = [
        (
            "credit-risk",
            CREDIT_RISK_DESCRIPTION,
            CREDIT_RISK_SPEC,
            CREDIT_RISK_SIM_PLAN,
            CREDIT_RISK_METRIC,
            CREDIT_RISK_EVAL_CASE_SET,
        ),
        (
            "contract-review",
            CONTRACT_REVIEW_DESCRIPTION,
            CONTRACT_REVIEW_SPEC,
            CONTRACT_REVIEW_SIM_PLAN,
            CONTRACT_REVIEW_METRIC,
            CONTRACT_REVIEW_EVAL_CASE_SET,
        ),
    ]

    seeded: list[SeededWorkflow] = []
    async with conn.transaction():
        for workflow_id, description, spec, sim_plan, metric, case_set in bundles:
            result = await _upsert_workflow(
                conn,
                workflow_id=workflow_id,
                description=description,
                spec_json=spec.model_dump_json(),
                sim_plan_json=sim_plan.model_dump_json(),
                metric_json=metric.model_dump_json(),
            )
            seeded.append(result)
            # Seed eval cases only on first INSERT — re-runs of seed-demo
            # shouldn't multiply cases on every invocation. The workflow
            # row's xmax-derived `inserted` flag is the same idempotency
            # signal we use for the printed marker.
            if result.inserted:
                await persist_eval_case_set(conn, case_set, workflow_id=workflow_id)

    if not with_iterations:
        return seeded

    api_key = os.environ.get(ENV_API_KEY)
    if not api_key:
        print(
            f"note: {ENV_API_KEY} not set — skipping --with-iterations.",
            file=sys.stderr,
        )
        return seeded

    # Run one iteration per workflow so the operator pages have
    # case-outputs to render. We do this outside the seed transaction —
    # `run_one_iteration_for_workflow` opens its own transaction for the
    # persistence step.
    from anthropic import AsyncAnthropic
    from ownevo_kernel.iteration_runner import run_one_iteration_for_workflow

    client = AsyncAnthropic(api_key=api_key)
    updated: list[SeededWorkflow] = []
    for w in seeded:
        try:
            outcome = await run_one_iteration_for_workflow(
                _pool, workflow_id=w.id, client=client,
            )
            updated.append(
                SeededWorkflow(
                    id=w.id,
                    description=w.description,
                    inserted=w.inserted,
                    iteration_state=str(outcome.state),
                    iteration_val=(
                        float(outcome.val_score)
                        if outcome.val_score is not None
                        else None
                    ),
                )
            )
        except Exception as exc:
            print(
                f"note: iteration on {w.id} failed: {exc}; workflow row is seeded.",
                file=sys.stderr,
            )
            updated.append(w)
    return updated


async def main_async(args: argparse.Namespace) -> int:
    db_url = os.environ.get(ENV_DB_URL)
    if not db_url:
        print(
            f"error: {ENV_DB_URL} is not set; seed_demo needs a migrated "
            "Postgres to write to.",
            file=sys.stderr,
        )
        return 4

    import asyncpg

    # run_one_iteration_for_workflow requires a Pool (calls pool.acquire()).
    # Use a pool for with_iterations; a bare connection is fine otherwise.
    if args.with_iterations:
        try:
            pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, timeout=10)
        except (asyncpg.PostgresError, OSError) as exc:
            print(f"error: could not connect to DB: {exc}", file=sys.stderr)
            return 4

        try:
            async with pool.acquire() as conn:
                seeded = await seed_demo(conn, with_iterations=True, _pool=pool)
        finally:
            await pool.close()
    else:
        try:
            conn = await asyncpg.connect(db_url, timeout=10)
        except (asyncpg.PostgresError, OSError) as exc:
            print(f"error: could not connect to DB: {exc}", file=sys.stderr)
            return 4

        try:
            seeded = await seed_demo(conn, with_iterations=False)
        finally:
            await conn.close()

    inserted = sum(1 for s in seeded if s.inserted)
    refreshed = len(seeded) - inserted
    print(
        f"seeded demo workflows: {inserted} inserted, {refreshed} refreshed",
    )
    for s in seeded:
        marker = "+" if s.inserted else "="
        line = (
            f"  {marker} {s.id} — "
            f"{s.description[:60]}{'…' if len(s.description) > 60 else ''}"
        )
        if s.iteration_state is not None:
            val_str = (
                f"{s.iteration_val:.3f}" if s.iteration_val is not None else "—"
            )
            line += f"\n      iteration: {s.iteration_state}, val_score={val_str}"
        print(line)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed demo workflows for the local dev workspace."
    )
    parser.add_argument(
        "--with-iterations",
        action="store_true",
        help=(
            "Also run one iteration per seeded workflow so the operator "
            f"pages light up immediately. Requires {ENV_API_KEY}."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
