"""Unit tests for TriggerDispatcher.dispatch() — run_clustering branch.

No DB required: _fetch_workflow_workspace_id, _dispatch_clustering, and
TriggerRegistry.record_fire are all monkeypatched so the dispatcher's
logic is exercised without real asyncpg connections.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from ownevo_kernel.triggers.dispatcher import DispatchResult, TriggerDispatcher
from ownevo_kernel.triggers.models import TriggerDefinition

_TRIGGER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_WORKFLOW_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
_JOB_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
_FIRE_ID = uuid.UUID("00000000-0000-0000-0000-000000000004")
_WORKSPACE_ID = "default"
_NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


def _trigger(action: str) -> TriggerDefinition:
    return TriggerDefinition(
        id=_TRIGGER_ID,
        workflow_id=_WORKFLOW_ID,
        name="test-trigger",
        kind="cron",
        action=action,  # type: ignore[arg-type]
        config={},
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
        last_fired_at=None,
        fire_count=0,
    )


def _fake_pool() -> MagicMock:
    """Minimal asyncpg pool stub — acquire() yields a context-manager conn."""
    conn = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _fake_fire() -> MagicMock:
    fire = MagicMock()
    fire.id = _FIRE_ID
    return fire


async def test_dispatch_run_clustering_enqueues_job() -> None:
    """Happy path: _dispatch_clustering returns a job_id; detail says 'enqueued'."""
    pool = _fake_pool()
    dispatcher = TriggerDispatcher(pool)

    with (
        patch(
            "ownevo_kernel.triggers.dispatcher._fetch_workflow_workspace_id",
            new=AsyncMock(return_value=_WORKSPACE_ID),
        ),
        patch(
            "ownevo_kernel.triggers.dispatcher._dispatch_clustering",
            new=AsyncMock(return_value=_JOB_ID),
        ),
        patch(
            "ownevo_kernel.triggers.dispatcher.TriggerRegistry.record_fire",
            new=AsyncMock(return_value=_fake_fire()),
        ),
    ):
        result: DispatchResult = await dispatcher.dispatch(_trigger("run_clustering"))

    assert result.status == "ok"
    assert str(_JOB_ID) in result.detail
    assert "enqueued" in result.detail


async def test_dispatch_run_clustering_already_queued() -> None:
    """Idempotent path: _dispatch_clustering returns None; detail says 'already queued'."""
    pool = _fake_pool()
    dispatcher = TriggerDispatcher(pool)

    with (
        patch(
            "ownevo_kernel.triggers.dispatcher._fetch_workflow_workspace_id",
            new=AsyncMock(return_value=_WORKSPACE_ID),
        ),
        patch(
            "ownevo_kernel.triggers.dispatcher._dispatch_clustering",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "ownevo_kernel.triggers.dispatcher.TriggerRegistry.record_fire",
            new=AsyncMock(return_value=_fake_fire()),
        ),
    ):
        result: DispatchResult = await dispatcher.dispatch(_trigger("run_clustering"))

    assert result.status == "ok"
    assert "already queued" in result.detail
