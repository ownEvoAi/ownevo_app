"""Observability primitives — loop-stuck alerting + learnings writer (W2.4a).

The kernel writes one `learnings` row per agent decision (hypothesis,
observation, request-to-human, failure-note) — the same append-only
discipline auto-harness uses for `learnings.md`. The loop-stuck alerter
reads the most recent row and fires a Slack webhook when the gap
exceeds the idle threshold (default 2h), catching the
"best-ever stuck / agent-spinning-on-rejected-proposals" failure mode
the CEO review flagged.

Slack webhook integration uses stdlib HTTP via `asyncio.to_thread` —
no `httpx` / `aiohttp` dep added for one webhook call. A custom
`http_post` callable is injectable for tests.
"""

from ..types import LearningKind
from .learnings import latest_learning, write_learning
from .loop_stuck import LoopStuckAlerter, StuckSignal
from .past_attempts import (
    PastAttempt,
    fetch_past_attempts,
    format_past_attempts,
    render_past_attempts_block,
)

__all__ = [
    "LoopStuckAlerter",
    "LearningKind",
    "PastAttempt",
    "StuckSignal",
    "fetch_past_attempts",
    "format_past_attempts",
    "latest_learning",
    "render_past_attempts_block",
    "write_learning",
]
