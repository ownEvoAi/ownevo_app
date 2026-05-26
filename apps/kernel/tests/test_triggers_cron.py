"""Unit tests for the cron tick helper (Track 17.1.1)."""

from __future__ import annotations

import datetime

import pytest

# Skip if croniter is not installed — cron triggers are opt-in.
croniter = pytest.importorskip(
    "croniter", reason="croniter not installed; install ownevo-kernel[triggers]"
)

from ownevo_kernel.triggers.cron import next_tick_utc, ticks_for_window  # noqa: E402


class TestNextTickUtc:
    def test_every_5_minutes(self):
        """@5-minute schedule advances by exactly 5 minutes."""
        base = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        nxt = next_tick_utc("*/5 * * * *", after=base)
        assert nxt.minute == 5
        assert nxt.hour == 12

    def test_result_is_utc(self):
        base = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        nxt = next_tick_utc("0 * * * *", after=base)
        assert nxt.tzinfo is not None
        assert nxt.tzinfo == datetime.timezone.utc or str(nxt.tzinfo) == "UTC"

    def test_daily_at_noon(self):
        base = datetime.datetime(2026, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        nxt = next_tick_utc("0 12 * * *", after=base)
        assert nxt.hour == 12
        assert nxt.minute == 0

    def test_after_defaults_to_now(self):
        """Not passing `after` uses the current time — just check it's in the future."""
        nxt = next_tick_utc("*/15 * * * *")
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        assert nxt > now


class TestTicksForWindow:
    def test_hourly_ticks_in_3_hours(self):
        start = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 1, 1, 3, 0, 0, tzinfo=datetime.timezone.utc)
        ticks = ticks_for_window("0 * * * *", start=start, end=end)
        # Three ticks: 01:00, 02:00, 03:00 is excluded (end is exclusive).
        assert len(ticks) == 2
        assert ticks[0].hour == 1
        assert ticks[1].hour == 2

    def test_empty_when_window_before_first_tick(self):
        start = datetime.datetime(2026, 1, 1, 0, 0, 1, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 1, 1, 0, 0, 59, tzinfo=datetime.timezone.utc)
        ticks = ticks_for_window("0 * * * *", start=start, end=end)
        assert ticks == []
