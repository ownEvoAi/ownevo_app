"""Stale-duration parsing for retention contracts.

Format per `docs/SKILL_FORMAT.md`:
  `1h`, `24h`, `7d`, `30d`, `never` — and the digit-less `never` literal.

Returned as `timedelta`; `never` becomes a sentinel max-duration.
The retention-violation eval-case generator divides this by two for
"still-fresh" cases and adds 1 minute for "stale" cases.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Final

NEVER: Final[timedelta] = timedelta(days=365 * 100)
"""Sentinel for `stale_after: never`. ~100y so any reasonable comparison
treats it as 'always fresh'."""

_PATTERN = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_TO_KW = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_stale_duration(s: str) -> timedelta:
    """Parse `1h`, `24h`, `7d`, `30d`, `never` into a `timedelta`.

    Raises `ValueError` for unknown formats so the skill registry can
    return a structured `SkillFormatError` to the agent.
    """
    text = s.strip().lower()
    if text == "never":
        return NEVER
    m = _PATTERN.match(text)
    if not m:
        raise ValueError(
            f"Invalid stale_after: {s!r}. Expected `<int><s|m|h|d|w>` or `never`.",
        )
    value, unit = int(m.group(1)), m.group(2).lower()
    return timedelta(**{_UNIT_TO_KW[unit]: value})
