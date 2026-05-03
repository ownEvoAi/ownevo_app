"""read_metrics + analyze_failures — DB-backed.

Pins the train/test discipline contract: by default, neither tool
surfaces traces stamped `fold == "test"` in metric_outputs. The agent
literally cannot read test traces.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import asyncpg
import pytest
from ownevo_kernel.agent_tools import (
    FOLD_KEY,
    TEST_FOLD,
    TestFoldAccessRefused,
    analyze_failures,
    read_metrics,
)
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.traces import TraceCollector

# `db` fixture lives in apps/kernel/tests/conftest.py.
pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set",
)


async def _seed_trace(
    db: asyncpg.Connection,
    *,
    workflow_id: str = "demo-wf",
    metric_outputs: dict | None = None,
    n_tool_errors: int = 0,
    started_at: datetime | None = None,
) -> uuid.UUID:
    """Insert one trace with the given metric_outputs + n synthetic
    tool_call_result error events. Idempotently creates the workflow
    row referenced by the FK so callers don't have to seed it."""
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, 'seeded by test', '{}'::jsonb) "
        "ON CONFLICT (id) DO NOTHING",
        workflow_id,
    )
    collector = TraceCollector(workflow_id=workflow_id)
    if started_at is not None:
        collector.started_at = started_at
    for i in range(n_tool_errors):
        collector.record(
            collector.make_event(
                type="tool_call_result",
                call_id=f"toolu_{i}",
                name="test",
                status="error",
                output=None,
                duration_ms=10,
                error="synthetic failure",
            ),
        )
    if metric_outputs is not None:
        collector.set_metric_outputs(metric_outputs)
    await collector.finalize(db)
    return collector.trace_id


# ---------------------------------------------------------------------------
# read_metrics
# ---------------------------------------------------------------------------


async def test_read_metrics_returns_none_for_unknown_trace(db: asyncpg.Connection):
    assert await read_metrics(db, uuid.uuid4()) is None


async def test_read_metrics_returns_dict(db: asyncpg.Connection):
    tid = await _seed_trace(db, metric_outputs={"rmse": 0.42, FOLD_KEY: "train"})
    metrics = await read_metrics(db, tid)
    assert metrics == {"rmse": 0.42, FOLD_KEY: "train"}


async def test_read_metrics_refuses_test_fold_by_default(db: asyncpg.Connection):
    """Agent must not see test-fold metrics — this is the core
    train/test discipline guarantee."""
    tid = await _seed_trace(db, metric_outputs={"rmse": 0.42, FOLD_KEY: TEST_FOLD})
    with pytest.raises(TestFoldAccessRefused, match="test fold"):
        await read_metrics(db, tid)


async def test_read_metrics_test_fold_accessible_with_opt_in(db: asyncpg.Connection):
    """Gate runner explicitly opts in to test-fold access via
    include_test_fold=True. Only the gate path exercises this."""
    tid = await _seed_trace(db, metric_outputs={"rmse": 0.42, FOLD_KEY: TEST_FOLD})
    metrics = await read_metrics(db, tid, include_test_fold=True)
    assert metrics is not None
    assert metrics[FOLD_KEY] == TEST_FOLD


async def test_read_metrics_no_metrics_returns_none(db: asyncpg.Connection):
    """Trace exists but has no metric_outputs → None, not raise."""
    tid = await _seed_trace(db)  # no metric_outputs
    assert await read_metrics(db, tid) is None


# ---------------------------------------------------------------------------
# analyze_failures
# ---------------------------------------------------------------------------


async def test_analyze_failures_orders_by_error_count(db: asyncpg.Connection):
    """Higher tool_call_result error count → earlier in the result."""
    a = await _seed_trace(db, n_tool_errors=1)
    b = await _seed_trace(db, n_tool_errors=3)
    c = await _seed_trace(db, n_tool_errors=2)

    snapshots = await analyze_failures(db, workflow_id="demo-wf")
    assert [s.trace_id for s in snapshots] == [b, c, a]
    assert [s.tool_errors for s in snapshots] == [3, 2, 1]


async def test_analyze_failures_filters_test_fold_by_default(db: asyncpg.Connection):
    """Test-fold traces must not appear in the agent's failure surface."""
    train_tid = await _seed_trace(
        db, metric_outputs={FOLD_KEY: "train"}, n_tool_errors=2,
    )
    await _seed_trace(
        db, metric_outputs={FOLD_KEY: TEST_FOLD}, n_tool_errors=5,
    )

    snapshots = await analyze_failures(db, workflow_id="demo-wf")
    ids = {s.trace_id for s in snapshots}
    assert train_tid in ids
    # Test-fold trace excluded even though it has more errors.
    assert all(s.fold != TEST_FOLD for s in snapshots)


async def test_analyze_failures_test_fold_accessible_with_opt_in(db: asyncpg.Connection):
    """Gate runner sees test-fold traces only with explicit opt-in."""
    test_tid = await _seed_trace(
        db, metric_outputs={FOLD_KEY: TEST_FOLD}, n_tool_errors=5,
    )
    snapshots = await analyze_failures(
        db, workflow_id="demo-wf", include_test_fold=True,
    )
    assert any(s.trace_id == test_tid for s in snapshots)


async def test_analyze_failures_respects_k(db: asyncpg.Connection):
    """k limits the result size — gate runner uses small k."""
    for n in range(5):
        await _seed_trace(db, n_tool_errors=n + 1)
    snapshots = await analyze_failures(db, workflow_id="demo-wf", k=3)
    assert len(snapshots) == 3


async def test_analyze_failures_includes_metric_outputs(db: asyncpg.Connection):
    """Agent reasons over the actual metric values, not just error
    counts — the snapshot carries metric_outputs through."""
    await _seed_trace(
        db,
        metric_outputs={"rmse": 1.23, FOLD_KEY: "train"},
        n_tool_errors=1,
    )
    snapshots = await analyze_failures(db, workflow_id="demo-wf")
    assert snapshots[0].metric_outputs == {"rmse": 1.23, FOLD_KEY: "train"}


async def test_analyze_failures_workflow_scoped(db: asyncpg.Connection):
    """Different workflows shouldn't bleed into each other's failure
    analysis."""
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ('other-wf', 'other', '{}'::jsonb)",
    )
    await _seed_trace(db, workflow_id="demo-wf", n_tool_errors=1)
    await _seed_trace(db, workflow_id="other-wf", n_tool_errors=10)

    snapshots = await analyze_failures(db, workflow_id="demo-wf")
    assert all(s.workflow_id == "demo-wf" for s in snapshots)


async def test_analyze_failures_empty_when_no_traces(db: asyncpg.Connection):
    snapshots = await analyze_failures(db, workflow_id="demo-wf")
    assert snapshots == []
