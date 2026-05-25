"""Tests for the debounced production-failure auto-clustering trigger.

No DB and no clustering deps: the clustering run is injected as a fake
`cluster_runner`, and a hand-driven `clock` makes the debounce
deterministic without real sleeps. The few timing-sensitive assertions
(start/stop) use a tiny poll interval.
"""

from __future__ import annotations

import asyncio

from ownevo_kernel.clustering.auto_trigger import ClusterAutoTrigger


class _FakeAcquire:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, *_: object) -> bool:
        return False


class _FakeConn:
    """Sentinel conn that satisfies set_workspace (workspace lookup + GUC set).

    `acquire_workspace_conn` binds the workspace before yielding, so the conn
    the runner receives must answer the `workspaces` lookup (live row) and the
    set_config call. Records bound workspaces so a test can assert the run was
    scoped correctly.
    """

    def __init__(self) -> None:
        self.bound_workspaces: list[str] = []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object]:
        return {"deleted_at": None}

    async def execute(self, sql: str, *args: object) -> str:
        if "set_config" in sql:
            self.bound_workspaces.append(args[1])  # type: ignore[arg-type]
        return "SELECT 1"


class _FakePool:
    """Stands in for an asyncpg pool — `acquire()` yields a sentinel conn."""

    def __init__(self) -> None:
        self.conn = _FakeConn()

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


def _trigger(*, debounce: float, runner, clock):  # noqa: ANN001, ANN202
    return ClusterAutoTrigger(
        _FakePool(),
        debounce_seconds=debounce,
        poll_interval_seconds=0.01,
        cluster_runner=runner,
        clock=clock,
    )


# --- debounce / coalescing (drive _drain_due directly) ---------------------


async def test_debounce_coalesces_a_burst_into_one_run() -> None:
    now = [0.0]
    calls: list[str] = []

    async def runner(conn, workflow_id):  # noqa: ANN001, ANN202
        calls.append(workflow_id)
        return []

    trig = _trigger(debounce=30.0, runner=runner, clock=lambda: now[0])

    # A burst of signals (wave-flushes) keeps resetting the debounce timer.
    trig.signal("default", "wf1")
    now[0] = 10.0
    trig.signal("default", "wf1")

    # 10s after the last signal — not yet quiet for the full window.
    now[0] = 20.0
    await trig._drain_due()
    assert calls == []

    # 35s after the last signal — now it runs, exactly once for the burst.
    now[0] = 45.0
    await trig._drain_due()
    assert calls == ["wf1"]

    # Already cleared — a later drain with no new signal does nothing.
    now[0] = 200.0
    await trig._drain_due()
    assert calls == ["wf1"]


async def test_signal_during_run_requeues_for_next_cycle() -> None:
    now = [0.0]
    calls: list[str] = []
    trig_holder: list[ClusterAutoTrigger] = []

    async def runner(conn, workflow_id):  # noqa: ANN001, ANN202
        calls.append(workflow_id)
        if len(calls) == 1:
            # A trace lands mid-run; it must not be lost.
            trig_holder[0].signal("default", workflow_id)
        return []

    trig = _trigger(debounce=30.0, runner=runner, clock=lambda: now[0])
    trig_holder.append(trig)

    trig.signal("default", "wf")
    now[0] = 100.0
    await trig._drain_due()  # due → runs, re-signals at t=100
    assert calls == ["wf"]

    now[0] = 120.0
    await trig._drain_due()  # 20s since re-signal — not due
    assert calls == ["wf"]

    now[0] = 140.0
    await trig._drain_due()  # 40s since re-signal — due again
    assert calls == ["wf", "wf"]


async def test_multiple_workflows_each_run_once() -> None:
    now = [0.0]
    calls: list[str] = []

    async def runner(conn, workflow_id):  # noqa: ANN001, ANN202
        calls.append(workflow_id)
        return []

    trig = _trigger(debounce=10.0, runner=runner, clock=lambda: now[0])
    trig.signal("default", "a")
    trig.signal("default", "b")
    now[0] = 15.0
    await trig._drain_due()
    assert sorted(calls) == ["a", "b"]


# --- failure handling ------------------------------------------------------


async def test_importerror_disables_the_trigger() -> None:
    async def runner(conn, workflow_id):  # noqa: ANN001, ANN202
        raise ImportError("clustering extras not installed")

    trig = _trigger(debounce=0.0, runner=runner, clock=lambda: 0.0)
    trig.signal("default", "wf")
    await trig._drain_due()

    assert trig._disabled is True
    # Further signals are ignored — no per-ingest log spam.
    trig.signal("default", "wf2")
    assert trig._pending == {}


async def test_runner_exception_is_swallowed_and_workflow_dropped() -> None:
    async def runner(conn, workflow_id):  # noqa: ANN001, ANN202
        raise ValueError("transient clustering failure")

    trig = _trigger(debounce=0.0, runner=runner, clock=lambda: 0.0)
    trig.signal("default", "wf")
    # Must not propagate — a background job can't take down the event loop.
    await trig._drain_due()
    assert trig._pending == {}
    assert trig._disabled is False


# --- start / stop lifecycle ------------------------------------------------


async def test_start_processes_signals_and_stop_halts_the_task() -> None:
    now = [0.0]
    calls: list[str] = []

    async def runner(conn, workflow_id):  # noqa: ANN001, ANN202
        calls.append(workflow_id)
        return []

    trig = _trigger(debounce=0.0, runner=runner, clock=lambda: now[0])
    await trig.start()
    trig.signal("default", "wf")

    # Give the loop a few poll cycles to pick up the (immediately-due) signal.
    for _ in range(50):
        if calls:
            break
        await asyncio.sleep(0.01)

    await trig.stop()
    assert calls == ["wf"]
    assert trig._task is None


async def test_stop_is_safe_without_start() -> None:
    trig = _trigger(debounce=0.0, runner=None, clock=lambda: 0.0)
    await trig.stop()  # no task — must not raise
    assert trig._task is None


async def test_signal_is_noop_when_disabled() -> None:
    trig = _trigger(debounce=0.0, runner=None, clock=lambda: 0.0)
    trig._disabled = True
    trig.signal("default", "wf")
    assert trig._pending == {}
