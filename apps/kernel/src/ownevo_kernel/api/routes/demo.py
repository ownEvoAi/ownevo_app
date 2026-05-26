"""`/api/demo/*` — Phase 1 demo helper routes.

Two endpoints:

  * ``GET /api/demo/status`` — returns the visitor's current tier,
    quota usage, reset time, and the global ``demo_budget_state``
    snapshot. The web app reads this server-side to render the
    quota-gated CTAs as disabled (with a tooltip) before the visitor
    clicks. Cheap, idempotent, no-LLM.

  * ``POST /api/demo/redeem-invite`` — accepts ``{token}`` in the
    body. Validates the JWT against ``OWNEVO_DEMO_SIGNING_KEY`` and
    the revocation denylist. On success, sets the
    ``ownevo_demo_invite`` cookie HttpOnly/SameSite=Lax. On failure,
    returns 400 with a structured ``code``.

Both routes are no-ops outside ``DEMO_MODE=true`` (status returns a
flat "not_demo" envelope; redeem returns 404). The kernel never
exposes the signing key.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .._demo_budget import get_budget_status
from .._demo_identity import (
    DEMO_INVITE_COOKIE,
    SIGNING_KEY_ENV,
    InviteInvalid,
    _is_revoked,
    resolve_demo_identity,
    verify_invite_token,
)
from .._demo_quota import get_quota_status
from ..deps import ConnDep, is_demo_mode

router = APIRouter(prefix="/api/demo", tags=["demo"])

# NOTE: these routes use ConnDep, which now requires an authenticated principal
# (bearer assertion or OWNEVO_DEV_AUTH=true). Public demo visitors are
# anonymous and won't carry a bearer token. In any deployment where dev-auth
# is disabled, these routes return 401 to unauthenticated visitors.
#
# Follow-up (web-side auth wiring): the demo flow should either
# (a) issue a short-lived signed assertion for the demo workspace, or
# (b) switch to a connection path that skips the membership gate for the
#     demo-only global tables (demo_budget, demo_identity, demo_quota),
#     which don't require workspace binding.

# Match the anonymous-cookie lifetime in _demo_identity.py.
_INVITE_COOKIE_MAX_AGE = 365 * 86400


class DemoStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    demo_mode: bool
    tier: str | None = None
    label: str | None = None
    used_tokens: int = 0
    limit_tokens: int | None = None
    exhausted: bool = False
    budget_exhausted: bool = False
    reset_at: str | None = None
    invite_exp: int | None = None


class RedeemInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=8, max_length=2048)


@router.get("/status", response_model=DemoStatusResponse)
async def get_demo_status(
    request: Request,
    response: Response,
    conn: ConnDep,
) -> DemoStatusResponse:
    """Snapshot of the visitor's demo state.

    Drives the header status pill and the server-rendered disabled state
    on quota-gated CTAs. Recording usage here would be wrong — this is a
    read.
    """
    if not is_demo_mode():
        return DemoStatusResponse(demo_mode=False)
    identity = await resolve_demo_identity(request, response, conn)
    budget = await get_budget_status(conn)
    quota = await get_quota_status(conn, identity)
    return DemoStatusResponse(
        demo_mode=True,
        tier=identity.tier,
        label=identity.label,
        used_tokens=quota.used,
        limit_tokens=quota.limit,
        exhausted=quota.exhausted,
        budget_exhausted=budget.exhausted,
        reset_at=quota.reset_at.isoformat(),
        invite_exp=identity.invite_exp,
    )


@router.post("/redeem-invite", status_code=status.HTTP_204_NO_CONTENT)
async def redeem_invite(
    body: RedeemInviteRequest,
    request: Request,
    response: Response,
    conn: ConnDep,
) -> Response:
    """Validate an invite token and set the ``ownevo_demo_invite`` cookie.

    Validation order:
      1. DEMO_MODE guard (404 if off).
      2. Signing key present (503 if misconfigured).
      3. HMAC signature + expiry (400 invite_invalid if bad).
      4. Revocation denylist (400 invite_revoked if revoked).

    On success: 204 with Set-Cookie ``ownevo_demo_invite``.
    """
    if not is_demo_mode():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo invites are only redeemable on the live demo",
        )
    signing_key = os.environ.get(SIGNING_KEY_ENV)
    if not signing_key:
        # Misconfiguration on the server, not a client error.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "signing_key_not_configured",
                "message": "Demo signing key is not configured on the kernel",
            },
        )
    try:
        claims = verify_invite_token(body.token, signing_key)
    except InviteInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invite_invalid", "reason": str(exc)},
        ) from exc

    jti = str(claims["jti"])
    if await _is_revoked(conn, jti):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invite_revoked", "reason": "This invite has been revoked."},
        )

    # 204 with Set-Cookie. The cookie holds the token verbatim — the
    # kernel re-verifies on every quota-gated request.
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    out.set_cookie(
        DEMO_INVITE_COOKIE,
        body.token,
        max_age=_INVITE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return out


__all__ = [
    "DemoStatusResponse",
    "RedeemInviteRequest",
    "get_demo_status",
    "redeem_invite",
    "router",
]
