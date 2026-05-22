"""Demo seed — inserts sample workflows so the workspace UI has something to show.

Writes `credit-risk`, `contract-review`, and `demand-prediction` as
real workflow rows using the existing NL-gen fixtures (CREDIT_RISK_SPEC,
CONTRACT_REVIEW_SPEC, DEMAND_PREDICTION_SPEC) plus their hand-authored
descriptions. Without
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
    demo_fixtures_added: bool = False  # True when demo clusters/proposal fixtures inserted


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


# Demo fixture content — hand-crafted clusters + skill diff + proposal for
# demand-prediction so the workspace UI shows the full loop on the
# Failures + Proposals tabs without waiting for a harder eval set.
#
# Why hand-craft instead of iterating until real clusters form: the
# demand-prediction eval cases are easy enough that one cycle produces
# ~2 failures, below the clustering pipeline's `min_inputs=5` floor.
# Running more iterations doesn't help — clustering is per-iteration.
# These fixtures stand in for what a harder eval set would produce
# organically; labels are concrete (named seasonal patterns) and the
# proposal is the kind of plain-English edit the proposer-LLM would
# write on a real failure cluster.
#
# Only inserted when demand-prediction has 0 clusters — re-running the
# seed is a no-op once the fixtures exist.
_DP_CLUSTER_LABELS: tuple[tuple[str, str, int], ...] = (
    (
        "Holiday markdown false-negatives, weeks 47-51",
        "high",
        5,
    ),
    (
        "Off-shelf bias on slow-mover SKUs",
        "medium",
        3,
    ),
)

_DP_SKILL_ID = "demand-prediction.instruction"
_DP_SKILL_V1_CONTENT = (
    "Predict whether each SKU will exceed its demand threshold in the "
    "upcoming planning week. Use the prior 8 weeks of velocity and the "
    "store's seasonal index. Lean conservative on uncertainty — under-"
    "forecasting beats over-stocking on perishables."
)
_DP_SKILL_V2_CONTENT = (
    _DP_SKILL_V1_CONTENT
    + "\n\n"
    + "When the planning window falls in weeks 47-51 and any holiday-"
    "markdown signal is present (seasonal-promo cue, dip-tail pattern, "
    "end-of-year clearance flag), lean toward predicting True even under "
    "uncertainty. Recall is the gating metric and false-negatives in "
    "this window are the dominant failure mode."
)
_DP_PROPOSAL_SUMMARY = (
    "Add a holiday-markdown override for weeks 47-51: when the trajectory "
    "shows any seasonal-promo signal in those weeks, lean True. Addresses "
    "the dominant false-negative cluster the loop surfaced this cycle."
)


async def _seed_demand_prediction_demo_fixtures(conn) -> bool:
    """Insert hand-crafted clusters + a gate-passed proposal on demand-prediction.

    Skipped if demand-prediction already has clusters (so re-runs are a no-op).
    Returns True when the fixture rows were inserted, False when skipped.

    These rows are demo-only — they let the workspace UI render the full
    loop on demand-prediction (Failures + Proposals tabs) without waiting
    for a harder eval set. They do NOT come from a real iteration's
    failure cases; the labels + skill diff are hand-authored.
    """
    from ownevo_kernel.audit import append_audit_entry
    from ownevo_kernel.types import AuditKind

    workflow_id = "demand-prediction"

    existing = await conn.fetchval(
        "SELECT COUNT(*)::int FROM failure_clusters WHERE workflow_id = $1",
        workflow_id,
    )
    if existing and existing > 0:
        return False

    # Need a recent iteration to attach the proposal to.
    iter_id = await conn.fetchval(
        "SELECT id FROM iterations WHERE workflow_id = $1 "
        "ORDER BY iteration_index DESC LIMIT 1",
        workflow_id,
    )
    if iter_id is None:
        # No iteration to anchor the proposal — bail rather than orphan rows.
        return False

    async with conn.transaction():
        # 1. Failure clusters.
        for label, severity, cluster_size in _DP_CLUSTER_LABELS:
            cluster_id = await conn.fetchval(
                """
                INSERT INTO failure_clusters
                    (workflow_id, label, severity, cluster_size)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                workflow_id,
                label,
                severity,
                cluster_size,
            )
            await append_audit_entry(
                conn,
                kind=AuditKind.CLUSTER_CREATED,
                payload={
                    "workflow_id": workflow_id,
                    "cluster_id": str(cluster_id),
                    "label": label,
                    "severity": severity,
                    "source": "demo-fixture",
                },
                actor="seed_demo:demo-fixtures",
                related_id=cluster_id,
            )

        # 2. Skill + two versions (v1 baseline, v2 the "proposed" edit).
        await conn.execute(
            """
            INSERT INTO skills (id, kind, workflow_id)
            VALUES ($1, 'instruction'::skill_kind, $2)
            ON CONFLICT (id) DO NOTHING
            """,
            _DP_SKILL_ID,
            workflow_id,
        )
        v1_id = await conn.fetchval(
            """
            INSERT INTO skill_versions
                (skill_id, version_seq, content, created_by)
            VALUES ($1, 1, $2, 'seed_demo:demo-fixtures')
            RETURNING id
            """,
            _DP_SKILL_ID,
            _DP_SKILL_V1_CONTENT,
        )
        v2_id = await conn.fetchval(
            """
            INSERT INTO skill_versions
                (skill_id, version_seq, content, parent_version_id, created_by)
            VALUES ($1, 2, $2, $3, 'seed_demo:demo-fixtures')
            RETURNING id
            """,
            _DP_SKILL_ID,
            _DP_SKILL_V2_CONTENT,
            v1_id,
        )
        await conn.execute(
            "UPDATE skills SET head_version_id = $1 WHERE id = $2",
            v1_id,
            _DP_SKILL_ID,
        )

        # 3. Gate-passed proposal pointing at the v2 skill version.
        proposal_id = await conn.fetchval(
            """
            INSERT INTO proposals
                (iteration_id, skill_id, parent_version_id,
                 proposed_content, plain_language_summary, state)
            VALUES ($1, $2, $3, $4, $5, 'gate-passed'::proposal_state)
            RETURNING id
            """,
            iter_id,
            _DP_SKILL_ID,
            v1_id,
            _DP_SKILL_V2_CONTENT,
            _DP_PROPOSAL_SUMMARY,
        )
        await append_audit_entry(
            conn,
            kind=AuditKind.PROPOSAL_CREATED,
            payload={
                "workflow_id": workflow_id,
                "proposal_id": str(proposal_id),
                "skill_id": _DP_SKILL_ID,
                "state": "gate-passed",
                "source": "demo-fixture",
            },
            actor="seed_demo:demo-fixtures",
            related_id=proposal_id,
        )

    return True


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
        DEMAND_PREDICTION_DESCRIPTION,
        DEMAND_PREDICTION_EVAL_CASE_SET,
        DEMAND_PREDICTION_METRIC,
        DEMAND_PREDICTION_SIM_PLAN,
        DEMAND_PREDICTION_SPEC,
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
        (
            "demand-prediction",
            DEMAND_PREDICTION_DESCRIPTION,
            DEMAND_PREDICTION_SPEC,
            DEMAND_PREDICTION_SIM_PLAN,
            DEMAND_PREDICTION_METRIC,
            DEMAND_PREDICTION_EVAL_CASE_SET,
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

    # Hand-craft demo clusters + proposal for demand-prediction (see comment
    # block on `_seed_demand_prediction_demo_fixtures`). Idempotent — bails
    # if clusters already exist.
    fixtures_added = await _seed_demand_prediction_demo_fixtures(conn)
    if fixtures_added:
        for i, w in enumerate(updated):
            if w.id == "demand-prediction":
                updated[i] = SeededWorkflow(
                    id=w.id,
                    description=w.description,
                    inserted=w.inserted,
                    iteration_state=w.iteration_state,
                    iteration_val=w.iteration_val,
                    demo_fixtures_added=True,
                )
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
