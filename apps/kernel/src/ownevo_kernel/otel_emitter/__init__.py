"""OTel emitter — emit ownEvo analysis events as OTLP spans (Track 17.2.3).

The emitter is the **outbound** companion to the inbound OTLP receiver
(`middleware/otel_receiver/`): while the receiver accepts customer-agent
traces, the emitter pushes ownEvo's own analysis events (clustering, gate,
approval, etc.) to a customer-configured OTLP backend.

Quick start
-----------
Set ``OWNEVO_OTEL_ENDPOINT`` to enable::

    export OWNEVO_OTEL_ENDPOINT=https://api.honeycomb.io/v1/traces
    export OWNEVO_OTEL_HEADERS=x-honeycomb-team=abc123
    # optional:
    export OWNEVO_OTEL_SERVICE_NAME=ownevo          # default
    export OWNEVO_OTEL_TIMEOUT_SECONDS=5            # default

The singleton `emitter` is auto-configured on import from these env vars.
Call ``emitter.emit_*(...)`` at each analysis event.  The emitter is a
no-op when ``OWNEVO_OTEL_ENDPOINT`` is unset, so existing code that calls
``emit_*`` without the env var enabled incurs no overhead.

Span conventions are documented in ``docs/OTEL_EMITTER_CONVENTIONS.md``.
"""

from .emitter import OtelEmitter, OtelEmitterConfig, get_emitter

__all__ = [
    "OtelEmitter",
    "OtelEmitterConfig",
    "get_emitter",
]
