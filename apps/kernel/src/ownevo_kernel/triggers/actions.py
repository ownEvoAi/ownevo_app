"""Trigger action implementations (Track 17.1).

When a trigger fires it dispatches one of three actions:

``run_clustering``
    Runs `cluster_production_failures` for the workflow immediately (no
    debounce; the trigger itself is the debounce mechanism for cron /
    threshold triggers).

``run_iteration``
    Enqueues one improvement-loop iteration for the workflow (not yet
    wired to a full async job queue — delegates to the same path as the
    manual "Run iteration" button in the UI).

``ingest_failures``
    Converts a list of failure-description strings to
    ``ToolCallResultEvent(status="error")`` AgentEvents and persists them
    as a synthetic trace bound to the workflow.  Used by the Slack and
    email ingestion paths to materialise external failure signals as
    first-class traces before clustering picks them up.

All actions accept a `pool` rather than a single connection so they can
manage their own transaction scope.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

_log = logging.getLogger(__name__)


async def action_run_clustering(
    pool: asyncpg.Pool,
    workflow_id: str,
) -> int:
    """Run the production-failure clustering pipeline for `workflow_id`.

    Returns the number of clusters persisted.  Imports the heavy
    clustering extras lazily — the caller can catch `ImportError` and
    log a meaningful message when the extras are absent.
    """
    from ..clustering.default_impl import (
        AnthropicLabeler,
        HDBSCANClusterer,
        SentenceTransformerEmbedder,
        UMAPReducer,
    )
    from ..clustering.from_traces import cluster_production_failures

    async with pool.acquire() as conn:
        results = await cluster_production_failures(
            conn,
            workflow_id,
            embedder=SentenceTransformerEmbedder(),
            reducer=UMAPReducer(),
            clusterer=HDBSCANClusterer(),
            labeler=AnthropicLabeler(),
        )
    return len(results)


async def action_run_iteration(
    pool: asyncpg.Pool,
    workflow_id: str,
) -> str | None:
    """Start one improvement-loop iteration for `workflow_id`.

    Creates an `iterations` row with state='running', then spawns the
    iteration runner as a background task.  Returns the new iteration ID.

    The iteration runner is imported lazily to keep the trigger module
    free of the heavy `agent` extra.
    """
    from ..iteration_runner import run_iteration_for_workflow

    iteration_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO iterations (id, workflow_id, iteration_index, state)
            VALUES ($1, $2,
                (SELECT COALESCE(MAX(iteration_index), 0) + 1
                 FROM iterations WHERE workflow_id = $2),
                'running')
            """,
            iteration_id,
            workflow_id,
        )

    # Fire-and-forget the heavy iteration in a background task.
    # Exceptions are logged inside run_iteration_for_workflow.
    asyncio.create_task(
        run_iteration_for_workflow(pool, workflow_id, iteration_id),
        name=f"trigger-iteration-{iteration_id[:8]}",
    )
    _log.info(
        "trigger: started iteration %s for workflow %s",
        iteration_id,
        workflow_id,
    )
    return iteration_id


async def action_ingest_failures(
    pool: asyncpg.Pool,
    workflow_id: str,
    failure_texts: list[str],
    source: str = "trigger",
) -> str | None:
    """Persist `failure_texts` as a synthetic trace bound to `workflow_id`.

    Each string in `failure_texts` becomes one
    ``ToolCallResultEvent(status="error")`` in the trace.  The trace is
    tagged with ``source`` so the UI can distinguish synthetic traces from
    real production traces.

    Returns the new `trace_id`, or None when `failure_texts` is empty.
    """
    if not failure_texts:
        return None

    from ownevo_format import ToolCallResultEvent

    trace_id = str(uuid.uuid4())
    events: list[dict] = []
    for idx, text in enumerate(failure_texts):
        evt = ToolCallResultEvent(
            trace_id=trace_id,
            span_id=str(uuid.uuid4()),
            tool_name="external_signal",
            call_id=f"call_{idx}",
            status="error",
            result=None,
            error=text,
        )
        events.append(evt.model_dump(mode="json"))

    import json

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO traces
                (id, workflow_id, events, source, created_at)
            VALUES ($1, $2, $3::jsonb, $4, now())
            ON CONFLICT (id) DO UPDATE
            SET events = traces.events || EXCLUDED.events::jsonb
            """,
            trace_id,
            workflow_id,
            json.dumps(events),
            source,
        )

    _log.info(
        "trigger: ingested %d failure event(s) as trace %s for workflow %s",
        len(failure_texts),
        trace_id,
        workflow_id,
    )
    return trace_id
