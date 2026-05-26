"""HMAC token signing primitive shared across the kernel's signed tokens.

The format is a stripped-down HS256 JWT with no header:
``base64url(payload).base64url(hmac_sha256(payload))``. Minting and
verification happen in the same trust domain (the kernel, and the web app
that shares the signing key), so RFC 7519 interop is not required and the
dependency surface stays stdlib-only.

Two callers share this primitive:

  * ``_demo_identity`` — signed demo invite tokens.
  * ``_internal_auth`` — the web→kernel identity assertion.

Only the encode / decode / sign helpers live here; each caller keeps its
own claim schema and validation rules.
"""

from __future__ import annotations

import base64
import hmac
from hashlib import sha256


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign(payload: str, key: str) -> str:
    """Return the base64url HMAC-SHA256 signature of ``payload`` under ``key``."""
    sig = hmac.new(key.encode(), payload.encode(), sha256).digest()
    return b64url_encode(sig)


def verify_sig(payload: str, sig: str, key: str) -> bool:
    """Timing-safe check that ``sig`` is a valid signature of ``payload``.

    Returns False (never raises) for a malformed signature so callers can
    treat "tampered" and "structurally broken" uniformly.
    """
    expected = hmac.new(key.encode(), payload.encode(), sha256).digest()
    try:
        actual = b64url_decode(sig)
    except Exception:
        return False
    return hmac.compare_digest(expected, actual)
