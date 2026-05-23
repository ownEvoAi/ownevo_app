"""Mint a Phase 1 demo invite link.

Usage:

    OWNEVO_DEMO_SIGNING_KEY=... \\
    uv run --package ownevo-kernel --extra api python apps/kernel/scripts/mint_demo_invite.py \\
        --label "demand-vp-acme" --tier unlimited --days 60 \\
        --base-url https://demo.ownevo.ai

Prints the redeem URL on stdout. Send it to the recipient via whatever
channel makes sense (Slack DM, email, application form). Each link is
per-recipient so individual revocation works via ``make demo-revoke``.

The ``label`` is recorded into the JWT claims and surfaced in
``GET /api/demo/status`` so the founder can see who redeemed which link
without storing PII.
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import urlencode

from ownevo_kernel.api._demo_identity import (
    SIGNING_KEY_ENV,
    mint_invite_token,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Human-readable invite label.")
    parser.add_argument(
        "--tier",
        choices=("elevated", "unlimited"),
        default="elevated",
        help="Invite tier. 'elevated' gets ~10x anon cap, 'unlimited' skips the cap.",
    )
    parser.add_argument("--days", type=int, required=True, help="TTL in days.")
    parser.add_argument(
        "--base-url",
        default="https://demo.ownevo.ai",
        help="Demo origin used to build the redeem URL.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        token = mint_invite_token(
            label=args.label,
            tier=args.tier,  # type: ignore[arg-type]
            ttl_days=args.days,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            f"hint: export {SIGNING_KEY_ENV}=$(openssl rand -hex 32) and store it"
            " in 1Password before minting.",
            file=sys.stderr,
        )
        return 2
    qs = urlencode({"invite": token})
    print(f"{args.base_url}/?{qs}")
    print(
        f"\n  label={args.label}  tier={args.tier}  ttl={args.days}d",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
