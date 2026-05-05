"""read_metrics / analyze_failures — agent's read surface over traces.

Both tools enforce **train/test discipline** (per W2.2 contract). The
agent must never see test-fold rows during training — those are the
held-out cases the gate uses to compute val_score.

How the filter works:
  Each trace's `metric_outputs` JSONB is expected to carry a
  string field `fold` ∈ {"train", "validation", "test"}. The runner
  that produces a trace stamps this field. `read_metrics` and
  `analyze_failures` refuse to surface traces with `fold == "test"`
  unless the caller explicitly opts in (via `include_test_fold=True`,
  reserved for the gate runner).

This is a convention layered on top of the schema rather than a column
constraint; the schema-side `eval_cases.is_test_fold` column gets joined
in W4 once the iteration↔eval_case linkage exists. Until then, the
metric_outputs convention is the enforcement boundary the agent tools
respect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg


class TestFoldAccessRefused(PermissionError):
    """Raised when an agent tool would return a test-fold trace and the
    caller did not opt in. Surfaces as a structured `tool_call_result`
    error so the agent's failure mode is observable in traces."""

    # Keep pytest from trying to collect this as a test class because
    # the name starts with "Test" — it's a runtime error type, not a fixture.
    __test__ = False


# The metric_outputs key the runner uses to declare which fold a trace ran on.
FOLD_KEY = "fold"
TEST_FOLD = "test"


# ---------------------------------------------------------------------------
# read_metrics
# ---------------------------------------------------------------------------


async def read_metrics(
    conn: asyncpg.Connection,
    trace_id: UUID,
    *,
    include_test_fold: bool = False,
) -> dict[str, Any] | None:
    """Return `traces.metric_outputs` for `trace_id`.

    Returns None when the trace doesn't exist or has no metric_outputs.
    Raises `TestFoldAccessRefused` when the trace is a test-fold run
    and the caller did not opt in.
    """
    row = await conn.fetchrow(
        "SELECT metric_outputs FROM traces WHERE id = $1",
        trace_id,
    )
    if row is None:
        return None
    raw = row["metric_outputs"]
    if raw is None:
        return None
    metrics = _decode_jsonb(raw)
    if not isinstance(metrics, dict):
        return None
    if not include_test_fold and metrics.get(FOLD_KEY) == TEST_FOLD:
        raise TestFoldAccessRefused(
            f"trace {trace_id} ran on the test fold; "
            "set include_test_fold=True only from the gate runner",
        )
    return metrics


# ---------------------------------------------------------------------------
# analyze_failures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureSnapshot:
    """One worst-prediction summary the agent reasons over.

    `tool_errors` is the count of `tool_call_result` events with
    `status="error"` in the trace's events array — a cheap signal of
    "this run was rough." `metric_outputs` is included so the agent can
    see the actual scores, not just error counts.

    `iteration_state`, `sandbox_error_class`, and `eval_rationale` are
    populated when the trace is linked to a finalized iteration —
    cross-iteration failure memory (TODO-23). For sandbox-error
    iterations, `eval_rationale` carries the gate's failure signature
    (e.g. "Runner raised TimeoutError: ...", OOM trace excerpt).
    """

    trace_id: UUID
    iteration_id: UUID | None
    skill_version_id: UUID | None
    workflow_id: str | None
    started_at: Any  # datetime; kept Any to avoid an import for one type
    metric_outputs: dict[str, Any] | None
    tool_errors: int
    fold: str | None
    iteration_state: str | None = None
    sandbox_error_class: str | None = None
    eval_rationale: str | None = None


_SANDBOX_ERROR_STATE = "sandbox-error"


async def analyze_failures(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    k: int = 10,
    include_test_fold: bool = False,
) -> list[FailureSnapshot]:
    """Return up to `k` recent traces for `workflow_id`, sorted with
    sandbox-error iterations first, then by tool_call_result error count
    (most failures first).

    Each row LEFT JOINs the linked iteration + proposal so the snapshot
    carries `iteration_state`, `sandbox_error_class`, and the gate's
    `eval_rationale` — the human-readable failure signature the agent
    needs to avoid repeating prior crashes (TODO-23).

    Train/test discipline: by default, traces stamped `fold == "test"`
    in metric_outputs are filtered out so the agent never sees them.
    `include_test_fold=True` is reserved for the gate runner.
    """
    rows = await conn.fetch(
        """
        SELECT t.id, t.iteration_id, t.skill_version_id, t.workflow_id,
               t.started_at, t.events, t.metric_outputs,
               i.state::text                AS iteration_state,
               i.sandbox_error_class::text  AS sandbox_error_class,
               p.eval_rationale             AS eval_rationale
        FROM traces t
        LEFT JOIN iterations i ON i.id = t.iteration_id
        LEFT JOIN proposals  p ON p.iteration_id = t.iteration_id
        WHERE t.workflow_id = $1
        ORDER BY t.started_at DESC
        LIMIT $2
        """,
        workflow_id,
        # Pull a generous window so we can post-filter by fold and still
        # honor `k`. 4× is cheap on Postgres at MVP scale.
        k * 4,
    )

    snapshots: list[FailureSnapshot] = []
    for row in rows:
        metrics = _decode_jsonb(row["metric_outputs"])
        fold = metrics.get(FOLD_KEY) if isinstance(metrics, dict) else None
        if not include_test_fold and fold == TEST_FOLD:
            continue
        events = _decode_jsonb(row["events"]) or []
        tool_errors = _count_tool_errors(events)
        snapshots.append(
            FailureSnapshot(
                trace_id=row["id"],
                iteration_id=row["iteration_id"],
                skill_version_id=row["skill_version_id"],
                workflow_id=row["workflow_id"],
                started_at=row["started_at"],
                metric_outputs=metrics,
                tool_errors=tool_errors,
                fold=fold,
                iteration_state=row["iteration_state"],
                sandbox_error_class=row["sandbox_error_class"],
                eval_rationale=row["eval_rationale"],
            ),
        )
        if len(snapshots) >= k:
            break

    # Sandbox-error iterations rank first regardless of tool-call counts —
    # the gate's failure rationale is exactly what the agent needs to read
    # to avoid re-triggering the same crash.
    snapshots.sort(
        key=lambda s: (
            0 if s.iteration_state == _SANDBOX_ERROR_STATE else 1,
            -s.tool_errors,
            -s.started_at.timestamp(),
        ),
    )
    return snapshots[:k]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _decode_jsonb(raw: Any) -> Any:
    """Normalize asyncpg JSONB output — may arrive as str or native object."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


def _count_tool_errors(events: list[dict[str, Any]]) -> int:
    return sum(
        1
        for e in events
        if isinstance(e, dict)
        and e.get("type") == "tool_call_result"
        and e.get("status") == "error"
    )
