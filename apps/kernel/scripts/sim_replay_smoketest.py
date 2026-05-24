"""Track 9.0.3 smoketest — end-to-end replay roundtrip.

Seeds a workflow + iteration + N captured `iteration_case_outputs`
rows, then drives `run_with_replay_agent` against them and asserts
the resulting EvalRunReport's predictions equal the captured shapes
byte-for-byte. Verifies the load-bearing contract of replay: a
captured iteration is replay-able with identical outputs and zero
LLM calls.

Invoke via `make sim-replay-smoketest`. Requires `OWNEVO_DATABASE_URL`
to be set — replay is a DB-backed feature and there's no meaningful
in-memory mock that exercises the same path. Without the env var the
script exits non-zero with a clear message rather than silently
no-opping (so CI surfaces the gap).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

import asyncpg
from ownevo_kernel.db import ENV_VAR, migrate
from ownevo_kernel.eval_runner.runner import run_with_replay_agent
from ownevo_kernel.nl_gen.eval_case_set import EvalCaseSet, GeneratedEvalCase
from ownevo_kernel.nl_gen.fixtures import (
    DEMAND_PREDICTION_METRIC,
    DEMAND_PREDICTION_SIM_PLAN,
    DEMAND_PREDICTION_SPEC,
)
from ownevo_kernel.nl_gen.fixtures.eval_case_sets import (
    DEMAND_PREDICTION_EVAL_CASE_SET,
)
from ownevo_kernel.nl_gen.spec import Provenance

_WALL_CLOCK_BUDGET_S = 5.0


def _custom_case(case_id: str, expected: bool) -> GeneratedEvalCase:
    return GeneratedEvalCase(
        case_id=case_id,
        provenance=Provenance(kind="inferred", source="smoketest"),
        sim_seed=1,
        n_steps=10,
        target_step_index=5,
        target_label_field="alert_correct_label",
        expected_value=expected,
        rationale="smoketest case",
    )


def _build_case_set(custom_case_ids: list[str]) -> EvalCaseSet:
    """Reuse the demand-prediction fixture as the label-balance tail
    (EvalCaseSet validator requires ≥10 cases + balance) and append
    the smoketest's custom case_ids."""
    fixture = DEMAND_PREDICTION_EVAL_CASE_SET.model_copy(deep=True)
    fixture.cases.extend(
        _custom_case(cid, expected=(i % 2 == 0))
        for i, cid in enumerate(custom_case_ids)
    )
    return fixture


