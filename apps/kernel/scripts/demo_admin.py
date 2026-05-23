"""Operator CLI for the Phase 1 demo: revocation + budget cap flip.

Subcommands:

  * ``revoke --jti <jti> [--reason <text>]`` — denylist a specific invite
    token. Idempotent.
  * ``budget-cap [--note <text>]`` — flip today's
    ``demo_budget_state`` row to ``exhausted``. Used when the operator
    decides the demo should pause (cost watchdog, abuse signal).
  * ``budget-clear`` — flip today's row back to ``available``.

All subcommands write to the same Postgres instance the kernel reads
from. Run via the Make targets (``make demo-revoke``,
``make demo-budget-cap``, ``make demo-budget-clear``) which handle the
``OWNEVO_DATABASE_URL`` plumbing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg
from ownevo_kernel.api._demo_budget import set_budget_status
from ownevo_kernel.db import ENV_VAR


async def _connect() -> asyncpg.Connection:
    db_url = os.environ.get(ENV_VAR)
    if not db_url:
        print(f"error: {ENV_VAR} is not set", file=sys.stderr)
        raise SystemExit(2)
    return await asyncpg.connect(db_url)


async def _revoke(jti: str, label: str | None, reason: str | None) -> None:
    conn = await _connect()
    try:
        await conn.execute(
            """
            INSERT INTO demo_invite_revocations(jti, label, reason)
            VALUES ($1, $2, $3)
            ON CONFLICT (jti) DO UPDATE
            SET reason = EXCLUDED.reason,
                label = COALESCE(EXCLUDED.label, demo_invite_revocations.label)
            """,
            jti,
            label,
            reason,
        )
        print(f"revoked: {jti}")
    finally:
        await conn.close()


async def _budget(*, exhausted: bool, note: str | None) -> None:
    conn = await _connect()
    try:
        await set_budget_status(conn, exhausted=exhausted, note=note)
        word = "exhausted" if exhausted else "available"
        print(f"demo_budget_state: today set to {word}")
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    rev = sub.add_parser("revoke", help="Denylist an invite JTI.")
    rev.add_argument("--jti", required=True)
    rev.add_argument("--label")
    rev.add_argument("--reason")

    cap = sub.add_parser("budget-cap", help="Flip today's budget to exhausted.")
    cap.add_argument("--note")

    sub.add_parser("budget-clear", help="Flip today's budget back to available.")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.cmd == "revoke":
        asyncio.run(_revoke(args.jti, args.label, args.reason))
    elif args.cmd == "budget-cap":
        asyncio.run(_budget(exhausted=True, note=args.note))
    elif args.cmd == "budget-clear":
        asyncio.run(_budget(exhausted=False, note=None))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
