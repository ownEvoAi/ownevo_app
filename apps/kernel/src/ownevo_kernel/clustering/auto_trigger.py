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
tool failure for a bound workflow; a single background task clusters a
workflow only once it has been *quiet* for `debounce_seconds` (no further
signal), so a burst of wave-flushes collapses into one clustering run.

Scope / limits (single-tenant MVP):

  * State is in-process: a kernel restart drops pending signals. This is
    self-healing — the next ingest re-marks the workflow, and the
    on-demand button remains available. Nothing is lost permanently
    because clustering re-derives everything from the `traces` table.
  * One clustering run at a time (the loop processes due workflows
    sequentially) so two heavy pipelines never load embedder models
    concurrently.
  * If a signal arrives *while* a workflow is being clustered, the
    workflow is re-marked and picked up on a later cycle — so traces
    that landed mid-run are not missed.

The heavy clustering implementations are imported lazily inside the
default runner, so a kernel built without the `clustering` / `agent`
extras can still ingest traces: the first run raises `ImportError`, the
trigger logs once and disables itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

    from .persistence import PersistedCluster

_log = logging.getLogger(__name__)

# Wait this long after a workflow's last ingest signal before clustering,
# so a collector flushing one trace in waves coalesces into one run.
DEFAULT_DEBOUNCE_SECONDS = 30.0
# How often the background task wakes to check for due workflows. Kept well
# below the debounce so the effective delay is dominated by the debounce.
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

# (conn, workflow_id) -> persisted clusters. Injectable so tests can run the
# coalescer without the heavy pipeline or a real Anthropic key.
ClusterRunner = Callable[["asyncpg.Connection", str], Awaitable[list["PersistedCluster"]]]


async def _default_cluster_runner(
    conn: asyncpg.Connection, workflow_id: str
) -> list[PersistedCluster]:
    """Build the real (heavy) clustering impls and run one production pass.

    Imported lazily so the module import doesn't pull the `clustering` /
    `agent` extras on every API boot — only an actual run needs them.
    Raises `ImportError` when the extras are absent; the caller treats
    that as "disable auto-trigger".
    """
    from .default_impl import (
        AnthropicLabeler,
        HDBSCANClusterer,
        SentenceTransformerEmbedder,
        UMAPReducer,
    )
    from .from_traces import cluster_production_failures

    return await cluster_production_failures(
        conn,
        workflow_id,
        embedder=SentenceTransformerEmbedder(),
        reducer=UMAPReducer(),
        clusterer=HDBSCANClusterer(),
        labeler=AnthropicLabeler(),
    )


class ClusterAutoTrigger:
    """Coalesces ingest signals into debounced production-failure clustering.

    Lifecycle: `await start()` spawns the background task; the ingest
    route calls the synchronous `signal(workflow_id)`; `await stop()`
    halts the task (after the in-flight run, if any, finishes).
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        cluster_runner: ClusterRunner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pool = pool
        self._debounce = debounce_seconds
        self._poll_interval = poll_interval_seconds
        self._run = cluster_runner or _default_cluster_runner
        self._clock = clock
        # workflow_id -> monotonic timestamp of its most recent signal.
        self._pending: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        # Set once the extras prove absent — stops accepting further signals
        # so a misconfigured kernel doesn't log on every ingest.
        self._disabled = False

    def signal(self, workflow_id: str) -> None:
        """Mark a workflow as having new failures to (re)cluster.

        Synchronous and cheap — safe to call from the request path. The
        actual clustering happens later, on the background task, after the
        debounce window elapses with no further signal.
        """
        if self._disabled:
            return
        self._pending[workflow_id] = self._clock()

    async def start(self) -> None:
        """Spawn the background coalescing task (idempotent)."""
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="cluster-auto-trigger")

    async def stop(self, timeout: float = 60.0) -> None:
        """Signal the loop to stop and await it.

        Waits up to `timeout` seconds for any in-flight clustering run to
        finish so the DB write isn't torn in half. If the run is still going
        after the timeout (sentence-transformers + UMAP can take longer than
        uvicorn's default 5-second graceful-shutdown window), the task is
        cancelled and a warning is logged — the OS thread running the
        embedder/clusterer continues to completion in the background but
        stops blocking the process exit. Nothing is lost permanently: the
        next ingest re-signals the workflow and clustering re-derives from
        the traces table.
        """
        self._stopping.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
            except TimeoutError:
                _log.warning(
                    "cluster auto-trigger: stop() timed out after %.0fs "
                    "(clustering run still in flight; cancelling task)",
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
        """Cluster every workflow whose debounce window has elapsed."""
        now = self._clock()
        due = [wf for wf, ts in self._pending.items() if now - ts >= self._debounce]
        for workflow_id in due:
            if self._stopping.is_set():
                break
            # Pop before running so a signal arriving mid-run re-marks the
            # workflow with a fresh timestamp and gets picked up next cycle.
            self._pending.pop(workflow_id, None)
            await self._run_for(workflow_id)

    async def _run_for(self, workflow_id: str) -> None:
        try:
            async with self._pool.acquire() as conn:
                persisted = await self._run(conn, workflow_id)
        except ImportError:
            _log.warning(
                "cluster auto-trigger: clustering extras not installed; "
                "disabling auto-trigger (install the `clustering` + `agent` "
                "extras to enable). Use the on-demand endpoint meanwhile.",
            )
            self._disabled = True
            self._pending.clear()
            return
        except Exception:  # noqa: BLE001 — a background job must not crash the loop
            _log.exception(
                "cluster auto-trigger: clustering failed for workflow %s",
                workflow_id,
            )
            return
        if persisted:
            _log.info(
                "cluster auto-trigger: workflow %s produced %d cluster(s)",
                workflow_id,
                len(persisted),
            )
