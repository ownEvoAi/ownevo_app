"""Unit tests for HMAC webhook validation (Track 17.1.1)."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from ownevo_kernel.triggers.webhook import (
    WebhookError,
    validate_webhook_signature,
)


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class TestValidateWebhookSignature:
    SECRET = "test-secret-abc"
    BODY = b'{"event": "failure"}'

    def test_valid_signature_passes(self):
        sig = _sign(self.BODY, self.SECRET)
        ts = str(int(time.time()))
        validate_webhook_signature(
            body=self.BODY,
            signature_header_value=sig,
            timestamp_header_value=ts,
            hmac_secret=self.SECRET,
        )

    def test_missing_signature_raises(self):
        with pytest.raises(WebhookError, match="Missing"):
            validate_webhook_signature(
                body=self.BODY,
                signature_header_value=None,
                timestamp_header_value=str(int(time.time())),
                hmac_secret=self.SECRET,
            )

    def test_wrong_signature_raises(self):
        ts = str(int(time.time()))
        with pytest.raises(WebhookError, match="mismatch"):
            validate_webhook_signature(
                body=self.BODY,
                signature_header_value="sha256=deadbeef" + "0" * 56,
                timestamp_header_value=ts,
                hmac_secret=self.SECRET,
            )

    def test_tampered_body_fails(self):
        sig = _sign(self.BODY, self.SECRET)
        ts = str(int(time.time()))
        tampered = self.BODY + b"EXTRA"
        with pytest.raises(WebhookError, match="mismatch"):
            validate_webhook_signature(
                body=tampered,
                signature_header_value=sig,
                timestamp_header_value=ts,
                hmac_secret=self.SECRET,
            )

    def test_expired_timestamp_raises(self):
        sig = _sign(self.BODY, self.SECRET)
        old_ts = str(int(time.time()) - 600)  # 10 minutes ago
        with pytest.raises(WebhookError, match="old"):
            validate_webhook_signature(
                body=self.BODY,
                signature_header_value=sig,
                timestamp_header_value=old_ts,
                hmac_secret=self.SECRET,
                max_age_seconds=300,
            )

    def test_disabled_timestamp_check_ignores_age(self):
        sig = _sign(self.BODY, self.SECRET)
        old_ts = str(int(time.time()) - 600)
        # Should not raise because max_age_seconds=0 disables the check.
        validate_webhook_signature(
            body=self.BODY,
            signature_header_value=sig,
            timestamp_header_value=old_ts,
            hmac_secret=self.SECRET,
            max_age_seconds=0,
        )

    def test_missing_timestamp_when_required_raises(self):
        sig = _sign(self.BODY, self.SECRET)
        with pytest.raises(WebhookError, match="X-Ownevo-Timestamp"):
            validate_webhook_signature(
                body=self.BODY,
                signature_header_value=sig,
                timestamp_header_value=None,
                hmac_secret=self.SECRET,
                max_age_seconds=300,
            )

    def test_bare_hex_signature_accepted(self):
        """Some providers omit the 'sha256=' prefix."""
        digest = hmac.new(
            self.SECRET.encode(), self.BODY, hashlib.sha256
        ).hexdigest()
        ts = str(int(time.time()))
        validate_webhook_signature(
            body=self.BODY,
            signature_header_value=digest,  # bare hex, no prefix
            timestamp_header_value=ts,
            hmac_secret=self.SECRET,
        )

    def test_custom_header_name_in_error_message(self):
        with pytest.raises(WebhookError) as exc_info:
            validate_webhook_signature(
                body=self.BODY,
                signature_header_value=None,
                timestamp_header_value=None,
                hmac_secret=self.SECRET,
                signature_header_name="X-Hub-Signature-256",
                max_age_seconds=0,
            )
        assert "X-Hub-Signature-256" in str(exc_info.value)

    def test_now_override_allows_frozen_time_test(self):
        """Using `now` override we can test the replay window precisely."""
        now = 1_000_000.0
        sig = _sign(self.BODY, self.SECRET)
        # Timestamp is exactly at the edge of the window — should pass.
        ts = str(int(now - 300))
        validate_webhook_signature(
            body=self.BODY,
            signature_header_value=sig,
            timestamp_header_value=ts,
            hmac_secret=self.SECRET,
            max_age_seconds=300,
            now=now,
        )
        # One second over the window — should fail.
        ts_expired = str(int(now - 301))
        with pytest.raises(WebhookError, match="old"):
            validate_webhook_signature(
                body=self.BODY,
                signature_header_value=sig,
                timestamp_header_value=ts_expired,
                hmac_secret=self.SECRET,
                max_age_seconds=300,
                now=now,
            )
