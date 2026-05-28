"""Sentry error reporting bootstrap.

Activated only when ``SENTRY_DSN`` is set, so dev and CI installs of
the kernel stay silent without any explicit opt-out. When the DSN is
present, ``init_sentry`` wires up the Sentry SDK with the FastAPI +
Starlette integrations so every uncaught route exception is shipped
alongside the structured JSON log line the global error handler
already writes.

The request-id middleware stamps a ``request_id`` Sentry tag per
request, so a Sentry event and a JSON log line for the same incident
share the correlation key the client already saw in the
``X-Request-Id`` response header.

A ``before_send`` hook drops the captured request body before the
event ships. The kernel's POST surface accepts workflow specs, agent
payloads, and provider credentials — none of which should leave the
box. PII is also disabled (``send_default_pii=False``) so cookies and
client IPs are not captured.

Sample-rate handling:

  * Error events are always captured (no sample rate to set).
  * Performance traces default to ``0.0`` so a noisy deployment does
    not silently burn the Sentry quota. The operator opts in via
    ``OWNEVO_SENTRY_TRACES_SAMPLE_RATE`` (``[0.0, 1.0]``); a malformed
    value fails the boot rather than silently disabling traces or
    sampling at the wrong rate.
"""

from __future__ import annotations

import os
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

DSN_ENV = "SENTRY_DSN"
ENVIRONMENT_ENV = "OWNEVO_ENV"
RELEASE_ENV = "OWNEVO_SENTRY_RELEASE"
TRACES_SAMPLE_RATE_ENV = "OWNEVO_SENTRY_TRACES_SAMPLE_RATE"

DEFAULT_ENVIRONMENT = "development"
DEFAULT_TRACES_SAMPLE_RATE = 0.0

_REQUEST_ID_TAG = "request_id"


def _read_env(name: str) -> str | None:
    """Return the env var value with whitespace stripped, or None when blank.

    docker-compose's ``${VAR:-}`` shape passes an unset variable through
    as the empty string, which we want to treat as unset.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def traces_sample_rate_from_env() -> float:
    raw = _read_env(TRACES_SAMPLE_RATE_ENV)
    if raw is None:
        return DEFAULT_TRACES_SAMPLE_RATE
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{TRACES_SAMPLE_RATE_ENV}={raw!r} is not a valid float"
        ) from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"{TRACES_SAMPLE_RATE_ENV}={raw!r} must be between 0.0 and 1.0"
        )
    return value


def _before_send(
    event: dict[str, Any], hint: dict[str, Any]
) -> dict[str, Any] | None:
    """Strip the captured request body (and cookies) before the event ships.

    POST bodies on this kernel routinely include workflow specs, agent
    payloads, and the test-credentials route's plaintext keys. The
    Sentry FastAPI integration captures them by default; we drop them
    explicitly. Headers are left in place because the integration
    already redacts ``Authorization`` / ``Cookie`` and the rest
    (``Content-Type``, ``X-Request-Id``) are useful for triage.
    """
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
    return event


def init_sentry() -> bool:
    """Initialize Sentry when configured. Returns True on init, False otherwise.

    No-op when ``SENTRY_DSN`` is unset or blank, so dev and CI never
    need to set an opt-out. Safe to call more than once; Sentry
    replaces its global client in place.
    """
    dsn = _read_env(DSN_ENV)
    if dsn is None:
        return False

    environment = _read_env(ENVIRONMENT_ENV) or DEFAULT_ENVIRONMENT
    release = _read_env(RELEASE_ENV)
    traces_sample_rate = traces_sample_rate_from_env()

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
        before_send=_before_send,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
        ],
    )
    return True


def tag_request(request_id: str) -> None:
    """Stamp the active Sentry scope with the request id.

    A no-op (in effect) when Sentry isn't initialized: the SDK's
    default client never ships events, so the tag set here is silently
    dropped. Cheap enough to call unconditionally per request.
    """
    sentry_sdk.set_tag(_REQUEST_ID_TAG, request_id)


__all__ = [
    "DEFAULT_ENVIRONMENT",
    "DEFAULT_TRACES_SAMPLE_RATE",
    "DSN_ENV",
    "ENVIRONMENT_ENV",
    "RELEASE_ENV",
    "TRACES_SAMPLE_RATE_ENV",
    "init_sentry",
    "tag_request",
    "traces_sample_rate_from_env",
]
