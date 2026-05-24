"""Tests for `ReplaySimSandbox` — Track 9.0.3 Slice B.

What we pin:

  1. Protocol conformance — ReplaySimSandbox satisfies the
     `SandboxRuntime` runtime_checkable Protocol; isinstance() works.
  2. Cursor walks the captured list in order — sandbox.run() returns
     captured[0], captured[1], ... per call.
  3. Exhaustion raises ReplayRunMissingError — caller can apply
     fallback policy.
  4. ReplayRunMissingError carries the source_iteration_id and the
     specific call_idx that wasn't covered, so error messages are
     actionable.
  5. Run-args are accepted but ignored — code/timeout/memory don't
     change the output.

Tests use the in-memory constructor (passing `captured=[...]` directly)
to avoid the DB-gated `load()` classmethod path. The DB load is
covered by the integration smoketest.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from ownevo_kernel.sandbox import (
    ReplayRunMissingError,
    ReplaySimSandbox,
    SandboxResult,
    SandboxRuntime,
)


def _ok(output: str, *, duration_ms: int = 1) -> SandboxResult:
    return SandboxResult(
        status="ok",
        output=output,
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        error=None,
        error_class=None,
    )


def test_replay_sim_sandbox_satisfies_sandbox_runtime_protocol() -> None:
    sandbox = ReplaySimSandbox(source_iteration_id=uuid4())
    assert isinstance(sandbox, SandboxRuntime)


async def test_cursor_walks_captured_in_order() -> None:
    captured = [_ok("first"), _ok("second"), _ok("third")]
    sandbox = ReplaySimSandbox(source_iteration_id=uuid4(), captured=captured)
    outputs = []
    for _ in range(3):
        r = await sandbox.run(code="ignored", timeout_seconds=1, memory_mb=1)
        outputs.append(r.output)
    assert outputs == ["first", "second", "third"]


async def test_exhaustion_raises_with_actionable_context() -> None:
    iter_id = uuid4()
    sandbox = ReplaySimSandbox(
        source_iteration_id=iter_id,
        captured=[_ok("only")],
    )
    await sandbox.run(code="x", timeout_seconds=1, memory_mb=1)
    with pytest.raises(ReplayRunMissingError) as exc_info:
        await sandbox.run(code="x", timeout_seconds=1, memory_mb=1)
    assert exc_info.value.source_iteration_id == iter_id
    assert exc_info.value.call_idx == 1


async def test_empty_captured_raises_on_first_call() -> None:
    """Edge case: a workflow with zero captured runs (e.g. the source
    iteration didn't go through any sandbox calls). First .run()
    should still raise meaningfully rather than crash on index 0."""
    iter_id = uuid4()
    sandbox = ReplaySimSandbox(source_iteration_id=iter_id, captured=[])
    with pytest.raises(ReplayRunMissingError) as exc_info:
        await sandbox.run(code="x", timeout_seconds=1, memory_mb=1)
    assert exc_info.value.call_idx == 0


async def test_run_args_are_ignored() -> None:
    """Replay's contract is 'do exactly what was captured,' not 'execute
    this code.' Code/timeout/memory args must not change the output."""
    captured = [_ok("fixed-output")]
    sandbox = ReplaySimSandbox(source_iteration_id=uuid4(), captured=captured)
    r = await sandbox.run(
        code="raise RuntimeError('boom')",
        timeout_seconds=3600,
        memory_mb=8192,
    )
    assert r.output == "fixed-output"
    assert r.status == "ok"


async def test_replays_error_results_byte_for_byte() -> None:
    """Captured errors must replay as errors — replay should not
    sanitize. A workflow that failed via Timeout on iteration N must
    show up as Timeout on the replay."""
    captured = [
        SandboxResult(
            status="error",
            output="",
            stderr="killed",
            exit_code=137,
            duration_ms=60000,
            error="wall-clock exceeded",
            error_class="Timeout",
        ),
    ]
    sandbox = ReplaySimSandbox(source_iteration_id=uuid4(), captured=captured)
    r = await sandbox.run(code="x", timeout_seconds=1, memory_mb=1)
    assert r.status == "error"
    assert r.error_class == "Timeout"
    assert r.error == "wall-clock exceeded"
    assert r.exit_code == 137
