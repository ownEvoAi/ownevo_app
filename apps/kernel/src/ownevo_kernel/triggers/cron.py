"""Cron-expression ticker used by the trigger scheduler (Track 17.1.1).

Wraps *croniter* to compute the next tick for a cron expression string,
keeping the scheduler loop free of expression parsing.

`CronTick` is a thin value object; `next_tick_utc` is the only public
function.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CronTick:
    """Describes the next scheduled tick for one trigger."""

    trigger_id: str
    workflow_id: str
    next_at_utc: datetime.datetime  # timezone-aware (UTC)
    schedule: str


def next_tick_utc(
    schedule: str,
    *,
    after: datetime.datetime | None = None,
    timezone: str = "UTC",
) -> datetime.datetime:
    """Return the next tick time (UTC) after `after` for `schedule`.

    Args:
        schedule: Standard cron expression or @alias (@hourly, @daily,
            @weekly, @monthly).
        after: Compute the next tick after this moment.  Defaults to the
            current UTC time.
        timezone: IANA timezone for expression evaluation (e.g.
            ``"America/New_York"``).  The returned datetime is always UTC.

    Raises:
        ImportError: When *croniter* is not installed.
        ValueError: When `schedule` is not a valid cron expression.
    """
    try:
        from croniter import croniter  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "croniter is required for cron triggers. "
            "Install the `triggers` extra: pip install ownevo-kernel[triggers]"
        ) from exc

    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(timezone)
    except (ImportError, zoneinfo.ZoneInfoNotFoundError) as exc:
        _log.warning("cron: unknown timezone %r, falling back to UTC: %s", timezone, exc)
        import datetime as _dt
        tz = _dt.timezone.utc

    reference = after if after is not None else datetime.datetime.now(tz=datetime.timezone.utc)
    # Convert reference to the target timezone for expression evaluation.
    ref_local = reference.astimezone(tz)

    it = croniter(schedule, ref_local)
    next_local: datetime.datetime = it.get_next(datetime.datetime)
    # Return as UTC.
    return next_local.astimezone(datetime.timezone.utc)


def ticks_for_window(
    schedule: str,
    *,
    start: datetime.datetime,
    end: datetime.datetime,
    timezone: str = "UTC",
) -> list[datetime.datetime]:
    """Return all ticks in [start, end) for `schedule` (UTC datetimes).

    Useful for tests that need to enumerate expected ticks without
    running the real-time scheduler.
    """
    try:
        from croniter import croniter  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError("croniter is required for cron triggers.") from exc

    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(timezone)
    except (ImportError, zoneinfo.ZoneInfoNotFoundError):
        import datetime as _dt
        tz = _dt.timezone.utc

    results: list[datetime.datetime] = []
    ref = start.astimezone(tz)
    it = croniter(schedule, ref)
    while True:
        nxt: datetime.datetime = it.get_next(datetime.datetime)
        nxt_utc = nxt.astimezone(datetime.timezone.utc)
        if nxt_utc >= end:
            break
        results.append(nxt_utc)
    return results
