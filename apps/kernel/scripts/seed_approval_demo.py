"""Seed a demo proposal in `gate-passed` state for manual W2.5 testing.

Creates one workflow + one skill + one `gate-passed` iteration + one
matching proposal so the approval queue UI has something to render. The
proposal text + plain_language_summary mirror an example from
`www/preview/s26-rk7p3/07-proposal-detail.html` so the manual click-through
matches the static mock visually.

Usage:

    OWNEVO_DATABASE_URL=postgres://… uv run --package ownevo-kernel \\
        python apps/kernel/scripts/seed_approval_demo.py

Idempotent: if a proposal with the demo skill_id already exists in
`gate-passed`, prints its id and exits 0 without creating duplicates.

Pair with the web app:

    # term 1
    make api
    # term 2
    cd apps/web && npm run dev
    # term 3
    make seed-approval-demo
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

import asyncpg

_ENV_VAR = "OWNEVO_DATABASE_URL"
_WORKFLOW_ID = "demo-demand-prediction"
_SKILL_ID = "demand.demo.seasonal_anomaly_detector"
_PROPOSED_CONTENT = """\
# seasonal-anomaly-detector v4

When forecasting weekly demand for SKU-region pairs:

  1. Compute prior-12-month baseline.
  1a. Compare prior-12 vs prior-24 month YoY shape — flag shape divergence.
  1b. Cross-reference NOAA regional weather forecast deltas.
  2. Apply seasonal index from regional norms (NRF 2024).
  3. If forecast deviates from baseline by >40%, flag for review.
  4. Emit markdown_alert as soon as YoY shape diverges by >2σ AND
     weather signal supports (precipitation/temp anomaly).

Skills retained across turns:
  - regional_norms
  - prior_baseline
  - weather_signals
"""
_PARENT_CONTENT = """\
# seasonal-anomaly-detector v3

When forecasting weekly demand for SKU-region pairs:

  1. Compute prior-12-month baseline.
  2. Apply seasonal index from regional norms (NRF 2024).
  3. If forecast deviates from baseline by >40%, flag for review.
  4. Emit markdown_alert if forecast > inventory × 1.5.

Skills retained across turns:
  - regional_norms
  - prior_baseline
"""
_PLAIN_LANGUAGE = (
    "Detect seasonal anomaly in winter footwear demand 6+ weeks earlier"
)
_RATIONALE = (
    "Gate passed: val_score 0.9420 > best_ever 0.9100 (+0.032); "
    "12 promotable task(s)"
)
_EXPECTED_IMPACT = {
    "forecast_accuracy_pts": 1.1,
    "markdown_exposure_usd": -340000,
    "coverage_skus": 6,
    "lift_basis": "14 retro replays",
}


async def _seed(db_url: str) -> int:
    conn = await asyncpg.connect(db_url)
    try:
        # Workflow.
        await conn.execute(
            """
            INSERT INTO workflows (id, description, spec, mode)
            VALUES ($1, $2, '{"benchmark": "demo"}'::jsonb, 'gated')
            ON CONFLICT (id) DO NOTHING
            """,
            _WORKFLOW_ID,
            "Demand prediction (W2.5 demo)",
        )

        # Skill + parent version.
        await conn.execute(
            """
            INSERT INTO skills (id, kind)
            VALUES ($1, 'instruction'::skill_kind)
            ON CONFLICT (id) DO NOTHING
            """,
            _SKILL_ID,
        )
        parent_version_id: UUID = await conn.fetchval(
            """
            INSERT INTO skill_versions (
                skill_id, version_seq, content, retention_block, created_by
            )
            VALUES ($1, 1, $2, '{}'::jsonb, 'demo-seed')
            ON CONFLICT (skill_id, version_seq) DO UPDATE SET content = EXCLUDED.content
            RETURNING id
            """,
            _SKILL_ID,
            _PARENT_CONTENT,
        )
        await conn.execute(
            "UPDATE skills "
            "SET head_version_id = $2, latest_proposed_version_id = $2 "
            "WHERE id = $1",
            _SKILL_ID,
            parent_version_id,
        )

        # Idempotency: if a gate-passed proposal already exists for this
        # skill + workflow, return its id and don't create another one.
        existing = await conn.fetchval(
            """
            SELECT p.id
            FROM proposals p
            JOIN iterations i ON i.id = p.iteration_id
            WHERE p.skill_id = $1
              AND i.workflow_id = $2
              AND p.state = 'gate-passed'::proposal_state
            ORDER BY p.created_at DESC
            LIMIT 1
            """,
            _SKILL_ID,
            _WORKFLOW_ID,
        )
        if existing is not None:
            print(
                f"Demo proposal already exists in gate-passed: {existing}\n"
                f"  Open: http://localhost:3000/proposals/{existing}",
            )
            return 0

        # Iteration in gate-pass + proposal in gate-passed.
        next_idx = await conn.fetchval(
            "SELECT COALESCE(MAX(iteration_index), -1) + 1 "
            "FROM iterations WHERE workflow_id = $1",
            _WORKFLOW_ID,
        )
        iteration_id: UUID = await conn.fetchval(
            """
            INSERT INTO iterations (
                workflow_id, iteration_index, state,
                parent_skill_version_id, val_score,
                best_ever_score_before, best_ever_score_after, ended_at
            )
            VALUES ($1, $2, 'gate-pass'::iteration_state, $3, 0.942, 0.910, 0.942, now())
            RETURNING id
            """,
            _WORKFLOW_ID,
            next_idx,
            parent_version_id,
        )
        proposal_id: UUID = await conn.fetchval(
            """
            INSERT INTO proposals (
                iteration_id, skill_id, parent_version_id,
                proposed_content, plain_language_summary,
                expected_impact, state, eval_score, eval_rationale
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb,
                    'gate-passed'::proposal_state, 0.942, $7)
            RETURNING id
            """,
            iteration_id,
            _SKILL_ID,
            parent_version_id,
            _PROPOSED_CONTENT,
            _PLAIN_LANGUAGE,
            __import_json_dumps(_EXPECTED_IMPACT),
            _RATIONALE,
        )

        print(
            f"Seeded demo proposal {proposal_id} (workflow={_WORKFLOW_ID}, "
            f"iter={next_idx}, state=gate-passed)\n"
            f"  Open: http://localhost:3000/proposals/{proposal_id}",
        )
        return 0
    finally:
        await conn.close()


def __import_json_dumps(value: dict) -> str:
    """Local import to keep the module top frosty."""
    import json

    return json.dumps(value)


def main() -> None:
    db_url = os.environ.get(_ENV_VAR)
    if not db_url:
        print(f"Error: {_ENV_VAR} is not set", file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(_seed(db_url)))


if __name__ == "__main__":
    main()
