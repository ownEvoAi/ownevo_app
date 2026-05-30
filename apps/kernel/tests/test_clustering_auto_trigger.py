"""Tests for the debounced production-failure auto-clustering trigger.

No DB and no clustering deps: the auto-trigger now *enqueues* a durable
`run_clustering` job rather than running the pipeline inline, so the enqueue
is injected as a fake `enqueue_fn`, and a hand-driven `clock` makes the
debounce deterministic without real sleeps. The few timing-sensitive
assertions (start/stop) use a tiny poll interval.
"""

from __future__ import annotations

import asyncio

from ownevo_kernel.clustering.auto_trigger import ClusterAutoTrigger

# Sentinel pool — the injected enqueue_fn ignores it, so it is never touched.
_POOL = object()


def _trigger(*, debounce: float, enqueue, clock):  # noqa: ANN001, ANN202
    return ClusterAutoTrigger(
        _POOL,  # type: ignore[arg-type]
        debounce_seconds=debounce,
        poll_interval_seconds=0.01,
        enqueue_fn=enqueue,
        clock=clock,
    )


# --- debounce / coalescing (drive _drain_due directly) ---------------------


async def test_debounce_coalesces_a_burst_into_one_enqueue() -> None:
    now = [0.0]
    calls: list[tuple[str, str]] = []

    async def enqueue(pool, workflow_id, workspace_id):  # noqa: ANN001, ANN202
        calls.append((workspace_id, workflow_id))
        return "job-1"

    trig = _trigger(debounce=30.0, enqueue=enqueue, clock=lambda: now[0])

    # A burst of signals (wave-flushes) keeps resetting the debounce timer.
    trig.signal("default", "wf1")
    now[0] = 10.0
    trig.signal("default", "wf1")

    # 10s after the last signal — not yet quiet for the full window.
    now[0] = 20.0
    await trig._drain_due()
    assert calls == []

    # 35s after the last signal — now it enqueues, exactly once for the burst.
    now[0] = 45.0
    await trig._drain_due()
    assert calls == [("default", "wf1")]

    # Already cleared — a later drain with no new signal does nothing.
    now[0] = 200.0
    await trig._drain_due()
    assert calls == [("default", "wf1")]


async def test_signal_during_enqueue_requeues_for_next_cycle() -> None:
    now = [0.0]
    calls: list[str] = []
    trig_holder: list[ClusterAutoTrigger] = []

    async def enqueue(pool, workflow_id, workspace_id):  # noqa: ANN001, ANN202
        calls.append(workflow_id)
        if len(calls) == 1:
            # A trace lands mid-enqueue; it must not be lost.
            trig_holder[0].signal("default", workflow_id)
        return "job"

    trig = _trigger(debounce=30.0, enqueue=enqueue, clock=lambda: now[0])
    trig_holder.append(trig)

    trig.signal("default", "wf")
    now[0] = 100.0
    await trig._drain_due()  # due → enqueues, re-signals at t=100
    assert calls == ["wf"]

    now[0] = 120.0
    await trig._drain_due()  # 20s since re-signal — not due
    assert calls == ["wf"]

    now[0] = 140.0
    await trig._drain_due()  # 40s since re-signal — due again
    assert calls == ["wf", "wf"]


async def test_multiple_workflows_each_enqueue_once() -> None:
    now = [0.0]
    calls: list[str] = []

    async def enqueue(pool, workflow_id, workspace_id):  # noqa: ANN001, ANN202
        calls.append(workflow_id)
        return "job"

    trig = _trigger(debounce=10.0, enqueue=enqueue, clock=lambda: now[0])
    trig.signal("default", "a")
    trig.signal("default", "b")
    now[0] = 15.0
    await trig._drain_due()
    assert sorted(calls) == ["a", "b"]


async def test_workspace_is_carried_through_to_enqueue() -> None:
    """The enqueue is scoped to the workspace the ingest signal landed in."""
    calls: list[tuple[str, str]] = []

    async def enqueue(pool, workflow_id, workspace_id):  # noqa: ANN001, ANN202
        calls.append((workspace_id, workflow_id))
        return "job"

    trig = _trigger(debounce=0.0, enqueue=enqueue, clock=lambda: 0.0)
    trig.signal("ws-a", "wf")
    await trig._drain_due()
    assert calls == [("ws-a", "wf")]


# --- failure handling ------------------------------------------------------


async def test_enqueue_exception_is_swallowed_and_workflow_dropped() -> None:
    async def enqueue(pool, workflow_id, workspace_id):  # noqa: ANN001, ANN202
        raise ValueError("transient enqueue failure")

    trig = _trigger(debounce=0.0, enqueue=enqueue, clock=lambda: 0.0)
    trig.signal("default", "wf")
    # Must not propagate — a background job can't take down the event loop.
    await trig._drain_due()
    assert trig._pending == {}


# --- start / stop lifecycle ------------------------------------------------


async def test_start_processes_signals_and_stop_halts_the_task() -> None:
    now = [0.0]
    calls: list[str] = []

    async def enqueue(pool, workflow_id, workspace_id):  # noqa: ANN001, ANN202
        calls.append(workflow_id)
        return "job"

    trig = _trigger(debounce=0.0, enqueue=enqueue, clock=lambda: now[0])
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
    async def enqueue(pool, workflow_id, workspace_id):  # noqa: ANN001, ANN202
        return "job"

    trig = _trigger(debounce=0.0, enqueue=enqueue, clock=lambda: 0.0)
    await trig.stop()  # no task — must not raise
    assert trig._task is None
