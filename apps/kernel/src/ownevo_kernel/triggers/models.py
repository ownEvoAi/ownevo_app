"""Pydantic models for the trigger framework (Track 17.1).

`TriggerDefinition` mirrors the `trigger_definitions` DB row; the
`*Config` types are stored as JSONB in `trigger_definitions.config` and
validated here at the application layer.

Design notes
------------
* All config models use ``extra="forbid"`` so unknown keys surface as
  validation errors at write time rather than silently surviving a
  round-trip through JSONB.
* Secrets (HMAC keys, OAuth tokens) are stored via `EncryptedField` —
  the same Fernet-based scheme used for LangSmith + Copilot Studio
  credentials.  Raw plaintext never reaches the DB.
* The ``TriggerSpec`` class is the *design-time* counterpart — a
  lightweight descriptor stored in ``WorkflowSpec.triggers`` (see
  ``nl_gen/spec.py``) that records the intended trigger shape without
  carrying live secrets or DB identifiers.
"""

from __future__ import annotations

import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

TriggerKind = Literal["webhook", "cron", "threshold", "slack", "email", "calendar"]

TriggerAction = Literal[
    "run_clustering",   # run cluster_production_failures for this workflow
    "run_iteration",    # start one improvement-loop iteration
    "ingest_failures",  # convert ingested content to production_failure AgentEvents
]

TriggerStatus = Literal["ok", "error"]


# ---------------------------------------------------------------------------
# Kind-specific configuration models
# ---------------------------------------------------------------------------


class WebhookConfig(_Base):
    """Configuration for an inbound HMAC-signed webhook trigger.

    The `hmac_secret` is used to verify the `X-Ownevo-Signature` header
    (or the provider-specific header named in `signature_header`).  The
    raw secret is encrypted at rest in the DB; the application layer
    decrypts it at request time before computing the expected HMAC.

    Supported algorithm: HMAC-SHA256.  The signature format expected in
    the header is ``sha256=<hex-digest>`` (the convention used by GitHub,
    Stripe, Twilio, and most webhook providers).
    """

    hmac_secret: str = Field(min_length=1, description="HMAC-SHA256 secret (stored encrypted)")
    signature_header: str = Field(
        default="X-Ownevo-Signature",
        description="HTTP header carrying the HMAC signature",
    )
    allowed_ips: list[str] = Field(
        default_factory=list,
        description="Optional IP allowlist; empty list means any origin is accepted",
    )
    max_age_seconds: int = Field(
        default=300,
        ge=0,
        description=(
            "Reject payloads whose X-Ownevo-Timestamp is older than this many seconds. "
            "Set to 0 to disable replay-attack protection."
        ),
    )


class CronConfig(_Base):
    """Configuration for a cron-schedule trigger.

    `schedule` follows the standard five-field Unix cron syntax::

        ┌─ minute (0-59)
        │  ┌─ hour (0-23)
        │  │  ┌─ day-of-month (1-31)
        │  │  │  ┌─ month (1-12)
        │  │  │  │  ┌─ day-of-week (0-7, Sun=0/7)
        *  *  *  *  *

    Extended expressions (``@hourly``, ``@daily``, ``@weekly``) are also
    accepted by the scheduler, which delegates parsing to *croniter*.
    """

    schedule: str = Field(min_length=1, description="cron expression or @alias")
    timezone: str = Field(
        default="UTC",
        description="IANA timezone name (e.g. 'America/New_York')",
    )


class ThresholdAggregation(str):
    """Rolling-window aggregate function applied to `metric_samples`."""


ThresholdOperator = Literal[">", ">=", "<", "<=", "==", "!="]
ThresholdAgg = Literal["avg", "sum", "count", "min", "max"]


