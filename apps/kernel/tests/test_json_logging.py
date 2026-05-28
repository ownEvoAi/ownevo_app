"""Tests for the opt-in JSON log formatter.

The formatter must:
  * emit one JSON object per record on a single line;
  * preserve any ``extra={...}`` keys the caller attached, since the
    exception handler depends on ``request_id``/``error_id`` landing as
    structured fields rather than buried in the message;
  * include the traceback when ``exc_info`` is attached.
"""

from __future__ import annotations

import json
import logging

import pytest
from ownevo_kernel.api._logging import (
    LOG_FORMAT_ENV,
    JsonFormatter,
    configure_logging,
    json_logging_enabled,
)


def _format_one(record: logging.LogRecord) -> dict:
    """Run the formatter and parse the resulting line back to JSON."""
    line = JsonFormatter().format(record)
    return json.loads(line)


def _build_record(
    *,
    name: str = "ownevo_kernel.test",
    level: int = logging.INFO,
    msg: str = "hello %s",
    args: tuple = ("world",),
    extra: dict | None = None,
    exc_info=None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="dummy.py",
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )
    if extra is not None:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


def test_formatter_emits_one_json_object() -> None:
    record = _build_record()
    parsed = _format_one(record)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "ownevo_kernel.test"
    # %-args resolved.
    assert parsed["message"] == "hello world"
    assert "timestamp" in parsed
    # ISO 8601 with timezone.
    assert parsed["timestamp"].endswith("+00:00")


def test_formatter_preserves_extras() -> None:
    record = _build_record(
        extra={
            "request_id": "abc123",
            "error_id": "abc123",
            "method": "GET",
            "path": "/boom",
            "exc_class": "RuntimeError",
        }
    )
    parsed = _format_one(record)
    assert parsed["request_id"] == "abc123"
    assert parsed["error_id"] == "abc123"
    assert parsed["method"] == "GET"
    assert parsed["path"] == "/boom"
    assert parsed["exc_class"] == "RuntimeError"


def test_formatter_includes_exception_when_attached() -> None:
    try:
        raise RuntimeError("simulated")
    except RuntimeError:
        import sys
        record = _build_record(level=logging.ERROR, exc_info=sys.exc_info())
    parsed = _format_one(record)
    assert "exception" in parsed
    assert "RuntimeError" in parsed["exception"]
    assert "simulated" in parsed["exception"]


def test_formatter_coerces_non_json_extras() -> None:
    """An exotic ``extra`` value must not crash the log line — it should
    fall back to ``str()`` so the record still ships."""
    class _Custom:
        def __repr__(self) -> str:
            return "<Custom>"

    record = _build_record(extra={"thing": _Custom()})
    parsed = _format_one(record)
    assert parsed["thing"] == "<Custom>"


def test_json_logging_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOG_FORMAT_ENV, raising=False)
    assert json_logging_enabled() is False


def test_json_logging_enabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOG_FORMAT_ENV, "json")
    assert json_logging_enabled() is True


def test_configure_logging_is_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LOG_FORMAT_ENV, raising=False)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging()
        assert root.handlers == before
    finally:
        root.handlers = before


def test_configure_logging_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling configure_logging twice must not stack two JSON handlers."""
    monkeypatch.setenv(LOG_FORMAT_ENV, "json")
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    try:
        configure_logging()
        configure_logging()
        ours = [h for h in root.handlers if getattr(h, "_ownevo_json", False)]
        assert len(ours) == 1
    finally:
        # Restore the prior handler set so we don't leak into other tests.
        root.handlers = original_handlers
