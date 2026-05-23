"""Demo global budget: the soft gate on top of the Anthropic console cap.

Anthropic does not expose a real-time spend signal, so the kernel cannot
poll for "are we at the daily limit." Instead, an external Make target
(or daily cron) flips the ``demo_budget_state(day, status)`` row to
``'exhausted'`` when the operator decides the demo should pause.

Every quota-gated route reads this state and returns 502 if exhausted.
The Anthropic console hard cap remains the actual ceiling — this table
just lets the kernel pre-empt the upstream 429 with a friendlier
visitor-facing message.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from fastapi import HTTPException, status

from ._demo_identity import utc_today


@dataclass(frozen=True)
class BudgetStatus:
    exhausted: bool


async def get_budget_status(conn: asyncpg.Connection) -> BudgetStatus:
    row = await conn.fetchval(
        "SELECT status FROM demo_budget_state WHERE day = $1",
        utc_today(),
    )
    return BudgetStatus(exhausted=(row == "exhausted"))


def raise_budget_exhausted() -> None:
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "demo_budget_cap_reached",
            "message": "Today's demo budget is exhausted — back tomorrow.",
        },
    )


async def set_budget_status(
    conn: asyncpg.Connection,
    *,
    exhausted: bool,
    note: str | None = None,
) -> None:
    """Idempotently set today's row to ``exhausted`` or ``available``."""
    new_status = "exhausted" if exhausted else "available"
    await conn.execute(
        """
        INSERT INTO demo_budget_state(day, status, note)
        VALUES ($1, $2, $3)
        ON CONFLICT (day) DO UPDATE
        SET status = EXCLUDED.status,
            note = EXCLUDED.note,
            updated_at = NOW()
        """,
        utc_today(),
        new_status,
        note,
    )