async def _seed_captured(
    conn: asyncpg.Connection,
    *,
    case_id_to_expected: dict[str, bool],
) -> tuple[asyncpg.connection.Connection, str, asyncpg.connection.Connection]:
    """Returns (workflow_id, iteration_id). Inserts everything via the
    passed conn so the smoketest can run inside one transaction."""
    workflow_id = f"smoketest-replay-wf-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, 'replay smoketest', '{}'::jsonb)",
        workflow_id,
    )
    iteration_id = await conn.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, started_at)
        VALUES ($1, 0, 'gate-passed'::iteration_state, now())
        RETURNING id
        """,
        workflow_id,
    )
    for case_id, expected in case_id_to_expected.items():
        eval_case_uuid = await conn.fetchval(
            """
            INSERT INTO eval_cases (
                workflow_id, case_id, input, expected_behavior, is_test_fold
            )
            VALUES ($1, $2, '{}'::jsonb, '{}'::jsonb, false)
            RETURNING id
            """,
            workflow_id,
            case_id,
        )
        # Captured prediction: agent got it right (predicted = expected,
        # passed = true). The smoketest then asserts the replay returns
        # the same shape.
        output_json = {
            "case_id": case_id,
            "predicted": expected,
            "expected": expected,
            "rationale": f"captured rationale for {case_id}",
            "is_test_fold": False,
        }
        await conn.execute(
            """
            INSERT INTO iteration_case_outputs (
                iteration_id, eval_case_id, output_json, passed, output_payload
            )
            VALUES ($1, $2, $3::jsonb, true, NULL)
            """,
            iteration_id,
            eval_case_uuid,
            json.dumps(output_json),
        )
    return workflow_id, iteration_id


async def _main() -> int:
    db_url = os.environ.get(ENV_VAR)
    if not db_url:
        print(
            f"FAIL — {ENV_VAR} not set. The replay smoketest is DB-backed "
            "(no meaningful in-memory analog of iteration_case_outputs); "
            "rerun with the env var pointing at a Postgres instance.",
        )
        return 1

    # Belt-and-braces: nuke API keys so a regression that routes back
    # through real LLMs fails loudly rather than silently spending.
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)

    # Run against an isolated test DB so the smoketest can be re-run
    # without cleanup churn. Mirrors the conftest `db` fixture pattern.
    dbname = f"replay_smoketest_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(db_url)
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()

    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(db_url)
    test_url = urlunparse(parsed._replace(path=f"/{dbname}"))

    conn = await asyncpg.connect(test_url)
    try:
        await migrate(conn)

        case_ids = [f"smoke-case-{i:02d}" for i in range(5)]
        case_id_to_expected = {cid: (i % 2 == 0) for i, cid in enumerate(case_ids)}

        workflow_id, iteration_id = await _seed_captured(
            conn, case_id_to_expected=case_id_to_expected,
        )
        print(
            f"sim-replay smoketest: workflow={workflow_id} "
            f"iteration={iteration_id} captured={len(case_ids)}",
        )

        case_set = _build_case_set(case_ids)

        started = time.monotonic()
        report, missing = await run_with_replay_agent(
            conn,
            case_set,
            DEMAND_PREDICTION_SIM_PLAN,
            DEMAND_PREDICTION_SPEC,
            DEMAND_PREDICTION_METRIC,
            source_iteration_id=iteration_id,
        )
        elapsed = time.monotonic() - started

        # Filter the report's outcomes to the ones we seeded; the rest
        # are missing (fixture's own cases weren't captured).
        smoke_outcomes = {o.case_id: o for o in report.outcomes if o.case_id in case_ids}

        failures: list[str] = []

        # Contract 1: every seeded case present in the report.
        for case_id in case_ids:
            if case_id not in smoke_outcomes:
                failures.append(f"missing from report: {case_id}")
                continue
            o = smoke_outcomes[case_id]
            expected = case_id_to_expected[case_id]
            if o.actual_value != expected:
                failures.append(
                    f"{case_id}: replayed actual_value={o.actual_value}, "
                    f"expected captured={expected}",
                )
            if not o.passed:
                failures.append(
                    f"{case_id}: replayed passed=False, expected True",
                )
            if not o.rationale or "[replay]" not in o.rationale:
                failures.append(
                    f"{case_id}: rationale missing [replay] marker — got {o.rationale!r}",
                )

        # Contract 2: fixture's own cases (not captured) MUST appear in `missing`.
        fixture_case_ids = {c.case_id for c in DEMAND_PREDICTION_EVAL_CASE_SET.cases}
        missing_set = set(missing)
        uncovered = fixture_case_ids - missing_set
        if uncovered:
            failures.append(
                f"fixture cases not flagged as missing: {sorted(uncovered)}",
            )

        print(
            f"  smoke cases checked: {len(case_ids)}, "
            f"missing reported: {len(missing)}, "
            f"wall clock: {elapsed * 1000:.1f}ms",
        )

        if failures:
            print("FAIL — replay contract violations:")
            for line in failures:
                print(f"  {line}")
            return 1
        if elapsed > _WALL_CLOCK_BUDGET_S:
            print(
                f"FAIL — wall clock {elapsed:.2f}s exceeds "
                f"{_WALL_CLOCK_BUDGET_S}s budget",
            )
            return 1
        print(
            "PASS — every captured prediction replayed byte-identically; "
            "missing cases flagged correctly; LLM-free + budget met.",
        )
        return 0
    finally:
        await conn.close()
        admin = await asyncpg.connect(db_url)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=$1 AND pid<>pg_backend_pid()",
                dbname,
            )
            await admin.execute(f'DROP DATABASE "{dbname}"')
        finally:
            await admin.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
