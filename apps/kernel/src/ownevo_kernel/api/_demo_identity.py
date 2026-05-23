"""Demo identity: signed-invite tokens + anonymous cookie resolver.

Phase 1 demo uses two cookies:

  * ``ownevo_demo_id`` — opaque random token minted server-side on first
    visit. Drives the anonymous ``identity_key`` for quota accounting.

  * ``ownevo_demo_invite`` — HMAC-signed payload carrying
    ``{label, tier, exp, jti}``. Set via the ``?invite=<token>`` query
    param (handled by the Next.js middleware) or directly by the
    ``/api/demo/redeem-invite`` route. Validates against
    ``OWNEVO_DEMO_SIGNING_KEY`` and the ``demo_invite_revocations``
    table.

The signing format is a stripped-down HS256 JWT (no header — just
``base64url(payload).base64url(hmac_sha256(payload))``). Mint and verify
happen in the same process; RFC 7519 interop is not required, so we keep
the dependency surface to stdlib only.
"""

from __future__ import annotations

import base64
import datetime as dt
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Literal

_log = logging.getLogger(__name__)

import asyncpg
from fastapi import Depends, Request, Response

from .deps import ConnDep

DemoTier = Literal["anonymous", "elevated", "unlimited"]

DEMO_ID_COOKIE = "ownevo_demo_id"
DEMO_INVITE_COOKIE = "ownevo_demo_invite"
SIGNING_KEY_ENV = "OWNEVO_DEMO_SIGNING_KEY"

# Cookie lifetime for the anonymous identity. One year is plenty — the
# quota row is per (identity, day), so a stable cookie just means the
# same visitor lands on the same daily counter.
_ANON_COOKIE_MAX_AGE = 365 * 86400


@dataclass(frozen=True)
class DemoIdentity:
    """Resolved demo visitor identity.

    ``identity_key`` is the quota-table primary key. It is either
    ``"c:<cookie>"`` for anonymous visitors or ``"inv:<jti>"`` for
    invite-redeemed visitors.
    """

    identity_key: str
    tier: DemoTier
    label: str | None
    invite_jti: str | None
    invite_exp: int | None


