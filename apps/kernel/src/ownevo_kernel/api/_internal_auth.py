"""Web→kernel identity assertion and the resolved request principal.

The kernel is a backend service: end users authenticate at the web edge
(Auth.js), and the web app calls the kernel on their behalf carrying a
short-lived signed assertion of the authenticated principal and the active
workspace. The kernel verifies the assertion and trusts ``(user_id,
workspace_id)`` — it never sees the upstream Google/OIDC tokens. See
``docs/AUTH.md``.

The assertion reuses the shared HMAC token format (``_signing``):

    payload = {"u": <user_id>, "w": <workspace_id>, "e": <exp epoch seconds>}
    token   = base64url(payload) + "." + base64url(hmac_sha256(payload, KEY))

``KEY`` is ``OWNEVO_INTERNAL_AUTH_KEY``, a secret shared only between the web
app and the kernel. The mint side runs in the web app (TypeScript) against
the same format; ``mint_workspace_assertion`` here is the Python counterpart
used by tests and any kernel-side tooling.

Local development and tests run with ``OWNEVO_DEV_AUTH=true``: a request with
no assertion resolves to a seeded dev principal in the default workspace, so
``make api`` and the test suite need no real sign-in. The fallback is refused
unless the flag is explicitly true, so a misconfigured production deployment
fails closed (401) rather than granting anonymous default-workspace access.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from dataclasses import dataclass

from fastapi import Request

from ..tenant_session import DEFAULT_WORKSPACE_ID
from ._signing import b64url_decode, b64url_encode, sign, verify_sig

# Shared secret between the web app and the kernel. Required to verify any
# bearer assertion; absent in pure dev-auth local runs.
INTERNAL_AUTH_KEY_ENV = "OWNEVO_INTERNAL_AUTH_KEY"

# When explicitly "true", an assertion-less request resolves to the dev
# principal below. Anything else (unset, "false", "0") disables the fallback.
DEV_AUTH_ENV = "OWNEVO_DEV_AUTH"

# The seeded local principal (migration 0035 makes it owner of 'default').
DEV_USER_ID = "dev-user"


class AssertionInvalid(ValueError):
    """Raised when an identity assertion fails any validation step."""


@dataclass(frozen=True)
class Principal:
    """The authenticated caller and the workspace the request operates in."""

    user_id: str
    workspace_id: str


def dev_auth_enabled() -> bool:
    """True only when ``OWNEVO_DEV_AUTH`` is explicitly the string ``true``."""
    return os.environ.get(DEV_AUTH_ENV, "").lower() == "true"


def bearer_token(request: Request) -> str | None:
    """Extract the bearer token from the Authorization header, or None."""
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def mint_workspace_assertion(
    *,
    user_id: str,
    workspace_id: str,
    ttl_seconds: int,
    signing_key: str,
    issued_at: int | None = None,
) -> str:
    """Mint a signed identity assertion. The web app's TS minter mirrors this."""
    if not user_id or not workspace_id:
        raise ValueError("user_id and workspace_id are required")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    iat = issued_at if issued_at is not None else int(time.time())
    claims = {"u": user_id, "w": workspace_id, "e": iat + ttl_seconds}
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    payload_s = b64url_encode(payload)
    return f"{payload_s}.{sign(payload_s, signing_key)}"


def verify_workspace_assertion(token: str, signing_key: str) -> Principal:
    """Return the Principal if the assertion is well-formed, signed, and unexpired.

    Raises AssertionInvalid otherwise.
    """
    parts = token.split(".")
    if len(parts) != 2:
        raise AssertionInvalid("malformed token")
    payload_s, sig_s = parts
    if not verify_sig(payload_s, sig_s, signing_key):
        raise AssertionInvalid("bad signature")
    try:
        raw = json.loads(b64url_decode(payload_s))
    except Exception as exc:
        raise AssertionInvalid("malformed payload") from exc
    if not isinstance(raw, dict):
        raise AssertionInvalid("payload is not an object")
    for claim in ("u", "w", "e"):
        if claim not in raw:
            raise AssertionInvalid(f"missing claim: {claim}")
    if not isinstance(raw["e"], int) or raw["e"] < int(time.time()):
        raise AssertionInvalid("expired")
    user_id, workspace_id = raw["u"], raw["w"]
    if not isinstance(user_id, str) or not isinstance(workspace_id, str):
        raise AssertionInvalid("u and w must be strings")
    if not user_id.strip() or not workspace_id.strip():
        raise AssertionInvalid("u and w must be non-empty")
    return Principal(user_id=user_id, workspace_id=workspace_id)


def dev_principal() -> Principal:
    """The principal an assertion-less request resolves to under dev-auth."""
    return Principal(user_id=DEV_USER_ID, workspace_id=DEFAULT_WORKSPACE_ID)


def require_service_token(request: Request, detail: str = "invalid internal service token") -> None:
    """Raise HTTPException if the request's bearer token does not match the
    shared ``OWNEVO_INTERNAL_AUTH_KEY``.

    Used by all internal service endpoints (auth-sync, workspace provisioning)
    so the check stays in one place as the endpoint list grows.
    """
    from fastapi import HTTPException, status  # local import avoids circular dep

    key = os.environ.get(INTERNAL_AUTH_KEY_ENV)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal endpoint is not configured",
        )
    if not verify_internal_service_token(bearer_token(request), key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
        )


def verify_internal_service_token(token: str | None, service_key: str | None) -> bool:
    """Constant-time check that ``token`` matches the shared service key.

    Authenticates trusted web→kernel service calls (e.g. the auth-sync
    endpoint) that run *before* any workspace is bound, so they cannot present
    a workspace assertion: the caller has no membership to assert yet. The web
    app presents ``OWNEVO_INTERNAL_AUTH_KEY`` directly as a bearer token.
    Returns False when either side is missing rather than raising.
    """
    if not token or not service_key:
        return False
    return hmac.compare_digest(token, service_key)
