"""High-fidelity replay agent solver — drop-in for `solve_with_agent`.

Track 9.0.3 Slice A. When `workflows.sim_tier='replay'`, the
iteration_runner swaps `solve_with_agent` (which calls an LLM per case)
for `solve_with_replay_agent` (which reads the agent's structured
outputs from a prior real iteration and emits them as ReplayResults).

Use cases this unlocks:
  * Pre-production validation of an instruction edit — run the new
    instruction against the SAME captured agent predictions as the
    last real iteration and see whether the gate verdict changes,
    without paying for fresh LLM calls.
  * Cheap regression coverage — pin a workflow's behaviour against a
    known-good captured iteration; any code change that breaks the
    replay path (clustering, metric compute, gate logic) surfaces
    immediately.
  * Reproducing a specific iteration in isolation — replay an old
    iteration's predictions while the workflow's other artifacts
    (metric, sim, eval cases) have evolved, to see "what would the
    gate have said about this run today."

Where the captured data comes from: `iteration_case_outputs` (migration
0008) is already populated by `_persist_case_outputs` in
iteration_runner — every real iteration writes per-case structured
output there. ReplayAgentSolver does not need a separate capture step;
any prior real iteration is replay-able by default.

Mapping `iteration_case_outputs` row → ReplayResult:

    output_json = {
        "case_id": ...,
        "predicted": <bool>,            → actual_value
        "expected":  <bool>,
        "rationale": <str>,             → rationale
        "is_test_fold": <bool>,
    }
    passed:         <bool>              → passed
    output_payload: <dict | None>       → output_payload

`expected_value` comes from the live case set (not the captured row)
so a replay against a workflow whose `expected_value` has since
changed correctly reflects today's gate semantics, not the captured
moment's. The audit trail makes the replay origin obvious via the
`[replay]` rationale prefix.

Fallback policy (`ReplaySimConfig.fallback`):
  * `'error'` — raise `ReplayCaseMissingError` when a case isn't in
    the captured set. Default; safer for validation.
  * `'mock'`  — degrade to MockAgentSolver for missing cases. Requires
    `workflows.mock_sim_config` to also be set.
  * `'real'`  — degrade to live LLM for missing cases. Defeats the
    "zero LLM cost" property but unblocks workflows that added new
    eval cases after the source iteration ran.

The 'mock' and 'real' fallbacks are wired in `run_with_replay_agent`
(eval_runner/runner.py) rather than here — this module stays
DB-only-dependency-free apart from the asyncpg.Connection it reads
captured outputs from.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from ..nl_gen.eval_case_set import EvalCaseSet
from ..nl_gen.eval_replay import ReplayResult
from ..nl_gen.metric_def import MetricDefinition
from ..nl_gen.sim_plan import SimulationPlan
from ..nl_gen.spec import WorkflowSpec


_REPLAY_RATIONALE_PREFIX = "[replay] "


class ReplayCaseMissingError(RuntimeError):
    """A requested case isn't in the source iteration's captured set.

    Raised when `ReplaySimConfig.fallback='error'`. Carries the
    missing case_ids so the caller can surface them to the operator
    (or decide to switch fallback mode).
    """

    def __init__(self, source_iteration_id: UUID, missing_case_ids: list[str]) -> None:
        super().__init__(
            f"Source iteration {source_iteration_id} has no captured "
            f"output for cases: {sorted(missing_case_ids)}. "
            "Either set `replay_sim_config.fallback` to 'mock' or 'real', "
            "or pick a source iteration that covers every current case.",
        )
        self.source_iteration_id = source_iteration_id
        self.missing_case_ids = missing_case_ids


async def solve_with_replay_agent(
    conn: asyncpg.Connection,
    case_set: EvalCaseSet,
    plan: SimulationPlan,
    spec: WorkflowSpec,
    metric: MetricDefinition,
    *,
    source_iteration_id: UUID,
) -> tuple[list[ReplayResult], list[str]]:
    """Read captured per-case outputs and emit them as ReplayResults.

    Same signature spine as `agent_solver.solve_with_agent` (case_set,
    plan, spec, metric, plus solver-specific kwargs) so the upstream
    `run_with_*` wrappers can substitute it at one site.

    Args:
        conn: asyncpg connection used to read iteration_case_outputs.
            Held only for the lookup query — released back to the
            caller before this function returns.
        case_set: Cases to predict. Each case's `case_id` is the
            lookup key into the captured set.
        plan / spec / metric: Carried for xref parity with
            `solve_with_agent`. Replay doesn't execute the sim, but
            workflow_spec_id agreement is still enforced so a
            mis-stitched trio fails fast on replay tier the same way
            it does on real tier.
        source_iteration_id: UUID of the iteration whose captured
            outputs this run replays against.

    Returns:
        A tuple `(results, missing_case_ids)`:
          * `results`: ReplayResult per case present in the capture
            set, in `case_set.cases` order. Cases NOT in the capture
            set are absent from this list — the caller decides what
            to do with them per the fallback policy.
          * `missing_case_ids`: case_ids requested but not found in
            the captured set. Empty list when every case was covered.

    Returning the missing list rather than raising lets the wrapper
    layer (`run_with_replay_agent`) apply the configured fallback
    without re-querying. If the fallback is 'error', the wrapper
    raises `ReplayCaseMissingError`; if 'mock' or 'real', it fills
    the gap with that solver's output.

    Raises:
        ValueError: workflow_spec_id mismatch between case_set / plan
            / metric and spec.
    """
    if case_set.workflow_spec_id != spec.id:
        raise ValueError(
            f"case_set.workflow_spec_id={case_set.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}",
        )
    if plan.workflow_spec_id != spec.id:
        raise ValueError(
            f"plan.workflow_spec_id={plan.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}",
        )
    if metric.workflow_spec_id != spec.id:
        raise ValueError(
            f"metric.workflow_spec_id={metric.workflow_spec_id!r} "
            f"does not match spec.id={spec.id!r}",
        )

    # Fetch every captured row for the source iteration in one query.
    # iteration_case_outputs joins eval_cases on eval_case_id; we need
    # the human-readable case_id to match against case_set.cases, so
    # join through.
    rows = await conn.fetch(
        """
        SELECT
            ec.case_id           AS case_id,
            ico.output_json      AS output_json,
            ico.passed           AS passed,
            ico.output_payload   AS output_payload
        FROM iteration_case_outputs ico
        JOIN eval_cases ec ON ec.id = ico.eval_case_id
        WHERE ico.iteration_id = $1
        """,
        source_iteration_id,
    )
    captured: dict[str, dict[str, Any]] = {
        r["case_id"]: {
            "output_json": _decode_jsonb(r["output_json"]),
            "passed": bool(r["passed"]),
            "output_payload": _decode_jsonb(r["output_payload"]),
        }
        for r in rows
    }

    results: list[ReplayResult] = []
    missing: list[str] = []
    for case in case_set.cases:
        entry = captured.get(case.case_id)
        if entry is None:
            missing.append(case.case_id)
            continue
        output_json = entry["output_json"] or {}
        predicted = output_json.get("predicted")
        # Coerce to bool — the captured value is stored via json.dumps
        # so it round-trips as the same Python type, but a defensive
        # cast guards against odd legacy rows from before the predicted
        # field was guaranteed bool.
        actual_value: Any = bool(predicted) if isinstance(predicted, bool) else predicted
        rationale = output_json.get("rationale")
        rationale_str = (
            f"{_REPLAY_RATIONALE_PREFIX}{rationale}"
            if isinstance(rationale, str) and rationale
            else f"{_REPLAY_RATIONALE_PREFIX}(no rationale captured)"
        )
        results.append(
            ReplayResult(
                case_id=case.case_id,
                passed=entry["passed"],
                actual_value=actual_value,
                expected_value=case.expected_value,
                rationale=rationale_str,
                output_payload=entry["output_payload"] if isinstance(entry["output_payload"], dict) else None,
            ),
        )

    return results, missing


def _decode_jsonb(value: Any) -> Any:
    """asyncpg may return JSONB as dict / list / None or as a raw
    JSON string depending on the codec wired on the pool. Accept
    both shapes the way the rest of the kernel does (matches
    `iteration_runner._coerce_jsonb`)."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        import json
        return json.loads(value)
    return value


__all__ = [
    "ReplayCaseMissingError",
    "solve_with_replay_agent",
]
