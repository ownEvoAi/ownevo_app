"""Concurrency-cap tests for LocalDockerSandbox.

These tests exercise the admission semaphore without invoking Docker. They
subclass LocalDockerSandbox and override ``_run_inside_slot`` so the slot
release timing is what the semaphore sees, while no real ``docker run`` is
spawned.
"""

from __future__ import annotations

import asyncio

import pytest
from ownevo_kernel.sandbox import SandboxResult
from ownevo_kernel.sandbox.local_docker import (
    _DEFAULT_MAX_CONCURRENT,
    _MAX_CONCURRENT_ENV,
    LocalDockerSandbox,
    _read_max_concurrent,
    reset_admission_for_tests,
)


def _ok_result() -> SandboxResult:
    return SandboxResult(
        status="ok",
        output="",
        stderr="",
        exit_code=0,
        duration_ms=0,
        error=None,
        error_class=None,
    )


class _CountingSandbox(LocalDockerSandbox):
    """Replaces the docker-driving body with an awaitable we control."""

    def __init__(self) -> None:
        super().__init__(network="none")
        self.in_flight = 0
        self.peak_in_flight = 0
        self.release_event = asyncio.Event()

    async def _run_inside_slot(  # type: ignore[override]
        self,
        code: str,
        *,
        timeout_seconds: float,
        memory_mb: int,
        validated_extras,
        extra_env,
    ) -> SandboxResult:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await self.release_event.wait()
        finally:
            self.in_flight -= 1
        return _ok_result()


@pytest.fixture(autouse=True)
def _reset_admission(monkeypatch):
    """Each test gets a fresh semaphore so capacity changes take effect and
    semaphores don't leak across tests' event loops."""
    reset_admission_for_tests()
    yield
    reset_admission_for_tests()
    monkeypatch.delenv(_MAX_CONCURRENT_ENV, raising=False)


def test_read_max_concurrent_default(monkeypatch):
    monkeypatch.delenv(_MAX_CONCURRENT_ENV, raising=False)
    assert _read_max_concurrent() == _DEFAULT_MAX_CONCURRENT


def test_read_max_concurrent_env_override(monkeypatch):
    monkeypatch.setenv(_MAX_CONCURRENT_ENV, "9")
    assert _read_max_concurrent() == 9


def test_read_max_concurrent_rejects_zero(monkeypatch):
    monkeypatch.setenv(_MAX_CONCURRENT_ENV, "0")
    with pytest.raises(ValueError, match=_MAX_CONCURRENT_ENV):
        _read_max_concurrent()


def test_read_max_concurrent_rejects_negative(monkeypatch):
    monkeypatch.setenv(_MAX_CONCURRENT_ENV, "-3")
    with pytest.raises(ValueError, match=_MAX_CONCURRENT_ENV):
        _read_max_concurrent()


def test_read_max_concurrent_rejects_garbage(monkeypatch):
    monkeypatch.setenv(_MAX_CONCURRENT_ENV, "not-a-number")
    with pytest.raises(ValueError, match=_MAX_CONCURRENT_ENV):
        _read_max_concurrent()


@pytest.mark.asyncio
async def test_admission_semaphore_caps_concurrent_runs(monkeypatch):
    """Fanning out N > cap calls must keep peak in-flight at the cap."""
    monkeypatch.setenv(_MAX_CONCURRENT_ENV, "2")
    sandbox = _CountingSandbox()

    async def one_call() -> SandboxResult:
        return await sandbox.run("ignored", timeout_seconds=1.0, memory_mb=64)

    tasks = [asyncio.create_task(one_call()) for _ in range(8)]
    # Let the scheduler give every coroutine a chance to try to acquire.
    for _ in range(10):
        await asyncio.sleep(0)

    assert sandbox.peak_in_flight == 2, (
        f"Expected peak to be capped at 2, got {sandbox.peak_in_flight}"
    )
    assert sandbox.in_flight == 2

    sandbox.release_event.set()
    results = await asyncio.gather(*tasks)
    assert len(results) == 8
    assert all(r.status == "ok" for r in results)
    assert sandbox.in_flight == 0


@pytest.mark.asyncio
async def test_admission_semaphore_releases_on_exception(monkeypatch):
    """A run that raises must still release its slot so later runs proceed."""
    monkeypatch.setenv(_MAX_CONCURRENT_ENV, "1")

    class _RaisingSandbox(LocalDockerSandbox):
        def __init__(self) -> None:
            super().__init__(network="none")
            self.calls = 0

        async def _run_inside_slot(  # type: ignore[override]
            self,
            code: str,
            *,
            timeout_seconds: float,
            memory_mb: int,
            validated_extras,
            extra_env,
        ) -> SandboxResult:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated failure")
            return _ok_result()

    sandbox = _RaisingSandbox()
    with pytest.raises(RuntimeError, match="simulated failure"):
        await sandbox.run("ignored", timeout_seconds=1.0, memory_mb=64)

    # If the slot leaked, this second call would block forever; the test
    # times out instead of completing in <1s.
    result = await asyncio.wait_for(
        sandbox.run("ignored", timeout_seconds=1.0, memory_mb=64),
        timeout=1.0,
    )
    assert result.status == "ok"
    assert sandbox.calls == 2


@pytest.mark.asyncio
async def test_admission_semaphore_is_shared_across_instances(monkeypatch):
    """The cap is process-global, not per-instance."""
    monkeypatch.setenv(_MAX_CONCURRENT_ENV, "1")

    sandbox_a = _CountingSandbox()
    sandbox_b = _CountingSandbox()

    async def one_call(sb: _CountingSandbox) -> SandboxResult:
        return await sb.run("ignored", timeout_seconds=1.0, memory_mb=64)

    t1 = asyncio.create_task(one_call(sandbox_a))
    t2 = asyncio.create_task(one_call(sandbox_b))
    for _ in range(5):
        await asyncio.sleep(0)

    # With cap=1, only one of the two instances can be in flight at a time.
    in_flight_total = sandbox_a.in_flight + sandbox_b.in_flight
    assert in_flight_total == 1, (
        f"Cross-instance cap broken; saw {in_flight_total} in flight"
    )

    sandbox_a.release_event.set()
    sandbox_b.release_event.set()
    await asyncio.gather(t1, t2)
