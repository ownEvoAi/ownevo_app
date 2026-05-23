"""Demo quota: per-identity daily token counter.

Tracks ``input_tokens + output_tokens`` per ``(identity_key, day)``.
``unlimited`` tier skips the cap; ``anonymous`` and ``elevated`` enforce
the env-configured limit.

The check is best-effort: pre-flight uses a worst-case estimate or a
zero-cost peek; the real usage from ``messages.create`` is recorded
post-call. A visitor can briefly exceed quota by a single call, which is
acceptable for Phase 1.

Env config:

  * ``OWNEVO_DEMO_ANON_TOKENS_PER_DAY`` (default 60_000)
  * ``OWNEVO_DEMO_ELEVATED_TOKENS_PER_DAY`` (default 600_000)
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

import asyncpg
from fastapi import HTTPException, status

from ._demo_identity import DemoIdentity, utc_today

DEFAULT_ANON_LIMIT = 60_000
DEFAULT_ELEVATED_LIMIT = 600_000


def limit_for_tier(tier: str) -> int | None:
    """Return the daily token cap for a tier (``None`` = unlimited)."""
    if tier == "unlimited":
        return None
    if tier == "elevated":
        try:
            return int(
                os.environ.get(
                    "OWNEVO_DEMO_ELEVATED_TOKENS_PER_DAY",
                    DEFAULT_ELEVATED_LIMIT,
                )
            )
        except ValueError:
            return DEFAULT_ELEVATED_LIMIT
    try:
        return int(
            os.environ.get(
                "OWNEVO_DEMO_ANON_TOKENS_PER_DAY",
                DEFAULT_ANON_LIMIT,
            )
        )
    except ValueError:
        return DEFAULT_ANON_LIMIT


@dataclass(frozen=True)
class QuotaStatus:
    used: int
    limit: int | None  # ``None`` = unlimited
    exhausted: bool
    reset_at: dt.datetime  # UTC midnight tomorrow


async def get_quota_status(
    conn: asyncpg.Connection,
    identity: DemoIdentity,
) -> QuotaStatus:
    today = utc_today()
    used = await conn.fetchval(
        """
        SELECT COALESCE(input_tokens + output_tokens, 0)
        FROM demo_usage
        WHERE identity_key = $1 AND day = $2
        """,
        identity.identity_key,
        today,
    )
    used_int = int(used or 0)
    limit = limit_for_tier(identity.tier)
    reset_at = dt.datetime.combine(
        today + dt.timedelta(days=1),
        dt.time(0, 0, tzinfo=dt.UTC),
    )
    exhausted = limit is not None and used_int >= limit
    return QuotaStatus(
        used=used_int,
        limit=limit,
        exhausted=exhausted,
        reset_at=reset_at,
    )


def raise_quota_exhausted(quota: QuotaStatus, identity: DemoIdentity) -> None:
    """Raise 429 with a structured body describing the gate."""
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "demo_quota_exhausted",
            "tier": identity.tier,
            "used": quota.used,
            "limit": quota.limit,
            "reset_at": quota.reset_at.isoformat(),
        },
    )


async def record_usage(
    conn: asyncpg.Connection,
    identity: DemoIdentity,
    *,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Add tokens to the visitor's daily counter (creates the row if absent).

    ``unlimited`` rows are still written so the operator dashboard can
    see who used what; only the cap check is skipped.
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return
    today = utc_today()
    await conn.execute(
        """
        INSERT INTO demo_usage(identity_key, day, input_tokens, output_tokens, tier)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (identity_key, day) DO UPDATE
        SET input_tokens = demo_usage.input_tokens + EXCLUDED.input_tokens,
            output_tokens = demo_usage.output_tokens + EXCLUDED.output_tokens,
            tier = EXCLUDED.tier,
            updated_at = NOW()
        """,
        identity.identity_key,
        today,
        max(0, int(input_tokens)),
        max(0, int(output_tokens)),
        identity.tier,
    )
