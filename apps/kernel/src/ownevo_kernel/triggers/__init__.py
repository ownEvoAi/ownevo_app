"""Generic event-trigger framework (Track 17.1).

Six trigger backends — webhook, cron, threshold, Slack, email, calendar —
share a single registry table (`trigger_definitions`), a unified dispatcher
that maps a fired trigger to its configured action, and a background
scheduler that wakes each cron/threshold trigger on schedule.

Public surface
--------------
* `TriggerScheduler` — long-lived background task; manages cron expression
  ticking and threshold polling.  Lifecycle mirrors `ClusterAutoTrigger`.
* `handle_webhook_request(...)` — called from the API route; validates the
  HMAC signature and dispatches the action.
* REST routes are registered from `api/routes/triggers.py`.

Trigger kinds
-------------
``webhook``
    Inbound HMAC-signed HTTP POST from any external system.  The kernel
    validates the ``X-Ownevo-Signature`` (or provider-specific) header with
    the per-trigger secret before dispatching.

``cron``
    cron-expression-based schedule.  The background scheduler evaluates
    expressions (via *croniter*) and fires when the wall-clock crosses a
    tick boundary.

``threshold``
    Polls the ``metric_samples`` table at a configurable interval and fires
    when a rolling aggregate (avg / sum / count / min / max) crosses the
    configured value.

``slack``
    Subscribes to a Slack channel via the MCP-backed Slack integration.
    New messages are converted to ``production_failure`` AgentEvents and
    fed into the clustering pipeline.

``email``
    Subscribes to a Gmail label or Outlook folder via the MCP email
    integration.  New threads are converted to ``production_failure``
    AgentEvents.

``calendar``
    Fires N minutes before or after a matching Google / Outlook Calendar
    event.

All trigger kinds share the same `TriggerDefinition` DB model and produce
`TriggerFire` audit records.
"""

from .cron import CronTick
from .dispatcher import TriggerDispatcher
from .models import (
    CalendarConfig,
    CronConfig,
    EmailConfig,
    SlackConfig,
    ThresholdConfig,
    TriggerAction,
    TriggerDefinition,
    TriggerKind,
    WebhookConfig,
)
from .registry import TriggerRegistry
from .scheduler import TriggerScheduler
from .webhook import WebhookError, validate_webhook_signature

__all__ = [
    "CalendarConfig",
    "CronConfig",
    "CronTick",
    "EmailConfig",
    "SlackConfig",
    "ThresholdConfig",
    "TriggerAction",
    "TriggerDefinition",
    "TriggerDispatcher",
    "TriggerKind",
    "TriggerRegistry",
    "TriggerScheduler",
    "WebhookConfig",
    "WebhookError",
    "validate_webhook_signature",
]
