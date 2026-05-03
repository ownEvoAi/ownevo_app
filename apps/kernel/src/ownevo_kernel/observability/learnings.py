"""Append-only learnings writer + most-recent reader.

The agent loop writes one `learnings` row per decision step. The
loop-stuck alerter (W2.4a) reads the most recent row to decide
whether to page. The table is INSERT-only by convention (no enforced
WORM trigger; the kind/content is the agent's reasoning trail, not
the audit log).

`LearningKind` mirrors the SQL CHECK constraint in 0001_substrate.sql:
the agent records hypotheses (proposed changes), observations (gate
outcomes / metric deltas), failure-notes (what didn't work), and
request-to-human (escalations). Three rejected hypotheses on the
same idea → abandon, per the proposer-loop discipline.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg

from ..types import Learning

LearningKind = Literal["hypothesis", "observation", "request-to-human", "failure-note"]


async def write_learning(
    conn: asyncpg.Connection,
    *,
    kind: LearningKind,
    content: str,
    iteration_id: UUID | None = None,
) -> Learning:
    """Insert one learning and return the typed model.

    `iteration_id` is None for top-of-workflow learnings (the agent
    framing a problem before binding to a specific iteration); set it
    for proposer-step learnings so the audit trail can join back.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO learnings (iteration_id, kind, content)
        VALUES ($1, $2, $3)
        RETURNING id, iteration_id, kind, content, created_at
        """,
        iteration_id,
        kind,
        content,
    )
    return Learning(
        id=row["id"],
        iteration_id=row["iteration_id"],
        kind=row["kind"],
        content=row["content"],
        created_at=row["created_at"],
    )


async def latest_learning(conn: asyncpg.Connection) -> Learning | None:
    """Return the most recent learning, or None if the table is empty.

    Used by `LoopStuckAlerter` to decide whether the loop is making
    progress. The sort is by `created_at DESC` (matches the index in
    0001_substrate.sql); ties broken by `id` for determinism.
    """
    row = await conn.fetchrow(
        """
        SELECT id, iteration_id, kind, content, created_at
        FROM learnings
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
    )
    if row is None:
        return None
    return Learning(
        id=row["id"],
        iteration_id=row["iteration_id"],
        kind=row["kind"],
        content=row["content"],
        created_at=row["created_at"],
    )
