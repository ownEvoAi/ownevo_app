"""Unit tests for trigger model validation (Track 17.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ownevo_kernel.triggers.models import (
    CalendarConfig,
    CronConfig,
    EmailConfig,
    SlackConfig,
    ThresholdConfig,
    TriggerSpec,
    WebhookConfig,
    parse_trigger_config,
)


class TestWebhookConfig:
    def test_valid_minimal(self):
        cfg = WebhookConfig(hmac_secret="s3cr3t")
        assert cfg.signature_header == "X-Ownevo-Signature"
        assert cfg.max_age_seconds == 300

    def test_custom_header(self):
        cfg = WebhookConfig(hmac_secret="s", signature_header="X-Hub-Signature-256")
        assert cfg.signature_header == "X-Hub-Signature-256"

    def test_empty_secret_rejected(self):
        with pytest.raises(ValidationError, match="at least 1 character|min_length"):
            WebhookConfig(hmac_secret="")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            WebhookConfig(hmac_secret="s", unknown_field=True)


class TestCronConfig:
    def test_valid_expression(self):
        cfg = CronConfig(schedule="*/5 * * * *")
        assert cfg.timezone == "UTC"

    def test_valid_alias(self):
        cfg = CronConfig(schedule="@daily", timezone="America/New_York")
        assert cfg.timezone == "America/New_York"

    def test_empty_schedule_rejected(self):
        with pytest.raises(ValidationError, match="at least 1 character|min_length"):
            CronConfig(schedule="")


class TestThresholdConfig:
    def test_valid_config(self):
        cfg = ThresholdConfig(
            metric_name="smape",
            window_minutes=60,
            aggregation="avg",
            operator=">",
            threshold_value=0.30,
        )
        assert cfg.poll_interval_minutes == 5

    def test_zero_window_rejected(self):
        with pytest.raises(ValidationError):
            ThresholdConfig(
                metric_name="m",
                window_minutes=0,
                operator=">",
                threshold_value=1.0,
            )


class TestSlackConfig:
    def test_valid_minimal(self):
        cfg = SlackConfig(mcp_server_id="srv-1", channel_id="C012AB3CD")
        assert cfg.poll_interval_seconds == 60
        assert cfg.lookback_hours == 1

    def test_empty_channel_rejected(self):
        with pytest.raises(ValidationError):
            SlackConfig(mcp_server_id="srv", channel_id="")


class TestEmailConfig:
    def test_gmail_requires_label(self):
        with pytest.raises(ValidationError, match="label"):
            EmailConfig(provider="gmail", mcp_server_id="srv")

    def test_outlook_requires_folder(self):
        with pytest.raises(ValidationError, match="folder"):
            EmailConfig(provider="outlook", mcp_server_id="srv")

    def test_valid_gmail(self):
        cfg = EmailConfig(
            provider="gmail", mcp_server_id="srv", label="ownevo-failures"
        )
        assert cfg.label == "ownevo-failures"

    def test_valid_outlook(self):
        cfg = EmailConfig(
            provider="outlook", mcp_server_id="srv", folder="Agent Failures"
        )
        assert cfg.folder == "Agent Failures"


class TestCalendarConfig:
    def test_valid_google(self):
        cfg = CalendarConfig(
            provider="google",
            mcp_server_id="srv",
            calendar_id="primary",
            offset_minutes=-15,
        )
        assert cfg.offset_minutes == -15

    def test_positive_offset_allowed(self):
        cfg = CalendarConfig(
            provider="outlook",
            mcp_server_id="srv",
            calendar_id="cal-id",
            offset_minutes=30,
        )
        assert cfg.offset_minutes == 30


class TestParseTriggerConfig:
    def test_webhook_dispatch(self):
        cfg = parse_trigger_config("webhook", {"hmac_secret": "abc"})
        assert isinstance(cfg, WebhookConfig)

    def test_cron_dispatch(self):
        cfg = parse_trigger_config("cron", {"schedule": "0 * * * *"})
        assert isinstance(cfg, CronConfig)

    def test_threshold_dispatch(self):
        cfg = parse_trigger_config(
            "threshold",
            {
                "metric_name": "smape",
                "window_minutes": 30,
                "operator": "<",
                "threshold_value": 0.1,
            },
        )
        assert isinstance(cfg, ThresholdConfig)

    def test_invalid_config_raises(self):
        with pytest.raises(ValidationError):
            parse_trigger_config("webhook", {})  # missing hmac_secret


class TestTriggerSpec:
    def test_valid_spec(self):
        spec = TriggerSpec(kind="cron", name="nightly-cluster", action="run_clustering")
        assert spec.kind == "cron"

    def test_defaults(self):
        spec = TriggerSpec(kind="webhook", name="payment-alerts")
        assert spec.action == "run_clustering"
        assert spec.description == ""
