"""Optional graceful-shutdown drain for the kernel API.

Behind a load balancer a plain SIGTERM is racy: uvicorn closes its listening
socket as soon as the signal arrives, so a request the balancer routed in the
last moment — before its own health check noticed the instance going away —
hits a closed socket and fails instead of completing.

When ``OWNEVO_SHUTDOWN_DRAIN_SECONDS`` is a positive number, ``install_drain``
inserts a drain window between the signal and uvicorn's shutdown:

  1. SIGTERM/SIGINT arrives -> mark the app as shutting down, so ``/readyz``
     starts returning 503 and the load balancer pulls the instance out of
     rotation. Liveness (``/livez``) stays 200 — the process is fine, it is
     just declining new traffic.
  2. Keep serving for the drain window so in-flight and just-routed requests
     finish against a still-open socket.
  3. Re-deliver the signal to uvicorn's own handler, which then runs its
     normal graceful shutdown (stop accepting, await in-flight, run the
     lifespan teardown that closes the pool).

Default 0 -> disabled: no signal handler is installed and uvicorn's behaviour
(and a local Ctrl-C) is left completely untouched. This is strictly an opt-in
for deployments that sit behind a draining load balancer; nothing changes for
``make api`` or the test suite.

The handler is chained, not replaced: uvicorn (>=0.46) installs its handlers
with ``signal.signal`` in the main thread, so ``signal.getsignal`` returns its
``handle_exit`` at lifespan-startup time and we re-deliver to it after the
drain rather than reimplementing shutdown ourselves.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import threading

from fastapi import FastAPI

logger = logging.getLogger(__name__)

DRAIN_SECONDS_ENV = "OWNEVO_SHUTDOWN_DRAIN_SECONDS"
DEFAULT_DRAIN_SECONDS = 0.0

# Signals uvicorn handles for graceful shutdown. We chain to its handler for
# each, so the drain applies whether the orchestrator sends TERM or a Ctrl-C
# INT reaches the process.
_DRAIN_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def drain_seconds_from_env() -> float:
    """Drain window in seconds. ``0`` (the default) disables the drain."""
    raw = os.environ.get(DRAIN_SECONDS_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_DRAIN_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not a number; graceful-shutdown drain disabled",
            DRAIN_SECONDS_ENV,
            raw,
        )
        return DEFAULT_DRAIN_SECONDS
    if value < 0:
        logger.warning(
            "%s=%r is negative; graceful-shutdown drain disabled",
            DRAIN_SECONDS_ENV,
            raw,
        )
        return DEFAULT_DRAIN_SECONDS
    return value


def install_drain(app: FastAPI, drain_seconds: float | None = None) -> bool:
    """Install the drain signal handlers on the running event loop.

    Returns ``True`` when handlers were installed, ``False`` for any of the
    no-op cases: the drain window is 0 (the default), there is no running
    loop, we are not on the main thread (signals can only be set there), or
    the loop does not support signal handlers (e.g. Windows / Proactor).

    Idempotent for the app's lifetime: the flag it sets (``shutting_down``)
    and the "already draining" guard make repeated signals after the first a
    no-op until the drain elapses.
    """
    if drain_seconds is None:
        drain_seconds = drain_seconds_from_env()
    if drain_seconds <= 0:
        return False
    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False

    # Captured before we install ours: uvicorn set these via signal.signal
    # before lifespan startup ran, so getsignal() returns its handle_exit.
    previous = {sig: signal.getsignal(sig) for sig in _DRAIN_SIGNALS}

    def _delegate(sig: int) -> None:
        # Drain elapsed: hand control back to uvicorn's handler and re-deliver
        # the signal so it performs its normal graceful shutdown now.
        with contextlib.suppress(ValueError, RuntimeError):
            loop.remove_signal_handler(sig)
        prev = previous.get(sig)
        if callable(prev):
            signal.signal(sig, prev)
        signal.raise_signal(sig)

    def _on_signal(sig: int) -> None:
        if getattr(app.state, "shutting_down", False):
            # Already draining from an earlier signal — let the timer run.
            return
        app.state.shutting_down = True
        logger.info(
            "shutdown: %s received; draining %.1fs before exit (readiness now 503)",
            signal.Signals(sig).name,
            drain_seconds,
        )
        loop.call_later(drain_seconds, _delegate, sig)

    try:
        for sig in _DRAIN_SIGNALS:
            loop.add_signal_handler(sig, _on_signal, sig)
    except NotImplementedError:
        # Loop without signal support (e.g. Windows). Leave uvicorn's
        # handlers in place; getsignal() captured nothing we replaced.
        return False
    return True


__all__ = [
    "DEFAULT_DRAIN_SECONDS",
    "DRAIN_SECONDS_ENV",
    "drain_seconds_from_env",
    "install_drain",
]
