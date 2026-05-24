"""CapturingSandbox — SandboxRuntime decorator that records every run.

Track 9.0.3. Wraps any concrete `SandboxRuntime` (LocalDockerSandbox in
practice) and persists each `.run()` result to the `captured_sandbox_runs`
table before returning. The downstream ReplaySimSandbox cursors through
those rows in `call_idx` order to reproduce the exact sequence of
Docker outputs from the captured iteration.

Usage pattern (benchmark runners — M5 / τ³):

    inner = LocalDockerSandbox(image="ownevo-sandbox-m5:0.1.0")
    sandbox = CapturingSandbox(
        inner=inner,
        pool=pool,
        workflow_id=workflow_id,
        iteration_id=iteration_id,
    )
    # Pass `sandbox` wherever the runner currently passes the bare
    # LocalDockerSandbox. Every .run() through it gets captured.

The decorator holds NO long-lived DB connection — each `.run()`
acquires from the pool, writes, releases. That mirrors how
iteration_runner's persistence phase works (one short-lived
connection per write rather than pinning for the whole sandboxed
iteration).

Failure mode: a capture write failure does NOT block the inner sandbox
result from returning. The inner result is the source of truth for the
caller; a missing capture row is recoverable (the operator can re-run
with capture re-enabled, or replay against an earlier captured run).
Errors are logged and counted on the decorator instance so a
capture-broke smoke test can assert clean runs at the end.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .types import SandboxResult

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


logger = logging.getLogger(__name__)


@dataclass
class CapturingSandbox:
    """Decorator that records every `.run()` to captured_sandbox_runs.

    Implements `SandboxRuntime` Protocol via `run(...)`. The inner
    sandbox does the real work; this class only adds the persist step.

    `_call_idx` is the per-instance monotonic counter that becomes the
    `call_idx` column on captured_sandbox_runs. Starting at 0 means a
    fresh CapturingSandbox per iteration aligns with ReplaySimSandbox's
    cursor — both walk 0, 1, 2, ... in order.

    `capture_failures` counts persist failures. Lets a smoketest assert
    `sandbox.capture_failures == 0` at the end of a clean run; production
    can ignore it (logger surfaces the warning per-failure).
    """

    inner: object  # SandboxRuntime — Protocol typing avoided to keep import surface tight
    pool: asyncpg.Pool
    workflow_id: str
    iteration_id: UUID
    _call_idx: int = 0
    capture_failures: int = field(default=0, init=False)

    async def run(
        self,
        code: str,
        *,
        timeout_seconds: float,
        memory_mb: int,
    ) -> SandboxResult:
        """Execute via the inner sandbox, then persist the result.

        Returns the inner result regardless of capture success — the
        caller gets exactly what LocalDockerSandbox would have returned,
        even if the capture row failed to write.
        """
        result = await self.inner.run(  # type: ignore[attr-defined]
            code,
            timeout_seconds=timeout_seconds,
            memory_mb=memory_mb,
        )
        call_idx = self._call_idx
        self._call_idx += 1

        try:
            await self._persist(call_idx, result)
        except Exception as exc:  # broad: any DB issue must not block the run
            self.capture_failures += 1
            logger.warning(
                "CapturingSandbox: capture write failed for "
                "workflow=%s iteration=%s call_idx=%d: %s",
                self.workflow_id, self.iteration_id, call_idx, exc,
            )

        return result

    async def _persist(self, call_idx: int, result: SandboxResult) -> None:
        payload = {
            "status": result.status,
            "output": result.output,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "error": result.error,
            "error_class": result.error_class,
        }
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO captured_sandbox_runs (
                    workflow_id, iteration_id, call_idx, result
                )
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (iteration_id, call_idx) DO UPDATE
                    SET result = EXCLUDED.result,
                        captured_at = now()
                """,
                self.workflow_id,
                self.iteration_id,
                call_idx,
                json.dumps(payload),
            )


__all__ = [
    "CapturingSandbox",
]
