"""Trigger dispatcher — route a fired trigger to its configured action (Track 17.1).

`TriggerDispatcher` is the central coordinator:

1.  Receives a `TriggerDefinition` and an optional payload.
2.  Resolves the ``workspace_id`` for the trigger's workflow so every
    workspace-scoped DB write is properly bound under RLS.
3.  Validates any kind-specific prerequisites (HMAC already checked by the
    webhook route; cron/threshold triggers have no incoming payload to validate).
4.  Dispatches to `action_run_clustering`, `action_run_iteration`, or
    `action_ingest_failures`.
5.  Records a `TriggerFire` row via `TriggerRegistry.record_fire`.
6.  Returns a `DispatchResult` for the caller to surface in the API response.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from pydantic import BaseModel, ConfigDict

from ..tenant_session import DEFAULT_WORKSPACE_ID, acquire_workspace_conn
from .models import TriggerAction, TriggerDefinition, TriggerFire
from .registry import TriggerRegistry

_log = logging.getLogger(__name__)


class DispatchResult(BaseModel):
    """Return value from a successful or failed trigger dispatch."""

    model_config = ConfigDict(extra="forbid")

    trigger_id: str
    workflow_id: str
    action: TriggerAction
    status: str  # "ok" | "error"
    detail: str  # short human-readable description
    fire_id: str | None = None


class TriggerDispatcher:
    """Dispatches a fired trigger to the appropriate action."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def dispatch(
        self,
        trigger: TriggerDefinition,
        *,
        payload_summary: str | None = None,
        failure_texts: list[str] | None = None,
    ) -> DispatchResult:
        """Execute `trigger.action` and record the fire.

        Args:
            trigger: The definition to dispatch.
            payload_summary: Short description of what caused the fire
                (e.g. the Slack message text, the metric value, the cron
                expression tick).  Stored in `trigger_fires.payload_summary`.
            failure_texts: For ``ingest_failures`` actions, the list of
                failure-description strings to convert to AgentEvents.
        """
        workflow_id = str(trigger.workflow_id)
        trigger_id = str(trigger.id)

        status = "ok"
        detail = ""
        error_msg: str | None = None

        try:
            # Resolve the workspace so action helpers can scope their DB writes
            # through acquire_workspace_conn.  trigger_definitions is not
            # workspace-scoped, so workflow_id is always readable here; the
            # workspace_id is fetched from the workflows row.  Keeping the
            # lookup inside the try block ensures a DB blip here is caught and
            # recorded as a fire with status="error" rather than bubbling as an
            # unhandled exception.
            workspace_id = await _fetch_workflow_workspace_id(self._pool, workflow_id)

            if trigger.action == "run_clustering":
                n = await _dispatch_clustering(self._pool, workflow_id, workspace_id)
                detail = f"clustering completed: {n} cluster(s)"

            elif trigger.action == "run_iteration":
                await _dispatch_iteration(self._pool, workflow_id, workspace_id)
                detail = "iteration task spawned"

            elif trigger.action == "ingest_failures":
                texts = failure_texts or []
                trace_id = await _dispatch_ingest(
                    self._pool, workflow_id, workspace_id, texts, payload_summary
                )
                detail = f"ingested {len(texts)} failure(s) → trace {trace_id}"

            else:
                raise ValueError(f"unknown trigger action: {trigger.action!r}")

        except Exception as exc:  # noqa: BLE001 — dispatcher must not bubble
            _log.exception(
                "trigger dispatcher: action %r failed for trigger %s / workflow %s",
                trigger.action,
                trigger_id,
                workflow_id,
            )
            status = "error"
            error_msg = str(exc)
            detail = f"error: {exc}"

        # Record the fire regardless of outcome.  trigger_fires is not
        # workspace-scoped, so a raw pool.acquire() is correct here.
        fire: TriggerFire | None = None
        try:
            async with self._pool.acquire() as conn:
                fire = await TriggerRegistry.record_fire(
                    conn,
                    trigger_id=trigger_id,
                    workflow_id=workflow_id,
                    action=trigger.action,
                    status=status,
                    error_message=error_msg,
                    payload_summary=payload_summary,
                )
        except Exception:  # noqa: BLE001
            _log.exception(
                "trigger dispatcher: failed to record fire for trigger %s",
                trigger_id,
            )

        return DispatchResult(
            trigger_id=trigger_id,
            workflow_id=workflow_id,
            action=trigger.action,
            status=status,
            detail=detail,
            fire_id=str(fire.id) if fire else None,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_workflow_workspace_id(pool: asyncpg.Pool, workflow_id: str) -> str:
    """Return the workspace_id for the given workflow.

    Queries ``workflows`` under the ``DEFAULT_WORKSPACE_ID`` scope so the
    connection is RLS-bound.  In the single-tenant MVP every workflow
    belongs to the default workspace, so the scoped query always resolves.
    Falls back to ``DEFAULT_WORKSPACE_ID`` when the workflow row is not
    found — this can happen when a trigger fires for a workflow that was
    deleted after the trigger was scheduled; the downstream action will
    then fail cleanly under RLS rather than silently operating on the
    wrong scope.
    """
    async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
        row = await conn.fetchrow(
            "SELECT workspace_id FROM workflows WHERE id = $1",
            workflow_id,
        )
    if row is None:
        _log.warning(
            "trigger dispatcher: workflow %s not found; "
            "falling back to default workspace for RLS scope",
            workflow_id,
        )
        return DEFAULT_WORKSPACE_ID
    return str(row["workspace_id"])


async def _dispatch_clustering(
    pool: asyncpg.Pool, workflow_id: str, workspace_id: str
) -> int:
    from .actions import action_run_clustering
    return await action_run_clustering(pool, workflow_id, workspace_id)


async def _dispatch_iteration(
    pool: asyncpg.Pool, workflow_id: str, workspace_id: str
) -> None:
    from .actions import action_run_iteration
    await action_run_iteration(pool, workflow_id, workspace_id)


async def _dispatch_ingest(
    pool: asyncpg.Pool,
    workflow_id: str,
    workspace_id: str,
    texts: list[str],
    payload_summary: str | None,
) -> str | None:
    from .actions import action_ingest_failures
    source = payload_summary or "trigger"
    return await action_ingest_failures(pool, workflow_id, texts, workspace_id, source=source)
