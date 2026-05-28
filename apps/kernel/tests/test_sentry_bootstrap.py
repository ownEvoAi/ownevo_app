"""Tests for the Sentry bootstrap.

These tests do not contact Sentry. ``sentry_sdk.init`` is monkey-patched
so calling it captures the kwargs we would have shipped instead of
spinning up a background transport thread.
"""

from __future__ import annotations

import pytest
import sentry_sdk
from ownevo_kernel.api import _sentry
from ownevo_kernel.api._sentry import (
    DEFAULT_ENVIRONMENT,
    DEFAULT_TRACES_SAMPLE_RATE,
    DSN_ENV,
    ENVIRONMENT_ENV,
    RELEASE_ENV,
    TRACES_SAMPLE_RATE_ENV,
    _before_send,
    flush_sentry,
    init_sentry,
    tag_request,
    traces_sample_rate_from_env,
)

_FAKE_DSN = "https://abc@sentry.example.invalid/1"


@pytest.fixture
def fake_init(monkeypatch):
    captured: dict[str, object] = {}
    calls = {"count": 0}

    def _fake(**kwargs):
        captured.clear()
        captured.update(kwargs)
        calls["count"] += 1

    monkeypatch.setattr(sentry_sdk, "init", _fake)
    return captured, calls


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for name in (DSN_ENV, ENVIRONMENT_ENV, RELEASE_ENV, TRACES_SAMPLE_RATE_ENV):
        monkeypatch.delenv(name, raising=False)


def test_init_is_noop_without_dsn(fake_init):
    captured, calls = fake_init
    assert init_sentry() is False
    assert calls["count"] == 0
    assert captured == {}


def test_blank_dsn_treated_as_unset(fake_init, monkeypatch):
    _, calls = fake_init
    monkeypatch.setenv(DSN_ENV, "   ")
    assert init_sentry() is False
    assert calls["count"] == 0


def test_init_with_dsn_passes_expected_kwargs(fake_init, monkeypatch):
    captured, calls = fake_init
    monkeypatch.setenv(DSN_ENV, _FAKE_DSN)
    assert init_sentry() is True
    assert calls["count"] == 1
    assert captured["dsn"] == _FAKE_DSN
    assert captured["environment"] == DEFAULT_ENVIRONMENT
    assert captured["release"] is None
    assert captured["traces_sample_rate"] == DEFAULT_TRACES_SAMPLE_RATE
    assert captured["send_default_pii"] is False
    assert captured["before_send"] is _before_send
    # Transaction events use a separate hook; verify it gets the same scrubbing.
    assert captured["before_send_transaction"] is _before_send
    # Stack frame locals must never ship (may contain credential dicts).
    assert captured["include_local_variables"] is False
    # Both integrations must be present so middleware wiring happens
    # without the caller threading an ASGI middleware in by hand.
    integration_names = {type(i).__name__ for i in captured["integrations"]}
    assert {"FastApiIntegration", "StarletteIntegration"} <= integration_names


def test_environment_from_env(fake_init, monkeypatch):
    captured, _ = fake_init
    monkeypatch.setenv(DSN_ENV, _FAKE_DSN)
    monkeypatch.setenv(ENVIRONMENT_ENV, "production")
    init_sentry()
    assert captured["environment"] == "production"


def test_release_from_env(fake_init, monkeypatch):
    captured, _ = fake_init
    monkeypatch.setenv(DSN_ENV, _FAKE_DSN)
    monkeypatch.setenv(RELEASE_ENV, "abc1234")
    init_sentry()
    assert captured["release"] == "abc1234"


def test_traces_sample_rate_from_env(fake_init, monkeypatch):
    captured, _ = fake_init
    monkeypatch.setenv(DSN_ENV, _FAKE_DSN)
    monkeypatch.setenv(TRACES_SAMPLE_RATE_ENV, "0.25")
    init_sentry()
    assert captured["traces_sample_rate"] == pytest.approx(0.25)


def test_idempotent(fake_init, monkeypatch):
    """Calling init twice re-invokes sentry_sdk.init (it replaces the
    global client in place). The slice's contract is that re-init does
    not throw — not that we deduplicate."""
    _, calls = fake_init
    monkeypatch.setenv(DSN_ENV, _FAKE_DSN)
    assert init_sentry() is True
    assert init_sentry() is True
    assert calls["count"] == 2