class InviteInvalid(ValueError):
    """Raised when an invite token fails any validation step."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload: str, key: str) -> str:
    sig = hmac.new(key.encode(), payload.encode(), sha256).digest()
    return _b64url_encode(sig)


def mint_invite_token(
    *,
    label: str,
    tier: DemoTier,
    ttl_days: int,
    signing_key: str | None = None,
    issued_at: int | None = None,
) -> str:
    """Mint a signed invite token. Caller is responsible for delivery."""
    if tier not in ("elevated", "unlimited"):
        raise ValueError(f"invite tier must be elevated|unlimited, got {tier!r}")
    if ttl_days <= 0:
        raise ValueError("ttl_days must be positive")
    key = signing_key or os.environ.get(SIGNING_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{SIGNING_KEY_ENV} is not set; refusing to mint an unsigned invite",
        )
    iat = issued_at if issued_at is not None else int(time.time())
    claims = {
        "label": label,
        "tier": tier,
        "iat": iat,
        "exp": iat + ttl_days * 86400,
        "jti": secrets.token_urlsafe(16),
    }
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    payload_s = _b64url_encode(payload)
    return f"{payload_s}.{_sign(payload_s, key)}"


def verify_invite_token(token: str, signing_key: str) -> dict[str, object]:
    """Return claims if the token is well-formed, signed, and unexpired."""
    parts = token.split(".")
    if len(parts) != 2:
        raise InviteInvalid("malformed token")
    payload_s, sig_s = parts
    try:
        expected = hmac.new(signing_key.encode(), payload_s.encode(), sha256).digest()
        actual = _b64url_decode(sig_s)
    except Exception as exc:  # pragma: no cover — malformed base64
        raise InviteInvalid("malformed token") from exc
    if not hmac.compare_digest(expected, actual):
        raise InviteInvalid("bad signature")
    try:
        claims = json.loads(_b64url_decode(payload_s))
    except Exception as exc:
        raise InviteInvalid("malformed payload") from exc
    if not isinstance(claims, dict):
        raise InviteInvalid("payload is not an object")
    for k in ("label", "tier", "exp", "jti"):
        if k not in claims:
            raise InviteInvalid(f"missing claim: {k}")
    if claims["tier"] not in ("elevated", "unlimited"):
        raise InviteInvalid(f"bad tier: {claims['tier']!r}")
    if not isinstance(claims["exp"], int) or claims["exp"] < int(time.time()):
        raise InviteInvalid("expired")
    return claims


async def _is_revoked(conn: asyncpg.Connection, jti: str) -> bool:
    row = await conn.fetchval(
        "SELECT 1 FROM demo_invite_revocations WHERE jti = $1",
        jti,
    )
    return row is not None


def _set_anon_cookie(response: Response, value: str, *, secure: bool) -> None:
    response.set_cookie(
        DEMO_ID_COOKIE,
        value,
        max_age=_ANON_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _clear_invite_cookie(response: Response, *, secure: bool) -> None:
    # Pass the same Secure flag used when the cookie was set so browsers
    # recognise the deletion directive. Mismatch causes some browsers to
    # silently ignore the delete, leaving a revoked token live in the jar.
    response.delete_cookie(DEMO_INVITE_COOKIE, path="/", secure=secure)


async def resolve_demo_identity(
    request: Request,
    response: Response,
    conn: ConnDep,
) -> DemoIdentity:
    """Resolve the visitor's tier + identity_key for quota accounting.

    Order of precedence:

      1. Valid ``ownevo_demo_invite`` cookie whose ``jti`` is not in
         ``demo_invite_revocations`` → ``elevated``/``unlimited``.
      2. ``ownevo_demo_id`` cookie present → ``anonymous``.
      3. Neither → mint a fresh ``ownevo_demo_id`` → ``anonymous``.
    """
    signing_key = os.environ.get(SIGNING_KEY_ENV)
    invite_token = request.cookies.get(DEMO_INVITE_COOKIE)
    if invite_token and not signing_key:
        _log.error(
            "%s not set; invite cookie present but cannot be verified — "
            "all visitors will be treated as anonymous",
            SIGNING_KEY_ENV,
        )
    if invite_token and signing_key:
        try:
            claims = verify_invite_token(invite_token, signing_key)
        except InviteInvalid:
            _clear_invite_cookie(response, secure=request.url.scheme == "https")
        else:
            jti = str(claims["jti"])
            if not await _is_revoked(conn, jti):
                tier = claims["tier"]
                if tier not in ("elevated", "unlimited"):
                    # verify_invite_token already validates this; reaching here
                    # would indicate a logic bug, not a client error.
                    raise InviteInvalid(f"bad tier: {tier!r}")
                return DemoIdentity(
                    identity_key=f"inv:{jti}",
                    tier=tier,  # type: ignore[arg-type]
                    label=str(claims["label"]),
                    invite_jti=jti,
                    invite_exp=int(claims["exp"]),
                )
            _clear_invite_cookie(response, secure=request.url.scheme == "https")

    cookie_id = request.cookies.get(DEMO_ID_COOKIE)
    # Reject browser-supplied values that exceed the expected token length.
    # Server-generated tokens from secrets.token_urlsafe(24) are 32 chars;
    # 256 is a generous cap that still prevents unbounded primary-key growth
    # in demo_usage under adversarial traffic.
    if not cookie_id or len(cookie_id) > 256:
        cookie_id = secrets.token_urlsafe(24)
        _set_anon_cookie(response, cookie_id, secure=request.url.scheme == "https")
    return DemoIdentity(
        identity_key=f"c:{cookie_id}",
        tier="anonymous",
        label=None,
        invite_jti=None,
        invite_exp=None,
    )


def hash_client_ip(request: Request) -> str:
    """Return a stable hashed IP key for the parallel anti-abuse ceiling.

    The hash is keyed by the signing key (if set) so the hashes are
    deterministic across process restarts but not portable to other
    deployments. Falls back to an empty key if the secret is missing,
    which is fine for local development.
    """
    forwarded = request.headers.get("x-forwarded-for") or ""
    ip = (forwarded.split(",")[0].strip() if forwarded else "") or (
        request.client.host if request.client else ""
    )
    key = os.environ.get(SIGNING_KEY_ENV, "")
    return _b64url_encode(hmac.new(key.encode(), ip.encode(), sha256).digest())


def utc_today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


# FastAPI dependency alias for routes that want only the identity (no
# gate). Most routes will use ``DemoGateDep`` in ``_demo_gate.py`` instead.
DemoIdentityDep = Annotated[DemoIdentity, Depends(resolve_demo_identity)]
