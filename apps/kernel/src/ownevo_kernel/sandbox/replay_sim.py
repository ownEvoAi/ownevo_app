"""ReplaySimSandbox — replay captured Docker outputs through SandboxRuntime.

Track 9.0.3 Slice B. The benchmark-path counterpart to
`eval_runner/replay_solver.py`. When a benchmark workflow (M5 / τ³)
sets `workflows.sim_tier='replay'`, the code-execution sandbox swaps
from `LocalDockerSandbox.run(code, ...)` (real Docker) to
`ReplaySimSandbox.run(code, ...)` — which reads the captured rows
from `captured_sandbox_runs` for the source iteration and emits them
in `call_idx` order.

Use cases this unlocks:
  * Pre-production validation of a proposer-generated skill against
    the SAME captured tool responses as the last real iteration.
    Zero Docker invocations.
  * Bit-identical reproduction of a historical benchmark run on
    today's loop code — useful for bisecting regressions in the
    gate / clustering / metric layers.
  * Local development on a benchmark workflow without a Docker
    daemon (replay tier needs only the captured table).

How the capture happens: `CapturingSandbox` (a decorator around
`LocalDockerSandbox`) is wrapped around the real sandbox during real
iterations on benchmark workflows; it writes each `.run()` result to
`captured_sandbox_runs` before returning. ReplaySimSandbox does the
inverse — read rows back in `call_idx` order.

Fallback policy: enforced by the caller layer (whichever path
instantiates ReplaySimSandbox). This class raises
`ReplayRunMissingError` when the cursor outruns the captured set;
the caller catches and dispatches per `ReplaySimConfig.fallback`.

Stateful — the cursor advances per `.run()` call. Construct a fresh
instance per "iteration" of the benchmark loop so the cursor starts
at call_idx=0 every time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg

from .mock_sim import _coerce_response
from .types import SandboxResult


class ReplayRunMissingError(RuntimeError):
    """ReplaySimSandbox cursor outran the captured set.

    Carries the source_iteration_id + the call_idx that wasn't found
    so the caller can surface meaningful context (e.g. "the proposed
    skill made more Docker calls than the captured run did — replay
    can't validate beyond call_idx N").
    """

    def __init__(self, source_iteration_id: UUID, call_idx: int) -> None:
        super().__init__(
            f"Source iteration {source_iteration_id} has no captured "
            f"sandbox run at call_idx={call_idx}. Either the proposed "
            "code makes more sandbox calls than the captured run did, "
            "or replay_sim_config.source_iteration_id points at the "
            "wrong iteration.",
        )
        self.source_iteration_id = source_iteration_id
        self.call_idx = call_idx


@dataclass
class ReplaySimSandbox:
    """SandboxRuntime that replays captured Docker outputs from a
    prior iteration.

    Implements the `SandboxRuntime` Protocol. Construct via
    `await ReplaySimSandbox.load(conn, source_iteration_id=...)` to
    pre-fetch the captured set into memory; subsequent `.run()` calls
    are sync-fast (no DB roundtrip per invocation).
    """

    source_iteration_id: UUID
    captured: list[SandboxResult] = field(default_factory=list)
    _cursor: int = 0

    @classmethod
    async def load(
        cls,
        conn: asyncpg.Connection,
        *,
        source_iteration_id: UUID,
    ) -> ReplaySimSandbox:
        """Pre-fetch the captured set in `call_idx` order and build the
        sandbox.

        One query at construction time — each `.run()` call is then a
        pure-Python list lookup. For benchmark workloads that issue
        hundreds of sandbox calls per iteration, per-call DB roundtrips
        would add latency without the captured shape changing.
        """
        rows = await conn.fetch(
            """
            SELECT call_idx, result
            FROM captured_sandbox_runs
            WHERE iteration_id = $1
            ORDER BY call_idx ASC
            """,
            source_iteration_id,
        )
        results: list[SandboxResult] = []
        for r in rows:
            payload = _decode_jsonb(r["result"]) or {}
            results.append(_coerce_response(payload))
        return cls(
            source_iteration_id=source_iteration_id,
            captured=results,
        )

    async def run(
        self,
        code: str,
        *,
        timeout_seconds: float,
        memory_mb: int,
    ) -> SandboxResult:
        """SandboxRuntime Protocol entry point.

        Returns the next captured SandboxResult. `code` /
        `timeout_seconds` / `memory_mb` are accepted to match the
        Protocol but ignored — replay's contract is "do exactly what
        the prior iteration did," not "execute this code." The cursor
        advances per call.

        Raises:
            ReplayRunMissingError: cursor outran the captured set.
                Caller dispatches per the configured fallback policy.
        """
        del code, timeout_seconds, memory_mb  # Protocol parity, unused

        if self._cursor >= len(self.captured):
            raise ReplayRunMissingError(
                source_iteration_id=self.source_iteration_id,
                call_idx=self._cursor,
            )
        result = self.captured[self._cursor]
        self._cursor += 1
        return result


def _decode_jsonb(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        import json
        return json.loads(value)
    return value


__all__ = [
    "ReplayRunMissingError",
    "ReplaySimSandbox",
]