@pytest.mark.parametrize("raw", ["not-a-number", "true", "0.5abc"])
def test_traces_sample_rate_rejects_non_float(monkeypatch, raw):
    monkeypatch.setenv(TRACES_SAMPLE_RATE_ENV, raw)
    with pytest.raises(ValueError, match="not a valid float"):
        traces_sample_rate_from_env()


@pytest.mark.parametrize("raw", ["-0.1", "1.01", "2", "-1"])
def test_traces_sample_rate_rejects_out_of_range(monkeypatch, raw):
    monkeypatch.setenv(TRACES_SAMPLE_RATE_ENV, raw)
    with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
        traces_sample_rate_from_env()


def test_traces_sample_rate_blank_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(TRACES_SAMPLE_RATE_ENV, "   ")
    assert traces_sample_rate_from_env() == DEFAULT_TRACES_SAMPLE_RATE


def test_bad_sample_rate_fails_init_loudly(fake_init, monkeypatch):
    """A typo in the sample rate must crash the boot rather than
    silently disabling traces or shipping the wrong rate."""
    _, calls = fake_init
    monkeypatch.setenv(DSN_ENV, _FAKE_DSN)
    monkeypatch.setenv(TRACES_SAMPLE_RATE_ENV, "lots")
    with pytest.raises(ValueError):
        init_sentry()
    assert calls["count"] == 0


def test_before_send_strips_request_body():
    event = {"request": {"data": {"workflow_spec": "secret"}, "method": "POST"}}
    cleaned = _before_send(event, hint={})
    assert "data" not in cleaned["request"]
    assert cleaned["request"]["method"] == "POST"


def test_before_send_strips_cookies():
    event = {"request": {"cookies": {"session": "abc"}, "url": "http://x/"}}
    cleaned = _before_send(event, hint={})
    assert "cookies" not in cleaned["request"]


def test_before_send_passes_through_when_no_request():
    event = {"level": "error", "message": "x"}
    cleaned = _before_send(event, hint={})
    assert cleaned == event


def test_tag_request_does_not_raise_without_init():
    """``set_tag`` mutates the active scope unconditionally; this just
    pins that the helper is safe to call from request middleware even
    when no DSN is configured."""
    tag_request("test-request-id-abc")


def test_tag_request_lands_in_scope(monkeypatch):
    calls: list[tuple[str, str]] = []

    def _capture(key, value):
        calls.append((key, value))

    monkeypatch.setattr(_sentry.sentry_sdk, "set_tag", _capture)
    tag_request("abc-123")
    assert calls == [("request_id", "abc-123")]


@pytest.mark.parametrize("raw,expected", [("0.0", 0.0), ("1.0", 1.0), ("0", 0.0), ("1", 1.0)])
def test_traces_sample_rate_accepts_boundary_values(monkeypatch, raw, expected):
    monkeypatch.setenv(TRACES_SAMPLE_RATE_ENV, raw)
    assert traces_sample_rate_from_env() == pytest.approx(expected)


def test_before_send_strips_both_data_and_cookies():
    event = {
        "request": {
            "data": {"workflow_spec": "secret"},
            "cookies": {"session": "x"},
            "method": "POST",
            "headers": {"X-Request-Id": "abc"},
        }
    }
    cleaned = _before_send(event, hint={})
    assert "data" not in cleaned["request"]
    assert "cookies" not in cleaned["request"]
    assert cleaned["request"]["method"] == "POST"
    assert cleaned["request"]["headers"] == {"X-Request-Id": "abc"}


def test_before_send_preserves_headers():
    event = {
        "request": {
            "data": "body",
            "headers": {"Content-Type": "application/json", "X-Request-Id": "xyz"},
        }
    }
    cleaned = _before_send(event, hint={})
    assert cleaned["request"]["headers"] == {
        "Content-Type": "application/json",
        "X-Request-Id": "xyz",
    }


def test_blank_environment_falls_back_to_default(fake_init, monkeypatch):
    captured, _ = fake_init
    monkeypatch.setenv(DSN_ENV, _FAKE_DSN)
    monkeypatch.setenv(ENVIRONMENT_ENV, "   ")
    init_sentry()
    assert captured["environment"] == DEFAULT_ENVIRONMENT


def test_flush_sentry_does_not_raise_without_init():
    """flush_sentry must be safe to call from the lifespan finally block
    even when no DSN was configured (no-op client)."""
    flush_sentry()


def test_flush_sentry_delegates_to_sdk(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(_sentry.sentry_sdk, "flush", lambda timeout: calls.append(timeout))
    flush_sentry(timeout=1.5)
    assert calls == [1.5]
