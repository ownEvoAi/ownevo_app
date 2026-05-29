"""Graceful-shutdown drain: env parsing, the readiness flip, and handler install.

The full signal -> drain -> re-deliver sequence is not exercised end to end
(it would mean delivering a real SIGTERM mid-test and racing uvicorn). Instead
the load-bearing pieces are pinned: the env parser, that ``/readyz`` flips to
503 once ``app.state.shutting_down`` is set (while ``/livez`` stays 200), and
that ``install_drain`` is a no-op when the drain window is 0 and installs loop
signal handlers when it is positive.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import AsyncGenerator

import httpx
import pytest
from httpx import ASGITransport
from ownevo_kernel.api._graceful_shutdown import (
    DEFAULT_DRAIN_SECONDS,
    DRAIN_SECONDS_ENV,
    drain_seconds_from_env,
    install_drain,
)
from ownevo_kernel.api.app import create_app

# ---------------------------------------------------------------------------
# Env parsing
# ---------------------------------------------------------------------------


def test_default_is_zero_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DRAIN_SECONDS_ENV, raising=False)
    assert drain_seconds_from_env() == DEFAULT_DRAIN_SECONDS == 0.0


def test_positive_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DRAIN_SECONDS_ENV, "5")
    assert drain_seconds_from_env() == 5.0


def test_fractional_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DRAIN_SECONDS_ENV, "2.5")
    assert drain_seconds_from_env() == 2.5


def test_non_numeric_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd value disables the drain rather than crashing the boot."""
    monkeypatch.setenv(DRAIN_SECONDS_ENV, "soon")
    assert drain_seconds_from_env() == 0.0


def test_negative_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DRAIN_SECONDS_ENV, "-3")
    assert drain_seconds_from_env() == 0.0


# ---------------------------------------------------------------------------
# Readiness flip
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_client() -> AsyncGenerator[
    tuple[httpx.AsyncClient, object], None
]:
    # No lifespan entered, so app.state.shutting_down starts unset; the tests
    # set it explicitly to exercise the drain branch.
    app = create_app(cors_origins=[])
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        yield client, app


async def test_readyz_503_when_shutting_down(
    app_client: tuple[httpx.AsyncClient, object],
) -> None:
    client, app = app_client
    app.state.shutting_down = True
    resp = await client.get("/api/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready", "reason": "shutting_down", "db": "ok"}


async def test_livez_stays_200_when_shutting_down(
    app_client: tuple[httpx.AsyncClient, object],
) -> None:
    """Liveness must not flip during a drain — the process is healthy, it is
    just declining new traffic. Flipping it would trigger a restart."""
    client, app = app_client
    app.state.shutting_down = True
    resp = await client.get("/api/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# install_drain
# ---------------------------------------------------------------------------


async def test_install_drain_noop_when_disabled() -> None:
    app = create_app(cors_origins=[])
    assert install_drain(app, drain_seconds=0) is False
    # Default (env unset -> 0) is also a no-op.
    assert install_drain(app) is False


async def test_install_drain_installs_when_enabled() -> None:
    """With a positive window, handlers land on the running loop. Capture and
    restore the process signal disposition so the shared test loop is left as
    we found it."""
    app = create_app(cors_origins=[])
    loop = asyncio.get_running_loop()
    drain_signals = (signal.SIGTERM, signal.SIGINT)
    originals = {sig: signal.getsignal(sig) for sig in drain_signals}
    try:
        assert install_drain(app, drain_seconds=5) is True
    finally:
        for sig in drain_signals:
            loop.remove_signal_handler(sig)
            signal.signal(sig, originals[sig])
