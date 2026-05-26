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
manage their own transaction scope.  Every action also accepts a
``workspace_id`` so that the connection it acquires is bound to the
workspace before any workspace-scoped table is touched.  Under RLS an
unbound connection sees no rows and cannot insert into workspace-scoped
tables — routing through ``acquire_workspace_conn`` is the only correct
path for background workers.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from ..tenant_session import acquire_workspace_conn

_log = logging.getLogger(__name__)


async def action_run_clustering(
    pool: asyncpg.Pool,
    workflow_id: str,
    workspace_id: str,
) -> int:
    """Run the production-failure clustering pipeline for `workflow_id`.

    Returns the number of clusters persisted.  Imports the heavy
    clustering extras lazily — the caller can catch `ImportError` and
    log a meaningful message when the extras are absent.

    ``workspace_id`` must match the workspace the workflow belongs to so
    the connection is properly scoped under RLS before any workspace-scoped
    table (``traces``, ``failure_clusters``) is read or written.
    """
    from ..clustering.default_impl import (
        AnthropicLabeler,
        HDBSCANClusterer,
        SentenceTransformerEmbedder,
        UMAPReducer,
    )
    from ..clustering.from_traces import cluster_production_failures

    async with acquire_workspace_conn(pool, workspace_id) as conn:
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
    workspace_id: str,
) -> None:
    """Start one improvement-loop iteration for `workflow_id`.

    Spawns `run_one_iteration_for_workflow` as a fire-and-forget background
    task so the trigger dispatcher returns promptly.  The iteration runner
    creates the ``iterations`` row (in ``running`` state), drives the full
    LLM + regression-gate cycle, and persists the outcome — all using
    ``workspace_id``-scoped connections under RLS.

    The iteration runner is imported lazily to keep the trigger module
    free of the heavy ``agent`` extra.

    ``workspace_id`` must match the workspace the workflow belongs to so
    every workspace-scoped write the runner makes is correctly scoped.
    """
    import os

    from ..api._anthropic_client import build_async_anthropic
    from ..iteration_runner import run_one_iteration_for_workflow

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        _log.error(
            "trigger: ANTHROPIC_API_KEY is not set; "
            "cannot start iteration for workflow %s",
            workflow_id,
        )
        return

    client = build_async_anthropic(api_key)

    async def _run_bg() -> None:
        try:
            await run_one_iteration_for_workflow(
                pool,
                workflow_id=workflow_id,
                workspace_id=workspace_id,
                client=client,
            )
        except Exception:  # noqa: BLE001
            _log.exception(
                "trigger: iteration runner failed for workflow %s",
                workflow_id,
            )
        finally:
            await client.close()

    asyncio.create_task(
        _run_bg(),
        name=f"trigger-iteration-{workflow_id[:8]}",
    )
    _log.info(
        "trigger: spawned iteration background task for workflow %s",
        workflow_id,
    )


async def action_ingest_failures(
    pool: asyncpg.Pool,
    workflow_id: str,
    failure_texts: list[str],
    workspace_id: str,
    source: str = "trigger",
) -> str | None:
    """Persist `failure_texts` as a synthetic trace bound to `workflow_id`.

    Each string in `failure_texts` becomes one
    ``ToolCallResultEvent(status="error")`` in the trace.  The trace is
    tagged with ``source`` so the UI can distinguish synthetic traces from
    real production traces.

    ``workspace_id`` must match the workspace the workflow belongs to so
    the ``traces`` insert is correctly scoped under RLS.

    Returns the new ``trace_id``, or ``None`` when ``failure_texts`` is
    empty.
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

    async with acquire_workspace_conn(pool, workspace_id) as conn:
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
