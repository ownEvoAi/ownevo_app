"""LoopStuckAlerter + learnings writer — DB-backed integration tests.

Pins the W2.4a contract:
  * Empty learnings table → not stuck (loop hasn't started writing).
  * Recent learning → not stuck.
  * Stale learning past threshold → stuck.
  * Webhook fires only when stuck AND webhook_url is set.
  * Webhook payload shape is `{"text": "..."}`.
  * `webhook_url=None` puts the alerter in observe-only mode.
  * `now=` is injectable so tests don't sleep.
  * webhook_url must be https://hooks.slack.com/... (SSRF guard).
  * Production path computes delta in DB (no cross-host clock skew).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.observability import (
    LoopStuckAlerter,
    latest_learning,
    write_learning,
)

# `db` fixture lives in apps/kernel/tests/conftest.py.
pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set",
)

# Shared test-mode threshold — short enough to fast-forward without sleeping.
TEST_THRESHOLD_SECONDS = 60.0


# ---------------------------------------------------------------------------
# learnings writer round-trip
# ---------------------------------------------------------------------------


async def test_write_learning_round_trip(db: asyncpg.Connection):
    learning = await write_learning(
        db,
        kind="hypothesis",
        content="Adding lag-7 feature should reduce RMSE on Mon/Tue underforecasts.",
    )
    assert learning.kind == "hypothesis"
    assert "lag-7" in learning.content
    assert learning.iteration_id is None
    assert learning.created_at.tzinfo is not None  # timestamptz preserved


async def test_write_learning_with_iteration_id_fk_enforced(db: asyncpg.Connection):
    """iteration_id is passed through to the SQL (not silently dropped).
    A non-existent UUID triggers FK violation — proves the parameter is wired."""
    fake_iter_id = uuid.uuid4()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await write_learning(
            db,
            kind="observation",
            content="Gate passed; RMSE improved 3.2%.",
            iteration_id=fake_iter_id,
        )


async def test_write_learning_rejects_invalid_kind(db: asyncpg.Connection):
    """SQL CHECK constraint catches kinds outside the four-value set."""
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO learnings (kind, content) VALUES ('not-a-real-kind', 'x')",
        )


async def test_latest_learning_returns_none_on_empty_table(db: asyncpg.Connection):
    assert await latest_learning(db) is None


async def test_latest_learning_returns_most_recent(db: asyncpg.Connection):
    """Sort is by created_at DESC — the freshest entry comes back."""
    await write_learning(db, kind="observation", content="first")
    await write_learning(db, kind="hypothesis", content="second")
    await write_learning(db, kind="failure-note", content="third")
    latest = await latest_learning(db)
    assert latest is not None
    assert latest.content == "third"


# ---------------------------------------------------------------------------
# LoopStuckAlerter — decision logic
# ---------------------------------------------------------------------------


async def test_alerter_not_stuck_on_empty_table(db: asyncpg.Connection):
    """Empty learnings table → not stuck (loop hasn't started writing).
    The "stuck" signal is for *stalls*, not for not-yet-started runs."""
    fired: list[tuple[str, dict]] = []

    async def fake_post(url: str, payload: dict) -> int:
        fired.append((url, payload))
        return 200

    alerter = LoopStuckAlerter(
        webhook_url="https://hooks.slack.com/services/FAKE/FAKE/fake",
        idle_threshold_seconds=TEST_THRESHOLD_SECONDS,
        http_post=fake_post,
    )
    signal = await alerter.check_and_alert(db)
    assert signal.is_stuck is False
    assert signal.last_learning_at is None
    assert signal.seconds_since_last is None
    assert signal.webhook_fired is False
    assert fired == []


async def test_alerter_not_stuck_on_recent_learning(db: asyncpg.Connection):
    """Latest learning is fresher than threshold → not stuck."""
    await write_learning(db, kind="observation", content="recent")

    fired: list[tuple[str, dict]] = []

    async def fake_post(url: str, payload: dict) -> int:
        fired.append((url, payload))
        return 200

    alerter = LoopStuckAlerter(
        webhook_url="https://hooks.slack.com/services/FAKE/FAKE/fake",
        idle_threshold_seconds=3600.0,  # 1h
        http_post=fake_post,
    )
    # Inject `now` explicitly so the test is independent of the system clock.
    just_now = datetime.now(UTC)
    signal = await alerter.check_and_alert(db, now=just_now)
    assert signal.is_stuck is False
    assert signal.seconds_since_last is not None
    assert signal.seconds_since_last < 3600.0
    assert signal.webhook_fired is False
    assert fired == []


async def test_alerter_stuck_past_threshold_fires_webhook(db: asyncpg.Connection):
    """Last learning > threshold ago → stuck; webhook fires with
    `{"text": "..."}` payload."""
    await write_learning(db, kind="observation", content="ancient hypothesis")

    fired: list[tuple[str, dict]] = []

    async def fake_post(url: str, payload: dict) -> int:
        fired.append((url, payload))
        return 200

    alerter = LoopStuckAlerter(
        webhook_url="https://hooks.slack.com/services/FAKE/FAKE/fake",
        idle_threshold_seconds=TEST_THRESHOLD_SECONDS,
        http_post=fake_post,
    )

    # Inject "now" 10 minutes ahead — well past the 60s threshold.
    far_future = datetime.now(UTC) + timedelta(minutes=10)
    signal = await alerter.check_and_alert(db, now=far_future)

    assert signal.is_stuck is True
    assert signal.seconds_since_last is not None
    assert signal.seconds_since_last > TEST_THRESHOLD_SECONDS
    assert signal.webhook_fired is True
    assert len(fired) == 1
    url, payload = fired[0]
    assert url == "https://hooks.slack.com/services/FAKE/FAKE/fake"
    assert "text" in payload
    assert "stuck" in payload["text"].lower()
    # Payload references the kind of the last entry so an oncall
    # opening the alert can quickly tell what the loop was last doing.
    assert "observation" in payload["text"]


async def test_alerter_observe_only_when_webhook_url_none(db: asyncpg.Connection):
    """webhook_url=None → never fires; the signal is still computed.
    Useful for dev / dry-run / tests-without-mocking."""
    await write_learning(db, kind="observation", content="ancient")

    fired: list[tuple[str, dict]] = []

    async def fake_post(url: str, payload: dict) -> int:
        fired.append((url, payload))
        return 200

    alerter = LoopStuckAlerter(
        webhook_url=None,
        idle_threshold_seconds=TEST_THRESHOLD_SECONDS,
        http_post=fake_post,
    )
    far_future = datetime.now(UTC) + timedelta(minutes=10)
    signal = await alerter.check_and_alert(db, now=far_future)

    assert signal.is_stuck is True
    assert signal.webhook_fired is False
    assert fired == []  # no POST attempted


async def test_alerter_threshold_validation():
    """Non-positive threshold is a programming error, not a config
    knob — fail loudly at construction."""
    with pytest.raises(ValueError, match="idle_threshold_seconds"):
        LoopStuckAlerter(idle_threshold_seconds=0)
    with pytest.raises(ValueError, match="idle_threshold_seconds"):
        LoopStuckAlerter(idle_threshold_seconds=-1)


async def test_alerter_rejects_non_slack_webhook_url():
    """SSRF guard: webhook_url must be https://hooks.slack.com/..."""
    with pytest.raises(ValueError, match="hooks.slack.com"):
        LoopStuckAlerter(webhook_url="http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError, match="hooks.slack.com"):
        LoopStuckAlerter(webhook_url="https://attacker.example.com/steal")
    with pytest.raises(ValueError, match="hooks.slack.com"):
        LoopStuckAlerter(webhook_url="https://hooks.example.test/X")
    # Valid URL must not raise
    LoopStuckAlerter(webhook_url="https://hooks.slack.com/services/T/B/key")


async def test_alerter_production_path_uses_db_delta(db: asyncpg.Connection):
    """Production path (now=None) computes delta in DB — no cross-host clock comparison.
    A very recent learning → not stuck, delta very small."""
    await write_learning(db, kind="hypothesis", content="just wrote this")

    fired: list[tuple[str, dict]] = []

    async def fake_post(url: str, payload: dict) -> int:
        fired.append((url, payload))
        return 200

    alerter = LoopStuckAlerter(
        webhook_url="https://hooks.slack.com/services/FAKE/FAKE/fake",
        idle_threshold_seconds=3600.0,
        http_post=fake_post,
    )
    # No now= override — exercises the DB-delta code path.
    signal = await alerter.check_and_alert(db)
    assert signal.is_stuck is False
    assert signal.seconds_since_last is not None
    assert signal.seconds_since_last < 3600.0
    assert fired == []


async def test_alerter_rejects_naive_now(db: asyncpg.Connection):
    """Passing a timezone-naive datetime as now= raises ValueError.
    latest.created_at is timestamptz (UTC-aware); arithmetic with a
    naive datetime raises TypeError — we guard this explicitly."""
    alerter = LoopStuckAlerter(idle_threshold_seconds=TEST_THRESHOLD_SECONDS)
    naive_now = datetime(2026, 5, 3, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="timezone-aware"):
        await alerter.check_and_alert(db, now=naive_now)


async def test_alerter_summary_humanizes_duration(db: asyncpg.Connection):
    """Summary uses h/m/s units so an oncall can read it at a glance."""
    await write_learning(db, kind="hypothesis", content="x")

    async def fake_post(url: str, payload: dict) -> int:
        return 200

    alerter = LoopStuckAlerter(
        webhook_url="https://hooks.slack.com/services/FAKE/FAKE/fake",
        idle_threshold_seconds=TEST_THRESHOLD_SECONDS,
        http_post=fake_post,
    )
    far_future = datetime.now(UTC) + timedelta(hours=3)
    signal = await alerter.check_and_alert(db, now=far_future)
    assert signal.is_stuck is True
    # 3h is rendered as "3.0h" not "10800s".
    assert "h" in signal.summary
    assert "10800s" not in signal.summary


# ---------------------------------------------------------------------------
# Determinism: equal-timestamp tiebreak
# ---------------------------------------------------------------------------


async def test_latest_learning_handles_equal_timestamps(
    db: asyncpg.Connection,
):
    """If two rows share the same created_at (clock granularity collision),
    the query returns one of them deterministically without crashing.
    UUID4 ordering is random, so we assert only that one row is returned —
    not which one. The tiebreak prevents ambiguous multi-row results."""
    ts = datetime.now(UTC)
    await db.execute(
        "INSERT INTO learnings (kind, content, created_at) VALUES ($1, $2, $3)",
        "hypothesis", "row-a", ts,
    )
    await db.execute(
        "INSERT INTO learnings (kind, content, created_at) VALUES ($1, $2, $3)",
        "hypothesis", "row-b", ts,
    )
    latest = await latest_learning(db)
    assert latest is not None
    assert latest.content in {"row-a", "row-b"}
