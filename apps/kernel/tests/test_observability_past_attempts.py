"""Cross-iteration failure memory (TODO-23) — past_attempts helper."""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.observability.past_attempts import (
    PastAttempt,
    fetch_past_attempts,
    format_past_attempts,
    render_past_attempts_block,
)


# ---------------------------------------------------------------------------
# Pure formatter
# ---------------------------------------------------------------------------


def test_format_past_attempts_empty_returns_empty_string():
    """Cold workflow → empty string so callers can concatenate unconditionally."""
    assert format_past_attempts([]) == ""


def test_format_past_attempts_renders_sandbox_error_with_rationale():
    block = format_past_attempts([
        PastAttempt(
            iteration_index=4,
            state="sandbox-error",
            sandbox_error_class="OOM",
            val_score=None,
            best_ever_score_before=0.39,
            best_ever_score_after=0.39,
            skill_id="m5.baseline.v1.feature_engineer",
            plain_language_summary="add lag-28 + rolling-7 features",
            eval_rationale="Runner raised MemoryError: feature matrix exceeded 512 MB",
        ),
    ])
    assert "iter 4" in block
    assert "sandbox-error" in block
    assert "OOM" in block
    assert "feature_engineer" in block
    assert "lag-28" in block
    assert "MemoryError" in block


def test_format_past_attempts_truncates_long_rationale():
    long_msg = "x" * 1000
    block = format_past_attempts([
        PastAttempt(
            iteration_index=0,
            state="gate-blocked-no-improvement",
            sandbox_error_class=None,
            val_score=0.33,
            best_ever_score_before=0.39,
            best_ever_score_after=0.39,
            skill_id="m5.baseline.v1.predictor",
            plain_language_summary="tweak n_estimators",
            eval_rationale=long_msg,
        ),
    ])
    # Truncated with ellipsis, never the full 1000 chars.
    assert "x" * 1000 not in block
    assert "…" in block


def test_format_past_attempts_omits_score_block_when_all_none():
    block = format_past_attempts([
        PastAttempt(
            iteration_index=0,
            state="sandbox-error",
            sandbox_error_class="Crash",
            val_score=None,
            best_ever_score_before=None,
            best_ever_score_after=None,
            skill_id="m5.baseline.v1.predictor",
            plain_language_summary="bad cast",
            eval_rationale="TypeError: ...",
        ),
    ])
    # No "(val_score=..." parenthetical when nothing populated.
    assert "val_score=" not in block
    assert "best_before=" not in block


# ---------------------------------------------------------------------------
# DB-backed fetch
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set",
)


async def _seed_workflow(db: asyncpg.Connection, workflow_id: str) -> None:
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, 'seeded by test', '{}'::jsonb) "
        "ON CONFLICT (id) DO NOTHING",
        workflow_id,
    )


async def _seed_iteration(
    db: asyncpg.Connection,
    *,
    workflow_id: str,
    iteration_index: int,
    state: str,
    sandbox_error_class: str | None = None,
    val_score: float | None = None,
    best_ever_score_after: float | None = None,
    skill_id: str | None = None,
    summary: str | None = None,
    eval_rationale: str | None = None,
) -> None:
    """Insert one iteration + matching proposal so the LEFT JOIN in
    fetch_past_attempts has something to render."""
    iteration_id = await db.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state,
                                sandbox_error_class, val_score,
                                best_ever_score_after, ended_at)
        VALUES ($1, $2, $3::iteration_state, $4::sandbox_error_class,
                $5, $6, now())
        RETURNING id
        """,
        workflow_id, iteration_index, state, sandbox_error_class,
        val_score, best_ever_score_after,
    )
    if skill_id is None:
        return
    # Skill row + a single proposal so the LEFT JOIN populates.
    await db.execute(
        "INSERT INTO skills (id, kind) VALUES ($1, 'python'::skill_kind) "
        "ON CONFLICT (id) DO NOTHING",
        skill_id,
    )
    await db.execute(
        """
        INSERT INTO proposals (
            iteration_id, skill_id, proposed_content,
            plain_language_summary, state, eval_rationale
        )
        VALUES ($1, $2, 'body', $3, 'gate-failed'::proposal_state, $4)
        """,
        iteration_id, skill_id, summary or "no summary", eval_rationale,
    )


async def test_fetch_past_attempts_empty_for_unknown_workflow(db: asyncpg.Connection):
    await _seed_workflow(db, "wf-x")
    assert await fetch_past_attempts(db, workflow_id="wf-x") == []


async def test_fetch_past_attempts_orders_newest_first_and_skips_running(
    db: asyncpg.Connection,
):
    await _seed_workflow(db, "wf-1")
    await _seed_iteration(
        db, workflow_id="wf-1", iteration_index=0, state="gate-pass",
        val_score=0.39, best_ever_score_after=0.39,
        skill_id="s.alpha", summary="iter 0 proposal", eval_rationale="passed",
    )
    await _seed_iteration(
        db, workflow_id="wf-1", iteration_index=1, state="sandbox-error",
        sandbox_error_class="OOM",
        skill_id="s.alpha", summary="iter 1 proposal", eval_rationale="MemoryError",
    )
    # In-flight iteration must be excluded from past_attempts.
    await _seed_iteration(
        db, workflow_id="wf-1", iteration_index=2, state="running",
    )

    attempts = await fetch_past_attempts(db, workflow_id="wf-1")
    assert [a.iteration_index for a in attempts] == [1, 0]
    assert attempts[0].sandbox_error_class == "OOM"
    assert attempts[0].eval_rationale == "MemoryError"
    assert attempts[1].state == "gate-pass"


async def test_fetch_past_attempts_respects_limit(db: asyncpg.Connection):
    await _seed_workflow(db, "wf-2")
    for i in range(5):
        await _seed_iteration(
            db, workflow_id="wf-2", iteration_index=i,
            state="gate-blocked-no-improvement", val_score=0.30,
        )
    attempts = await fetch_past_attempts(db, workflow_id="wf-2", limit=3)
    assert len(attempts) == 3
    assert [a.iteration_index for a in attempts] == [4, 3, 2]


async def test_render_past_attempts_block_end_to_end(db: asyncpg.Connection):
    """Convenience wrapper returns a non-empty markdown block when there's
    history and the empty string when there isn't."""
    await _seed_workflow(db, "wf-3")
    assert await render_past_attempts_block(db, workflow_id="wf-3") == ""

    await _seed_iteration(
        db, workflow_id="wf-3", iteration_index=0, state="sandbox-error",
        sandbox_error_class="Timeout",
        skill_id="s.beta", summary="rolling-mean window=14",
        eval_rationale="Sandbox timed out after 60s while training",
    )
    block = await render_past_attempts_block(db, workflow_id="wf-3")
    assert "Past attempts" in block
    assert "Timeout" in block
    assert "rolling-mean" in block
