"""Workspace invite signing primitives.

A workspace invite is a signed token that lets the holder add themselves to a
workspace's member list. The token format reuses the kernel's HMAC primitive
(``_signing``) and the same ``OWNEVO_INTERNAL_AUTH_KEY`` shared with the web
edge for workspace assertions. Different ``kind`` claims keep the two token
shapes distinct: an invite cannot be presented as a workspace assertion (it
has no ``u``/``w``/``e`` triplet) and vice versa.

Claim shape (short keys keep the URL compact):

    payload = {"k": "inv", "i": <invite_id>, "e": <exp epoch seconds>}
    token   = base64url(payload) + "." + base64url(hmac_sha256(payload, KEY))

The invite ``id`` is a UUID issued at mint time. State (redeemed / revoked)
lives in the ``workspace_invites`` row keyed by that id; the token itself
carries no state, so revocation requires a row lookup at redeem time.
"""

from __future__ import annotations

import json
import time

from ._signing import b64url_decode, b64url_encode, sign, verify_sig

# Kind discriminator. Refuses any other value at verify time so a future
# workspace-assertion claim shape (without ``u``/``w``/``e``) cannot be reused
# as an invite token and vice versa.
INVITE_KIND = "inv"


class InviteTokenInvalid(ValueError):
    """Raised when an invite token fails any validation step."""


def mint_invite_token(
    *,
    invite_id: str,
    ttl_seconds: int,
    signing_key: str,
    issued_at: int | None = None,
) -> str:
    """Mint a signed invite token addressing the ``workspace_invites`` row id.

    ``ttl_seconds`` should match the row's ``expires_at`` so the token expires
    in lockstep with the DB-stored expiry; both checks run at redemption.
    """
    if not invite_id:
        raise ValueError("invite_id is required")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    if not signing_key:
        raise ValueError("signing_key is required")
    iat = issued_at if issued_at is not None else int(time.time())
    claims = {"e": iat + ttl_seconds, "i": invite_id, "k": INVITE_KIND}
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    payload_s = b64url_encode(payload)
    return f"{payload_s}.{sign(payload_s, signing_key)}"


def verify_invite_token(
    token: str, signing_key: str, *, check_expiry: bool = True
) -> str:
    """Return the invite id if the token is well-formed, signed, and (optionally) unexpired.

    Raises ``InviteTokenInvalid`` otherwise. Callers must still look the id up
    in ``workspace_invites`` to apply state checks (revoked, redeemed, row
    expiry).

    Pass ``check_expiry=False`` when the caller will determine expiry from the
    database row rather than the token claim — this lets the preview endpoint
    return a structured ``expired`` status with workspace/inviter metadata
    instead of a generic invalid-token error.
    """
    parts = token.split(".")
    if len(parts) != 2:
        raise InviteTokenInvalid("malformed token")
    payload_s, sig_s = parts
    if not verify_sig(payload_s, sig_s, signing_key):
        raise InviteTokenInvalid("bad signature")
    try:
        raw = json.loads(b64url_decode(payload_s))
    except Exception as exc:
        raise InviteTokenInvalid("malformed payload") from exc
    if not isinstance(raw, dict):
        raise InviteTokenInvalid("payload is not an object")
    for claim in ("k", "i", "e"):
        if claim not in raw:
            raise InviteTokenInvalid(f"missing claim: {claim}")
    if raw["k"] != INVITE_KIND:
        raise InviteTokenInvalid(f"wrong token kind: {raw['k']!r}")
    if check_expiry and (not isinstance(raw["e"], int) or raw["e"] < int(time.time())):
        raise InviteTokenInvalid("expired")
    invite_id = raw["i"]
    if not isinstance(invite_id, str) or not invite_id.strip():
        raise InviteTokenInvalid("i must be a non-empty string")
    return invite_id
