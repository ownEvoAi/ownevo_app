"""Persist a generated EvalCaseSet to the eval_cases table (A4.1).

The bridge between A4.1's typed `EvalCaseSet` artifact and the W2.3 CRUD
seam in `ownevo_kernel.eval_cases.registry`. Every case becomes one row
with `provenance=ProvenanceKind.NL_GEN`. The case-level shape (sim_seed,
n_steps, target_step_index, target_label_field, expected_value, rationale,
case_id) is preserved across a `(input, expected_behavior)` JSONB split:

  * `input`  = {sim_seed, n_steps, target_step_index} — what the replay
    helper needs to reproduce the targeted event.
  * `expected_behavior` = {target_label_field, expected_value, rationale,
    case_id, provenance: {kind, source}} — what the case asserts about
    that event, plus the audit-trail fields.

This split mirrors `EvalCase.input` / `EvalCase.expected_behavior` in the
SQL schema. It also keeps the gate runner's "read input, drive replay,
compare expected_behavior" interpretation cheap — no JSONB sub-traversal
beyond the obvious keys.

Single transaction: either the whole set lands or nothing does. Each row's
`workflow_id` is set to the EvalCaseSet's `workflow_spec_id` so the gate
runner's per-workflow filter (W2.3) picks them up automatically.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from ..eval_cases.registry import add_eval_case
from ..types import EvalCase, ProvenanceKind
from .eval_case_set import EvalCaseSet, GeneratedEvalCase


def _input_payload(case: GeneratedEvalCase) -> dict[str, Any]:
    """Replay parameters — what the replay helper reads off `EvalCase.input`."""
    return {
        "sim_seed": case.sim_seed,
        "n_steps": case.n_steps,
        "target_step_index": case.target_step_index,
    }


def _expected_behavior_payload(case: GeneratedEvalCase) -> dict[str, Any]:
    """Assertion + audit fields — what the gate compares against."""
    return {
        "case_id": case.case_id,
        "target_label_field": case.target_label_field,
        "expected_value": case.expected_value,
        "rationale": case.rationale,
        "provenance": {
            "kind": case.provenance.kind,
            "source": case.provenance.source,
        },
    }


async def persist_eval_case_set(
    conn: asyncpg.Connection,
    case_set: EvalCaseSet,
    *,
    workflow_id: str | None = None,
) -> list[EvalCase]:
    """Insert every case in `case_set` as an `eval_cases` row.

    Args:
        conn: An asyncpg connection with the `eval_cases` schema migrated.
        case_set: A validated `EvalCaseSet`.
        workflow_id: Optional override for the per-row `workflow_id`. Defaults
            to `case_set.workflow_spec_id`. Pass `None` (the default) when the
            workflow row exists; pass an explicit value for tests that don't
            create the workflow row.

    Returns:
        The inserted `EvalCase` rows in source order.

    Raises:
        asyncpg.PostgresError: any row fails to insert (transaction rolls back).
    """
    wf_id = workflow_id if workflow_id is not None else case_set.workflow_spec_id

    inserted: list[EvalCase] = []
    async with conn.transaction():
        for case in case_set.cases:
            row = await add_eval_case(
                conn,
                provenance=ProvenanceKind.NL_GEN,
                input=_input_payload(case),
                expected_behavior=_expected_behavior_payload(case),
                workflow_id=wf_id,
                is_test_fold=case.is_test_fold,
            )
            inserted.append(row)
    return inserted


__all__ = [
    "persist_eval_case_set",
]
