"""Debounced auto-clustering of ingested production failures.

`cluster_production_failures` (see `from_traces.py`) is the on-demand
entry point behind `POST /api/workflows/{id}/cluster-production-failures`.
It is deliberately *not* run on every OTLP ingest: the pipeline is
CPU-heavy (sentence-transformers + UMAP + HDBSCAN + a synchronous LLM
label call), and a single external collector typically flushes one
trace's spans in several waves, so per-flush clustering would burn the
whole pipeline many times to converge on the same clusters.

This module bridges that gap with an in-process **debounced coalescer**.
The ingest route calls `signal(workflow_id)` whenever a batch lands a
tool failure for a bound workflow; a single background task waits until a
workflow has been *quiet* for `debounce_seconds` (no further signal), then
**enqueues a durable `run_clustering` job** on the `jobs` queue. The actual
heavy pipeline runs later in the `JobWorker` (via `action_run_clustering`),
so a kernel restart no longer drops an in-flight clustering run — the same
durability the trigger → iteration path already has.

Two layers of coalescing keep the pipeline from over-running:

  * The debounce collapses a collector's wave-flushes into one enqueue.
  * The queue's active-job unique index (workspace, kind, workflow) collapses
    enqueues across the debounce window: while a workflow's clustering job is
    still queued or running, further enqueues are no-ops.

If a signal arrives *while* a workflow's clustering is in flight, the enqueue
is a no-op (a job already exists); the next signal after that job completes
re-enqueues, so traces that landed mid-run are picked up on the following pass.
A kernel restart drops only the in-memory pending signals — self-healing, since
the next ingest re-signals and clustering re-derives everything from `traces`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

_log = logging.getLogger(__name__)

# Wait this long after a workflow's last ingest signal before enqueuing, so a
# collector flushing one trace in waves coalesces into one clustering job.
DEFAULT_DEBOUNCE_SECONDS = 30.0
# How often the background task wakes to check for due workflows. Kept well
# below the debounce so the effective delay is dominated by the debounce.
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

# (pool, workflow_id, workspace_id) -> job_id | None. Injectable so tests can
# drive the coalescer without a real queue; defaults to enqueuing a durable
# run_clustering job via the trigger action.
EnqueueFn = Callable[["asyncpg.Pool", str, str], Awaitable[Any]]


class ClusterAutoTrigger:
    """Coalesces ingest signals into debounced `run_clustering` job enqueues.

    Lifecycle: `await start()` spawns the background task; the ingest
    route calls the synchronous `signal(workflow_id)`; `await stop()`
    halts the task (after the in-flight enqueue, if any, finishes).
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        enqueue_fn: EnqueueFn | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pool = pool
        self._debounce = debounce_seconds
        self._poll_interval = poll_interval_seconds
        self._enqueue = enqueue_fn or _default_enqueue
        self._clock = clock
        # (workspace_id, workflow_id) -> monotonic timestamp of its most recent
        # signal. Keyed by workspace too so the enqueued job binds the same
        # workspace the ingest happened in (the queue row is RLS-scoped).
        self._pending: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def signal(self, workspace_id: str, workflow_id: str) -> None:
        """Mark a workflow as having new failures to (re)cluster.

        Synchronous and cheap — safe to call from the request path. The
        enqueue happens later, on the background task, after the debounce
        window elapses with no further signal. ``workspace_id`` is the
        workspace the ingest landed in; it is carried through so the enqueued
        job is scoped to the same workspace.
        """
        self._pending[(workspace_id, workflow_id)] = self._clock()

    async def start(self) -> None:
        """Spawn the background coalescing task (idempotent)."""
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="cluster-auto-trigger")

    async def stop(self, timeout: float = 10.0) -> None:
        """Signal the loop to stop and await it.

        Enqueues are fast (a single INSERT), so the in-flight drain finishes
        quickly; the timeout is a backstop. If the task is somehow still
        running after `timeout`, it is cancelled and a warning logged. Nothing
        is lost permanently: the next ingest re-signals the workflow.
        """
        self._stopping.set()
        if self._task is not None:
            # asyncio.wait avoids the asyncio.shield + asyncio.wait_for pattern,
            # which has a cancellation-propagation bug on Python 3.11 (bpo-45874):
            # wait_for cancels the shielded task immediately on timeout rather than
            # protecting it. asyncio.wait simply returns on timeout without
            # cancelling, so we stay in control of when (and whether) to cancel.
            done, _ = await asyncio.wait({self._task}, timeout=timeout)
            if not done:
                _log.warning(
                    "cluster auto-trigger: stop() timed out after %.0fs; "
                    "cancelling task",
                    timeout,
                )
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            # Sleep one poll interval, but wake immediately if asked to stop.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
            if self._stopping.is_set():
                break
            await self._drain_due()

    async def _drain_due(self) -> None:
        """Enqueue clustering for every workflow whose debounce window elapsed."""
        now = self._clock()
        due = [key for key, ts in self._pending.items() if now - ts >= self._debounce]
        for key in due:
            if self._stopping.is_set():
                break
            # Pop before enqueuing so a signal arriving mid-enqueue re-marks the
            # workflow with a fresh timestamp and gets picked up next cycle.
            self._pending.pop(key, None)
            workspace_id, workflow_id = key
            await self._run_for(workspace_id, workflow_id)

    async def _run_for(self, workspace_id: str, workflow_id: str) -> None:
        try:
            await self._enqueue(self._pool, workflow_id, workspace_id)
        except Exception:  # noqa: BLE001 — a background job must not crash the loop
            _log.exception(
                "cluster auto-trigger: failed to enqueue clustering for "
                "workflow %s",
                workflow_id,
            )


async def _default_enqueue(
    pool: asyncpg.Pool, workflow_id: str, workspace_id: str
) -> Any:
    """Enqueue a durable run_clustering job. Imported lazily to avoid a
    triggers ↔ clustering import cycle at module load."""
    from ..triggers.actions import action_enqueue_clustering

    return await action_enqueue_clustering(pool, workflow_id, workspace_id)
