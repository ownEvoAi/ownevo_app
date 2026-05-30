"""Background worker that drains the durable job queue (`jobs` table).

`JobWorker` is a long-lived background task whose lifecycle mirrors
`TriggerScheduler` / `ClusterAutoTrigger`:

* ``await start()`` — spawns the poll loop.
* ``await stop()`` — lets the in-flight job finish, then halts the loop.

Each poll, for every active workspace, the worker re-queues stale jobs (whose
worker died mid-run) and then claims at most one ready job. A claimed job is
run with a concurrent heartbeat so a long run is not mistaken for a dead one;
on success it is completed with its result, on failure it is retried with
backoff until `max_attempts` is exhausted.

The queue is workspace-scoped under RLS, so — like the orphan reaper — the
worker enumerates the global `workspaces` index and binds each workspace's
GUC via `acquire_workspace_conn` before touching the `jobs` table.

Why the work is not held on one connection: a `run_iteration` job drives a
30-90s LLM cycle. The worker holds no DB connection across that window; the
iteration runner manages its own short-lived connections, and the heartbeat
acquires a fresh connection per ping.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

from ..tenant_session import acquire_workspace_conn
from .queue import (
    claim_next_job,
    complete_job,
    fail_job,
    heartbeat_job,
    requeue_stale_jobs,
)

_log = logging.getLogger(__name__)


def _decode_payload(raw: str | dict[str, Any] | Any) -> dict[str, Any]:
    """Decode the asyncpg ``payload`` column to a Python dict.

    asyncpg returns ``jsonb`` as a plain string unless a custom codec is
    registered.  This guard handles both the raw-string and the already-decoded
    dict cases so callers don't have to repeat the isinstance check.
    """
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    return {}


# How often the worker wakes to re-queue stale jobs and claim ready work.
_POLL_INTERVAL_SECONDS = 3.0
# How often a running job's heartbeat is advanced.
_HEARTBEAT_INTERVAL_SECONDS = 15.0
# A running job whose heartbeat is older than this is treated as abandoned and
# re-queued. Must comfortably exceed the heartbeat interval so a live worker's
# job is never stolen mid-run.
_STALE_AFTER_SECONDS = 90.0

# Retry backoff: base * 2^(attempt-1), capped. Iterations are minutes long, so
# a failed attempt waits before re-running rather than hammering the LLM.
_RETRY_BACKOFF_BASE_SECONDS = 30.0
_RETRY_BACKOFF_CAP_SECONDS = 300.0

# A handler runs one claimed job and returns its result payload (stored on the
# job row) or raises to mark the attempt failed.
JobHandler = Callable[["asyncpg.Record", str], Awaitable[dict[str, Any] | None]]


class JobWorker:
    """Drains the `jobs` table. Lifecycle: `start()` → run → `stop()`."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        heartbeat_interval: float = _HEARTBEAT_INTERVAL_SECONDS,
        stale_after_seconds: float = _STALE_AFTER_SECONDS,
        instance_id: str | None = None,
    ) -> None:
        self._pool = pool
        self._poll_interval = poll_interval
        self._heartbeat_interval = heartbeat_interval
        self._stale_after = stale_after_seconds
        self._instance_id = instance_id or f"job-worker-{uuid.uuid4().hex[:8]}"

        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

        # kind -> handler. A new job kind adds a value to the job_kind enum
        # (migration) and a handler here. Tests substitute handlers to drive
        # the claim/heartbeat/complete machinery without a real LLM.
        self._dispatch: dict[str, JobHandler] = {
            "run_iteration": self._run_iteration,
            "run_clustering": self._run_clustering,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="job-worker")
        _log.info("job worker: started (instance %s)", self._instance_id)

    async def stop(self, timeout: float = 120.0) -> None:
        self._stopping.set()
        if self._task is not None:
            done, _ = await asyncio.wait({self._task}, timeout=timeout)
            if not done:
                _log.warning(
                    "job worker: stop() timed out after %.0fs; cancelling",
                    timeout,
                )
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task
            self._task = None
        _log.info("job worker: stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._poll_interval
                )
            if self._stopping.is_set():
                break
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
                _log.exception("job worker: unhandled error in poll tick")

    async def _tick(self) -> None:
        for workspace_id in await self._list_active_workspaces():
            # Don't start claiming new work once a stop has been requested;
            # the in-flight job (if any) is already past this point.
            if self._stopping.is_set():
                break
            try:
                await self._poll_workspace(workspace_id)
            except Exception:  # noqa: BLE001 — one poisoned tenant must not stall others
                _log.exception(
                    "job worker: poll failed for workspace %s; continuing",
                    workspace_id,
                )

    async def _poll_workspace(self, workspace_id: str) -> None:
        """Re-queue stale jobs, then claim and run at most one ready job.

        The connection is released before the (potentially long) job runs —
        the claim is brief, the run holds no pooled connection.
        """
        async with acquire_workspace_conn(self._pool, workspace_id) as conn:
            await requeue_stale_jobs(conn, stale_after_seconds=self._stale_after)
            job = await claim_next_job(conn, claimed_by=self._instance_id)
        if job is None:
            return
        await self._execute(job, workspace_id)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute(self, job: asyncpg.Record, workspace_id: str) -> None:
        job_id = job["id"]
        kind = job["kind"]
        handler = self._dispatch.get(kind)
        if handler is None:
            _log.error("job worker: no handler for kind %r (job %s)", kind, job_id)
            await self._mark_failed(
                workspace_id, job, error=f"no handler for kind {kind!r}"
            )
            return

        heartbeat = asyncio.create_task(
            self._heartbeat_loop(workspace_id, job_id),
            name=f"job-heartbeat-{str(job_id)[:8]}",
        )
        try:
            result = await handler(job, workspace_id)
        except Exception as exc:  # noqa: BLE001 — surface as a failed attempt
            _log.exception("job worker: job %s (kind %s) failed", job_id, kind)
            await self._cancel(heartbeat)
            await self._mark_failed(workspace_id, job, error=repr(exc))
            return
        await self._cancel(heartbeat)
        async with acquire_workspace_conn(self._pool, workspace_id) as conn:
            await complete_job(conn, job_id, result=result)
        _log.info("job worker: job %s (kind %s) succeeded", job_id, kind)

    async def _mark_failed(
        self, workspace_id: str, job: asyncpg.Record, *, error: str
    ) -> None:
        # `attempts` was incremented at claim, so the backoff for the next try
        # is derived from the count of attempts already made.
        backoff = min(
            _RETRY_BACKOFF_BASE_SECONDS * (2 ** max(0, job["attempts"] - 1)),
            _RETRY_BACKOFF_CAP_SECONDS,
        )
        async with acquire_workspace_conn(self._pool, workspace_id) as conn:
            retried = await fail_job(
                conn, job["id"], error=error, backoff_seconds=backoff
            )
        # Structured fields so the JSON log formatter / Sentry ship them as
        # queryable attributes — a log-based alert keys on `job_failed_terminal`
        # rather than regex-matching the message. (The Prometheus signal is the
        # ownevo_jobs{status="failed"} gauge.)
        payload = _decode_payload(job["payload"])
        workflow_id = payload.get("workflow_id")
        fields = {
            "job_id": str(job["id"]),
            "kind": job["kind"],
            "workflow_id": workflow_id,
            "attempts": job["attempts"],
            "max_attempts": job["max_attempts"],
            "last_error": error,
        }
        if retried:
            _log.warning(
                "job worker: job %s re-queued (attempt %d/%d, backoff %.0fs)",
                job["id"], job["attempts"], job["max_attempts"], backoff,
                extra={**fields, "backoff_seconds": backoff},
            )
        else:
            _log.error(
                "job worker: job %s failed terminally after %d attempt(s)",
                job["id"], job["attempts"],
                extra={**fields, "job_failed_terminal": True},
            )

    async def _heartbeat_loop(self, workspace_id: str, job_id: uuid.UUID) -> None:
        """Advance the job's heartbeat until cancelled by `_execute`."""
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                async with acquire_workspace_conn(self._pool, workspace_id) as conn:
                    await heartbeat_job(conn, job_id)
            except Exception:  # noqa: BLE001 — a transient ping failure is not fatal
                _log.warning(
                    "job worker: heartbeat ping failed for job %s", job_id,
                )

    @staticmethod
    async def _cancel(task: asyncio.Task[None]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _list_active_workspaces(self) -> list[str]:
        """Non-deleted workspace ids — the global tenancy index is not RLS'd."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM workspaces WHERE deleted_at IS NULL"
            )
        return [row["id"] for row in rows]

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _run_iteration(
        self, job: asyncpg.Record, workspace_id: str
    ) -> dict[str, Any]:
        """Run one improvement-loop iteration for the job's workflow.

        Imported lazily so the worker module stays importable without the
        heavy ``agent`` extra (the no-DB unit test job must not require it).
        """
        from ..api._anthropic_client import build_async_anthropic
        from ..iteration_runner import run_one_iteration_for_workflow

        payload = _decode_payload(job["payload"])
        workflow_id = payload["workflow_id"]

        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; cannot run iteration"
            )

        client = build_async_anthropic(api_key)
        try:
            outcome = await run_one_iteration_for_workflow(
                self._pool,
                workflow_id=workflow_id,
                workspace_id=workspace_id,
                client=client,
            )
        finally:
            await client.close()

        return {
            "iteration_id": str(outcome.iteration_id),
            "iteration_index": outcome.iteration_index,
            "state": outcome.state,
        }

    async def _run_clustering(
        self, job: asyncpg.Record, workspace_id: str
    ) -> dict[str, Any]:
        """Run one production-failure clustering pass for the job's workflow.

        Delegates to ``action_run_clustering`` (the executor), imported lazily
        so the worker module stays importable without the heavy
        ``clustering`` / ``agent`` extras. If the extras are absent the
        executor raises ``ImportError``; we complete the job as a no-op rather
        than fail it, because a missing-extra condition is not fixable by retry
        and should not burn the retry budget.
        """
        from ..triggers.actions import action_run_clustering

        payload = _decode_payload(job["payload"])
        workflow_id = payload["workflow_id"]

        try:
            n = await action_run_clustering(self._pool, workflow_id, workspace_id)
        except ImportError:
            _log.warning(
                "job worker: clustering extras not installed; completing "
                "run_clustering job %s as a no-op (install the `clustering` + "
                "`agent` extras to enable)",
                job["id"],
            )
            return {"skipped": "clustering_extras_absent", "clusters": 0}

        return {"clusters": n}


__all__ = ["JobWorker", "JobHandler"]
