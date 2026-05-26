"""Unit tests for calendar event triggers (Track 17.1.4)."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from ownevo_kernel.triggers.calendar import CalendarPoller
from ownevo_kernel.triggers.models import TriggerDefinition


def _make_calendar_trigger(config: dict) -> TriggerDefinition:
    return TriggerDefinition(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        name="cal-trigger",
        kind="calendar",
        action="run_clustering",
        config=config,
        enabled=True,
        created_at=datetime.datetime.now(tz=datetime.timezone.utc),
        updated_at=datetime.datetime.now(tz=datetime.timezone.utc),
        last_fired_at=None,
        fire_count=0,
    )


_NOW = datetime.datetime(2026, 1, 15, 10, 0, 0, tzinfo=datetime.timezone.utc)
# Event starts in 20 minutes; with offset=-15 the fire time is in 5 minutes.
_EVENT_START = _NOW + datetime.timedelta(minutes=20)


_GOOGLE_CFG = {
    "provider": "google",
    "mcp_server_id": "srv-gcal",
    "calendar_id": "primary",
    "offset_minutes": -15,
}


def _make_event(event_id: str = "evt-1", title: str = "Weekly Review") -> dict:
    return {
        "id": event_id,
        "title": title,
        "start_utc": _EVENT_START,
    }


class TestCalendarPoller:
    @pytest.mark.asyncio
    async def test_fires_within_tolerance_window(self):
        """A trigger fires when wall-clock is within ±90s of the fire time."""
        poller = CalendarPoller()
        trigger = _make_calendar_trigger(_GOOGLE_CFG)
        pool = AsyncMock()

        # Fire time = event_start + offset(-15min) = _NOW + 5min
        # Set current wall-clock to be 1 second before the fire time → within tolerance.
        fire_time = _EVENT_START + datetime.timedelta(minutes=-15)
        test_now = fire_time - datetime.timedelta(seconds=1)

        with (
            patch.object(
                poller,
                "_fetch_events",
                new=AsyncMock(return_value=[_make_event()]),
            ),
            patch(
                "ownevo_kernel.triggers.dispatcher.TriggerDispatcher",
            ) as mock_dispatcher_cls,
        ):
            mock_dispatcher = AsyncMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            fired = await poller.poll(pool, trigger, now=test_now)

        assert fired == 1
        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_fire_outside_tolerance(self):
        """A trigger does NOT fire when wall-clock is far from the fire time."""
        poller = CalendarPoller()
        trigger = _make_calendar_trigger(_GOOGLE_CFG)
        pool = AsyncMock()

        fire_time = _EVENT_START + datetime.timedelta(minutes=-15)
        # 10 minutes before the fire time — outside the ±90s window.
        test_now = fire_time - datetime.timedelta(minutes=10)

        with (
            patch.object(
                poller,
                "_fetch_events",
                new=AsyncMock(return_value=[_make_event()]),
            ),
            patch("ownevo_kernel.triggers.dispatcher.TriggerDispatcher") as mock_dispatcher_cls,
        ):
            mock_dispatcher = AsyncMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            fired = await poller.poll(pool, trigger, now=test_now)

        assert fired == 0
        mock_dispatcher.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_double_fire_for_same_event(self):
        """A trigger fires at most once per event per scheduler cycle."""
        poller = CalendarPoller()
        trigger = _make_calendar_trigger(_GOOGLE_CFG)
        pool = AsyncMock()

        fire_time = _EVENT_START + datetime.timedelta(minutes=-15)
        test_now = fire_time  # exactly at fire time

        with (
            patch.object(
                poller,
                "_fetch_events",
                new=AsyncMock(return_value=[_make_event()]),
            ),
            patch("ownevo_kernel.triggers.dispatcher.TriggerDispatcher") as mock_dispatcher_cls,
        ):
            mock_dispatcher = AsyncMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            # First poll — fires.
            fired1 = await poller.poll(pool, trigger, now=test_now)
            # Second poll — same event, already in _fired — should NOT fire again.
            fired2 = await poller.poll(pool, trigger, now=test_now)

        assert fired1 == 1
        assert fired2 == 0
        assert mock_dispatcher.dispatch.call_count == 1

    @pytest.mark.asyncio
    async def test_title_pattern_filters_events(self):
        config = {**_GOOGLE_CFG, "event_title_pattern": "(?i)board"}
        trigger = _make_calendar_trigger(config)
        poller = CalendarPoller()
        pool = AsyncMock()

        fire_time = _EVENT_START + datetime.timedelta(minutes=-15)
        test_now = fire_time

        events = [
            _make_event("e1", "Board Review"),
            _make_event("e2", "Weekly Standup"),  # should not match
        ]

        with (
            patch.object(poller, "_fetch_events", new=AsyncMock(return_value=events)),
            patch("ownevo_kernel.triggers.dispatcher.TriggerDispatcher") as mock_dispatcher_cls,
        ):
            mock_dispatcher = AsyncMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            fired = await poller.poll(pool, trigger, now=test_now)

        assert fired == 1

    @pytest.mark.asyncio
    async def test_positive_offset_fires_after_event(self):
        """Positive offset means fire AFTER the event starts."""
        config = {
            "provider": "google",
            "mcp_server_id": "srv",
            "calendar_id": "primary",
            "offset_minutes": 30,  # fire 30 min AFTER event
        }
        trigger = _make_calendar_trigger(config)
        poller = CalendarPoller()
        pool = AsyncMock()

        event_start = _NOW
        # Fire time = event_start + 30min = _NOW + 30min
        fire_time = event_start + datetime.timedelta(minutes=30)
        test_now = fire_time  # exactly at fire time

        event = {"id": "evt-2", "title": "Kickoff", "start_utc": event_start}

        with (
            patch.object(poller, "_fetch_events", new=AsyncMock(return_value=[event])),
            patch("ownevo_kernel.triggers.dispatcher.TriggerDispatcher") as mock_dispatcher_cls,
        ):
            mock_dispatcher = AsyncMock()
            mock_dispatcher_cls.return_value = mock_dispatcher
            fired = await poller.poll(pool, trigger, now=test_now)

        assert fired == 1

    def test_normalise_event_google(self):
        raw = {
            "id": "evt-1",
            "summary": "Board Review",
            "start": {"dateTime": "2026-01-15T10:00:00Z"},
        }
        result = CalendarPoller._normalise_event(raw, "google")
        assert result["id"] == "evt-1"
        assert result["title"] == "Board Review"
        assert result["start_utc"].hour == 10
