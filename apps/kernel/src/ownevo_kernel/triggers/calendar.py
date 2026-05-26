"""Calendar event triggers — Google Calendar + Outlook Calendar (Track 17.1.4).

Fires N minutes before or after a matching calendar event.

Architecture
------------
* `CalendarPoller` polls the configured calendar via the MCP server to
  list upcoming events in a rolling look-ahead window.
* For each event, it computes the target fire time as:
      event_start + timedelta(minutes=offset_minutes)
* When the current wall-clock enters the ±`fire_tolerance_seconds`
  window around the target fire time, the trigger fires.
* A set of (event_id, offset_minutes) pairs already fired is tracked in
  memory to prevent double-fires within the same scheduler cycle.
* The `TriggerScheduler` calls `CalendarPoller.poll(trigger)` at the
  configured `poll_interval_seconds`.
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from .models import CalendarConfig, TriggerDefinition

_log = logging.getLogger(__name__)

# Fire the trigger when the wall-clock is within this many seconds of the
# computed fire time. Generous tolerance compensates for polling jitter.
_FIRE_TOLERANCE_SECONDS = 90

# Look-ahead window: search for events within the next N hours.
_LOOKAHEAD_HOURS = 24


class CalendarPoller:
    """Polls a calendar for events and fires when the offset time arrives."""

    def __init__(self) -> None:
        # (trigger_id, event_id) -> True when already fired in this cycle
        self._fired: dict[tuple[str, str], bool] = {}
        # Prune fired-event set hourly to prevent unbounded growth.
        self._last_prune: datetime.datetime = datetime.datetime.now(tz=datetime.timezone.utc)

    async def poll(
        self,
        pool: asyncpg.Pool,
        trigger: TriggerDefinition,
        *,
        now: datetime.datetime | None = None,
    ) -> int:
        """Check upcoming events and dispatch when the offset time arrives.

        Returns the number of trigger fires dispatched.
        """
        try:
            cfg = CalendarConfig.model_validate(trigger.config)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "calendar: invalid config for trigger %s: %s", trigger.id, exc
            )
            return 0

        trigger_id = str(trigger.id)
        current = now or datetime.datetime.now(tz=datetime.timezone.utc)

        events = await self._fetch_events(cfg, current)

        fired_count = 0
        for event in events:
            event_id = event.get("id", "")
            if not event_id:
                continue

            # Filter by title pattern.
            if cfg.event_title_pattern:
                title = event.get("title") or event.get("summary") or ""
                try:
                    if not re.search(cfg.event_title_pattern, title):
                        continue
                except re.error:
                    pass  # invalid pattern — skip filter

            start = event.get("start_utc")
            if start is None:
                continue

            fire_time = start + datetime.timedelta(minutes=cfg.offset_minutes)
            delta = abs((fire_time - current).total_seconds())

            key = (trigger_id, event_id)
            if delta <= _FIRE_TOLERANCE_SECONDS and not self._fired.get(key):
                self._fired[key] = True
                fired_count += 1

                summary = (
                    f"calendar: event '{event.get('title', event_id)}' "
                    f"at {start.isoformat()} offset={cfg.offset_minutes:+d}min"
                )
                _log.info("calendar: firing trigger %s — %s", trigger_id, summary)

                from .dispatcher import TriggerDispatcher

                dispatcher = TriggerDispatcher(pool)
                await dispatcher.dispatch(trigger, payload_summary=summary)

        self._maybe_prune(current)
        return fired_count

    async def _fetch_events(
        self,
        cfg: CalendarConfig,
        now: datetime.datetime,
    ) -> list[dict]:
        """List upcoming calendar events via MCP."""
        try:
            from ..mcp_client.client import MCPClient
        except ImportError:
            _log.warning(
                "calendar triggers require the `mcp` extra. "
                "Install ownevo-kernel[mcp]."
            )
            return []

        time_min = now.isoformat()
        time_max = (now + datetime.timedelta(hours=_LOOKAHEAD_HOURS)).isoformat()

        try:
            async with MCPClient.from_server_id(cfg.mcp_server_id) as client:
                if cfg.provider == "google":
                    result = await client.call_tool(
                        "list_events",
                        {
                            "calendar_id": cfg.calendar_id,
                            "time_min": time_min,
                            "time_max": time_max,
                            "max_results": 50,
                        },
                    )
                else:
                    result = await client.call_tool(
                        "list_calendar_events",
                        {
                            "folder_id": cfg.calendar_id,
                            "start": time_min,
                            "end": time_max,
                            "top": 50,
                        },
                    )
            events_raw = result if isinstance(result, list) else []
            return [self._normalise_event(e, cfg.provider) for e in events_raw]
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "calendar: MCP call failed for provider=%s server=%s: %s",
                cfg.provider,
                cfg.mcp_server_id,
                exc,
            )
            return []

    @staticmethod
    def _normalise_event(raw: dict, provider: str) -> dict:
        """Extract id, title, and start_utc from a provider-specific event dict."""
        if provider == "google":
            event_id = raw.get("id", "")
            title = raw.get("summary", "")
            start_str = (
                raw.get("start", {}).get("dateTime")
                or raw.get("start", {}).get("date")
            )
        else:
            event_id = raw.get("id", "")
            title = raw.get("subject", "") or raw.get("summary", "")
            start_str = raw.get("start", {}).get("dateTime") or raw.get("start")

        start_utc: datetime.datetime | None = None
        if start_str:
            try:
                dt = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                start_utc = dt.astimezone(datetime.timezone.utc)
            except ValueError:
                pass

        return {"id": event_id, "title": title, "start_utc": start_utc}

    def _maybe_prune(self, now: datetime.datetime) -> None:
        """Prune fired-event memory once per hour to prevent unbounded growth."""
        if (now - self._last_prune).total_seconds() < 3600:
            return
        self._fired.clear()
        self._last_prune = now