class ThresholdConfig(_Base):
    """Configuration for a metric-threshold trigger.

    The threshold evaluator polls ``metric_samples`` at the configured
    ``poll_interval_minutes`` and evaluates::

        aggregate(metric_name, window_minutes) <operator> threshold_value

    When the expression becomes truthy (and was not truthy on the
    immediately preceding poll), the trigger fires.  The "rising-edge"
    semantics avoid repeated fires while a metric sits above a threshold.

    Example: fire once when the 60-minute average SMAPE for workflow W1
    rises above 0.30::

        metric_name = "smape"
        window_minutes = 60
        aggregation = "avg"
        operator = ">"
        threshold_value = 0.30
        poll_interval_minutes = 5
    """

    metric_name: str = Field(min_length=1)
    window_minutes: int = Field(default=60, ge=1)
    aggregation: ThresholdAgg = "avg"
    operator: ThresholdOperator = ">"
    threshold_value: float
    poll_interval_minutes: int = Field(default=5, ge=1)
    # Internal: set to True after a fire to implement rising-edge semantics.
    # Persisted in the JSONB config blob; updated by the evaluator.
    _currently_above: bool = False


class SlackConfig(_Base):
    """Configuration for Slack channel message ingestion.

    Messages arriving in the configured channel are converted to
    ``ToolCallResultEvent(status="error")`` AgentEvents and fed into the
    failure-clustering pipeline, so Slack incident threads become
    eval cases automatically.

    `mcp_server_id` references a row in `mcp_servers` that provides the
    Slack MCP binding.  The kernel calls the MCP server's
    ``slack_list_messages`` tool to poll for new messages; each unique
    message text becomes one AgentEvent in the trace.
    """

    mcp_server_id: str = Field(
        min_length=1,
        description="ID of the registered Slack MCP server",
    )
    channel_id: str = Field(
        min_length=1,
        description="Slack channel ID (e.g. C012AB3CD)",
    )
    workspace_id: str | None = Field(
        default=None,
        description="Slack workspace ID; disambiguates when multiple workspaces share an MCP server",
    )
    filter_pattern: str | None = Field(
        default=None,
        description="Optional Python regex; only messages matching this pattern are ingested",
    )
    # Poll interval for new messages (seconds).  The Slack Events API
    # push model is preferred when available; polling is the fallback.
    poll_interval_seconds: int = Field(default=60, ge=10)
    # How far back to look on the first poll (avoids flooding with history).
    lookback_hours: int = Field(default=1, ge=1)


class EmailProvider(str):
    """Supported email providers."""


EmailProviderT = Literal["gmail", "outlook"]


class EmailConfig(_Base):
    """Configuration for email thread ingestion.

    New threads matching the label / folder filter are converted to
    ``production_failure`` AgentEvents and fed into the clustering
    pipeline.  The kernel uses the registered Gmail or Outlook MCP
    server to list threads; IMAP/REST credentials are managed by the
    MCP binding.

    For Gmail, `label` is the label name (e.g. ``"ownevo-failures"``).
    For Outlook, `folder` is the folder display name
    (e.g. ``"Agent Failures"``).
    """

    provider: EmailProviderT
    mcp_server_id: str = Field(
        min_length=1,
        description="ID of the registered Gmail or Outlook MCP server",
    )
    label: str | None = Field(
        default=None,
        description="Gmail label to subscribe to (provider='gmail' only)",
    )
    folder: str | None = Field(
        default=None,
        description="Outlook folder name to subscribe to (provider='outlook' only)",
    )
    filter_from: str | None = Field(
        default=None,
        description="Optional sender address/domain filter",
    )
    filter_subject_pattern: str | None = Field(
        default=None,
        description="Optional Python regex applied to the subject line",
    )
    poll_interval_seconds: int = Field(default=300, ge=60)

    @model_validator(mode="after")
    def _label_or_folder_set(self) -> EmailConfig:
        if self.provider == "gmail" and self.label is None:
            raise ValueError("EmailConfig with provider='gmail' requires label")
        if self.provider == "outlook" and self.folder is None:
            raise ValueError("EmailConfig with provider='outlook' requires folder")
        return self


CalendarProviderT = Literal["google", "outlook"]


