"""HMAC-signed webhook receiver (Track 17.1.1).

Validates inbound webhook payloads from any external system that signs
its requests with HMAC-SHA256.  The signature format follows the GitHub /
Stripe / Twilio convention::

    X-Ownevo-Signature: sha256=<hex-digest>

The expected HMAC is computed over the raw request body with the
per-trigger secret (stored encrypted in `trigger_definitions.config`).

Replay-attack protection: when `WebhookConfig.max_age_seconds > 0`, the
route also reads ``X-Ownevo-Timestamp`` (Unix seconds) and rejects
requests whose timestamp is outside the tolerance window.  Providers that
embed the timestamp in the signed body (e.g. Stripe) should set
`max_age_seconds=0` and validate the timestamp separately.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

_log = logging.getLogger(__name__)

# Header names used by the webhook receiver.
SIGNATURE_HEADER_DEFAULT = "X-Ownevo-Signature"
TIMESTAMP_HEADER = "X-Ownevo-Timestamp"


class WebhookError(Exception):
    """Raised when a webhook request fails validation."""

    def __init__(self, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.http_status = http_status


def _compute_hmac(secret: str, body: bytes) -> str:
    """Compute the HMAC-SHA256 hex digest for *body* using *secret*."""
    return hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def _parse_signature_header(header_value: str) -> str:
    """Extract the hex digest from ``sha256=<hex>`` or bare hex strings.

    Raises `WebhookError` when the format is unrecognised.
    """
    if header_value.startswith("sha256="):
        return header_value[7:]
    # Some providers omit the algorithm prefix; accept bare 64-char hex.
    if len(header_value) == 64 and all(c in "0123456789abcdefABCDEF" for c in header_value):
        return header_value.lower()
    raise WebhookError(
        f"Unrecognised signature format: {header_value!r}. "
        "Expected 'sha256=<hex>' or a bare 64-character hex string."
    )


def validate_webhook_signature(
    *,
    body: bytes,
    signature_header_value: str | None,
    timestamp_header_value: str | None,
    hmac_secret: str,
    signature_header_name: str = SIGNATURE_HEADER_DEFAULT,
    max_age_seconds: int = 300,
    now: float | None = None,
) -> None:
    """Validate the HMAC signature and optional replay-attack timestamp.

    Args:
        body: Raw request body bytes.
        signature_header_value: Value of the signature header, e.g.
            ``"sha256=abcdef..."``.  If None, raises `WebhookError`.
        timestamp_header_value: Value of ``X-Ownevo-Timestamp`` (Unix
            seconds as a string).  Only checked when `max_age_seconds > 0`.
        hmac_secret: Per-trigger secret (plaintext; decrypted before call).
        signature_header_name: Header name, for error messages only.
        max_age_seconds: Reject requests older than this value.
            Set to 0 to skip timestamp validation.
        now: Override the current time (monotonic seconds); used in tests.

    Raises:
        WebhookError: When the signature is missing, incorrect, or the
            request timestamp is outside the replay window.
    """
    if not signature_header_value:
        raise WebhookError(
            f"Missing {signature_header_name} header.",
            http_status=401,
        )

    # Timestamp check first — cheap, doesn't touch HMAC.
    if max_age_seconds > 0:
        if not timestamp_header_value:
            raise WebhookError(
                f"Missing {TIMESTAMP_HEADER} header. "
                "Set max_age_seconds=0 to disable replay-attack protection.",
                http_status=401,
            )
        try:
            ts = float(timestamp_header_value)
        except ValueError:
            raise WebhookError(
                f"Invalid {TIMESTAMP_HEADER}: {timestamp_header_value!r} is not a number.",
                http_status=400,
            ) from None
        current = now if now is not None else time.time()
        age = abs(current - ts)
        if age > max_age_seconds:
            raise WebhookError(
                f"Request timestamp is {age:.0f}s old; replay window is "
                f"{max_age_seconds}s.  Regenerate the request.",
                http_status=401,
            )

    # HMAC validation — constant-time comparison.
    try:
        received_hex = _parse_signature_header(signature_header_value)
    except WebhookError:
        raise

    expected_hex = _compute_hmac(hmac_secret, body)
    if not hmac.compare_digest(received_hex.lower(), expected_hex.lower()):
        raise WebhookError(
            "HMAC signature mismatch — invalid secret or tampered payload.",
            http_status=401,
        )

    _log.debug("webhook: HMAC validation passed")
