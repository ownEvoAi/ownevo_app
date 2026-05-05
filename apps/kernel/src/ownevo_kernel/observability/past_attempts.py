"""Cross-iteration failure memory (TODO-23).

Renders a compact "Past attempts" block summarising prior iterations on a
workflow. The driver prepends it to the agent's kick-off message so each
iteration sees what previous iterations tried and why they failed —
without depending on the agent remembering to call `analyze_failures`.

Each row carries: iteration_index, decision (gate-pass / blocked / sandbox-error),
sandbox_error_class when applicable, val_score vs best_ever, the proposing
skill_id + plain-language summary, and the gate's eval_rationale (the human-
readable failure signature: stack trace excerpt, OOM/Timeout reason, etc.).

Why this lives in observability/: it reads from the same iteration/proposal
substrate the loop_stuck alerter watches — same domain (visibility into
prior loop activity), distinct surface (agent prompt vs operator alert).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg


_DEFAULT_LIMIT = 8
_MAX_RATIONALE_CHARS = 320
_MAX_SUMMARY_CHARS = 200


@dataclass(frozen=True)
class PastAttempt:
    iteration_index: int
    state: str                       # iteration_state enum value
    sandbox_error_class: str | None  # 'Timeout' | 'OOM' | 'Crash' | None
    val_score: float | None
    best_ever_score_before: float | None
    best_ever_score_after: float | None
    skill_id: str | None
    plain_language_summary: str | None
    eval_rationale: str | None


async def fetch_past_attempts(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    limit: int = _DEFAULT_LIMIT,
) -> list[PastAttempt]:
    """Return the most recent finalized iterations on `workflow_id`,
    newest first, capped at `limit`. 'running' iterations are skipped —
    they have no decision yet.
    """
    rows = await conn.fetch(
        """
        SELECT i.iteration_index,
               i.state::text                AS state,
               i.sandbox_error_class::text  AS sandbox_error_class,
               i.val_score,
               i.best_ever_score_before,
               i.best_ever_score_after,
               p.skill_id,
               p.plain_language_summary,
               p.eval_rationale
        FROM iterations i
        LEFT JOIN proposals p ON p.iteration_id = i.id
        WHERE i.workflow_id = $1
          AND i.state <> 'running'
        ORDER BY i.iteration_index DESC
        LIMIT $2
        """,
        workflow_id,
        limit,
    )
    return [
        PastAttempt(
            iteration_index=r["iteration_index"],
            state=r["state"],
            sandbox_error_class=r["sandbox_error_class"],
            val_score=_to_float(r["val_score"]),
            best_ever_score_before=_to_float(r["best_ever_score_before"]),
            best_ever_score_after=_to_float(r["best_ever_score_after"]),
            skill_id=r["skill_id"],
            plain_language_summary=r["plain_language_summary"],
            eval_rationale=r["eval_rationale"],
        )
        for r in rows
    ]


def format_past_attempts(attempts: list[PastAttempt]) -> str:
    """Render attempts as a markdown block suitable for an agent prompt.

    Empty input returns the empty string so callers can unconditionally
    concatenate without special-casing the cold-start case.
    """
    if not attempts:
        return ""
    lines: list[str] = [
        "## Past attempts on this workflow (most recent first)",
        "",
        "Each row is one prior iteration. **Don't repeat the same proposal "
        "or hit the same sandbox failure twice — try a different direction.**",
        "",
    ]
    for a in attempts:
        lines.append(_render_row(a))
    lines.append("")
    return "\n".join(lines)


async def render_past_attempts_block(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    limit: int = _DEFAULT_LIMIT,
) -> str:
    """Convenience: fetch + format in one call."""
    attempts = await fetch_past_attempts(conn, workflow_id=workflow_id, limit=limit)
    return format_past_attempts(attempts)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _render_row(a: PastAttempt) -> str:
    head = f"- **iter {a.iteration_index}** — `{a.state}`"
    if a.sandbox_error_class:
        head += f" ({a.sandbox_error_class})"
    if a.skill_id:
        head += f" · skill `{a.skill_id}`"
    score_bits: list[str] = []
    if a.val_score is not None:
        score_bits.append(f"val_score={a.val_score:.4f}")
    if a.best_ever_score_before is not None:
        score_bits.append(f"best_before={a.best_ever_score_before:.4f}")
    if a.best_ever_score_after is not None and (
        a.best_ever_score_before is None
        or a.best_ever_score_after != a.best_ever_score_before
    ):
        score_bits.append(f"best_after={a.best_ever_score_after:.4f}")
    if score_bits:
        head += "  (" + ", ".join(score_bits) + ")"

    body: list[str] = [head]
    if a.plain_language_summary:
        body.append(f"  - proposal: {_truncate(a.plain_language_summary, _MAX_SUMMARY_CHARS)}")
    if a.eval_rationale:
        body.append(f"  - reason: {_truncate(a.eval_rationale, _MAX_RATIONALE_CHARS)}")
    return "\n".join(body)


def _truncate(s: str, n: int) -> str:
    s = s.strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