class CalendarConfig(_Base):
    """Configuration for calendar-event proximity triggers.

    Fires N minutes before (`offset_minutes < 0`) or after
    (`offset_minutes > 0`) a matching calendar event starts.

    Use-cases:
    * Fire a clustering run 15 minutes before a weekly review meeting
      so fresh clusters are ready when the VP opens the approval queue.
    * Kick off an iteration after a scheduled board review (offset > 0).

    `event_title_pattern` is a Python regex applied to the event summary;
    leave ``None`` to fire on every event in the calendar.
    """

    provider: CalendarProviderT
    mcp_server_id: str = Field(
        min_length=1,
        description="ID of the registered Google or Outlook Calendar MCP server",
    )
    calendar_id: str = Field(
        min_length=1,
        description="Calendar identifier (Google: calendar email; Outlook: folder ID)",
    )
    offset_minutes: int = Field(
        default=-15,
        description=(
            "Signed offset in minutes relative to event start. "
            "Negative = fire before the event; positive = fire after."
        ),
    )
    event_title_pattern: str | None = Field(
        default=None,
        description="Optional Python regex filter applied to event title/summary",
    )
    poll_interval_seconds: int = Field(default=120, ge=60)


# ---------------------------------------------------------------------------
# Discriminated-union for the config JSONB blob
# ---------------------------------------------------------------------------

# Annotated union keyed on the `kind` field stored alongside the config.
# The API and scheduler use this to validate the config before writing.
AnyTriggerConfig = (
    WebhookConfig
    | CronConfig
    | ThresholdConfig
    | SlackConfig
    | EmailConfig
    | CalendarConfig
)


def parse_trigger_config(kind: TriggerKind, raw: dict[str, Any]) -> AnyTriggerConfig:
    """Validate and return the typed config for a given trigger kind.

    Raises `pydantic.ValidationError` when `raw` does not conform to the
    expected schema for `kind`.
    """
    _map: dict[str, type[_Base]] = {
        "webhook": WebhookConfig,
        "cron": CronConfig,
        "threshold": ThresholdConfig,
        "slack": SlackConfig,
        "email": EmailConfig,
        "calendar": CalendarConfig,
    }
    return _map[kind].model_validate(raw)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# DB row model
# ---------------------------------------------------------------------------


class TriggerDefinition(_Base):
    """Application-level representation of a `trigger_definitions` DB row.

    `config` is stored as raw dict here; call `parse_trigger_config` to
    get a typed model when the kind-specific fields are needed.
    """

    id: UUID
    workflow_id: UUID
    name: str
    kind: TriggerKind
    action: TriggerAction
    config: dict[str, Any]
    enabled: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
    last_fired_at: datetime.datetime | None
    fire_count: int


class TriggerFire(_Base):
    """Application-level representation of a `trigger_fires` DB row."""

    id: UUID
    trigger_id: UUID
    workflow_id: UUID
    fired_at: datetime.datetime
    action: TriggerAction
    status: TriggerStatus
    error_message: str | None
    payload_summary: str | None


# ---------------------------------------------------------------------------
# Design-time spec (stored in WorkflowSpec.triggers)
# ---------------------------------------------------------------------------


class TriggerSpec(_Base):
    """Lightweight design-time trigger descriptor stored in `WorkflowSpec`.

    Carries enough information to describe what triggers the workflow
    *should* have, without embedding live secrets or DB identifiers.
    The actual runtime `TriggerDefinition` rows are created separately
    through the triggers API.
    """

    kind: TriggerKind
    name: str = Field(min_length=1)
    description: str = ""
    action: TriggerAction = "run_clustering"


__all__ = [
    "AnyTriggerConfig",
    "CalendarConfig",
    "CalendarProviderT",
    "CronConfig",
    "EmailConfig",
    "EmailProviderT",
    "SlackConfig",
    "ThresholdAgg",
    "ThresholdConfig",
    "ThresholdOperator",
    "TriggerAction",
    "TriggerDefinition",
    "TriggerFire",
    "TriggerKind",
    "TriggerSpec",
    "TriggerStatus",
    "WebhookConfig",
    "parse_trigger_config",
]
