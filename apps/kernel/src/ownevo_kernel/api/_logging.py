"""Opt-in structured (JSON) logging for the kernel.

The default kernel boot keeps the stdlib ``logging`` defaults so
``make api`` produces the same human-readable lines it always has. When
``OWNEVO_LOG_FORMAT=json`` is set, ``configure_logging()`` swaps the
root logger's handler for one that emits one JSON object per record —
log shippers (Datadog, Loki, Cloud Logging) can ingest the stream
directly without a regex-parse step.

The formatter preserves any ``extra={...}`` kwargs the call site
attached (e.g. ``request_id``, ``error_id``), so the exception handler's
correlation fields land in the same record they describe rather than
inside a free-text message.

This module is intentionally tiny and dependency-free (no
``python-json-logger``) so the JSON-log path stays in lockstep with the
exception handler that produced the fields it serialises.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

LOG_FORMAT_ENV = "OWNEVO_LOG_FORMAT"
JSON_LOG_FORMAT_VALUE = "json"

# Standard LogRecord attributes — anything outside this set on a record
# was attached via ``logging.<level>(..., extra={...})`` and is worth
# carrying through to the JSON payload.
_STANDARD_LOGRECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

# Top-level keys we write explicitly in format(). An extra= kwarg with the
# same name would silently overwrite the canonical field — skip them so a
# caller's extra={"level": "INFO"} on an ERROR record cannot corrupt the
# field that log shippers threshold on.
_RESERVED_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {"timestamp", "level", "logger", "exception", "stack"}
)


class JsonFormatter(logging.Formatter):
    """One JSON object per log record, with extras preserved."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        # ``record.getMessage()`` resolves any %-args the caller passed.
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_ATTRS or key in _RESERVED_PAYLOAD_KEYS or key.startswith("_"):
                continue
            payload[key] = _json_safe(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str)


def _json_safe(value: object) -> object:
    """JSON-pass-through for plain types, ``str()`` fallback otherwise.

    Mirrors the philosophy of ``iteration_runner._json_safe`` — the log
    line is more useful than a TypeError if a caller attaches something
    exotic in ``extra``.
    """
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def json_logging_enabled() -> bool:
    """True when the operator has opted into JSON logs."""
    return os.environ.get(LOG_FORMAT_ENV, "").strip().lower() == JSON_LOG_FORMAT_VALUE


def configure_logging() -> None:
    """Install ``JsonFormatter`` on the root logger when opted in.

    Idempotent: calling it twice will not stack handlers. When the env
    flag is unset (the local-dev default), this is a no-op and the
    stdlib ``logging`` defaults remain in place.
    """
    if not json_logging_enabled():
        return
    root = logging.getLogger()
    # Drop any handler we previously installed so re-invocation (e.g.
    # during a reload in tests) doesn't duplicate output.
    root.handlers = [h for h in root.handlers if not getattr(h, "_ownevo_json", False)]
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._ownevo_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)


__all__ = [
    "JSON_LOG_FORMAT_VALUE",
    "JsonFormatter",
    "LOG_FORMAT_ENV",
    "configure_logging",
    "json_logging_enabled",
]
