"""LoopStuckAlerter — Slack webhook for the agent-stuck failure mode (W2.4a).

A specific failure mode worth alerting on: the agent loop runs,
proposes changes, the gate rejects them, and the agent keeps spinning
without making progress. From the outside, the loop *looks* alive —
processes are running, traces are being written — but no learnings
are landing because the proposer is stuck on the same hypothesis.

The alerter is the simplest possible signal: read the most recent
`learnings.created_at`; if the gap exceeds `idle_threshold_seconds`,
fire a Slack webhook. Default threshold is 2h, matching the review's
ask. Test mode uses a 1-minute window so integration tests can
verify the contract without sleeping.

Slack webhook is `https://hooks.slack.com/services/...`; payload
shape is `{"text": "..."}`. Stdlib `urllib.request` via
`asyncio.to_thread` keeps the kernel free of HTTP-client deps; for
testability `http_post` is injectable.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from ..types import Learning
from .learnings import latest_learning

HttpPostFn = Callable[[str, dict], Awaitable[int]]
"""Async (url, payload) → HTTP status. Used to mock the webhook in tests."""

DEFAULT_IDLE_THRESHOLD_SECONDS = 2 * 60 * 60
"""2h — the review's spec for "loop is stuck"."""

_HTTP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class StuckSignal:
    """What the alerter found.

    `is_stuck` is the decision; the rest is evidence the caller can
    log or surface in the Slack message. `seconds_since_last` is None
    only when `last_learning_at` is None (empty table — the loop
    hasn't started writing yet, treated as not-stuck).
    """

    is_stuck: bool
    last_learning_at: datetime | None
    seconds_since_last: float | None
    threshold_seconds: float
    summary: str
    webhook_fired: bool


class LoopStuckAlerter:
    """Reads `learnings`, decides stuck/not-stuck, optionally pages Slack.

    Empty `learnings` table → not stuck. The first learning establishes
    the baseline; the alerter's job is to catch *stalls*, not
    not-yet-started workflows. A separate "no progress in N hours from
    workflow start" check belongs at the workflow lifecycle layer
    (Phase 2 work), not here.

    `webhook_url=None` puts the alerter in observe-only mode — it
    returns the signal but never POSTs. Useful for dev / dry-run.

    Wire to a periodic background task in the kernel startup — see W2.6
    (M5 loop). Example: check every 10 minutes, pass the pool connection.
    Alert deduplication (cooldown) should be added at the call site.
    """

    def __init__(
        self,
        *,
        webhook_url: str | None = None,
        idle_threshold_seconds: float = DEFAULT_IDLE_THRESHOLD_SECONDS,
        http_post: HttpPostFn | None = None,
    ) -> None:
        if idle_threshold_seconds <= 0:
            raise ValueError(
                f"idle_threshold_seconds must be positive; got {idle_threshold_seconds}",
            )
        if webhook_url is not None:
            _parsed = urllib.parse.urlparse(webhook_url)
            if _parsed.scheme != "https" or _parsed.netloc != "hooks.slack.com":
                raise ValueError(
                    f"webhook_url must be an https://hooks.slack.com/... URL; "
                    f"got {webhook_url!r}"
                )
        self.webhook_url = webhook_url
        self.idle_threshold_seconds = idle_threshold_seconds
        self._http_post = http_post or _default_http_post

    async def check_and_alert(
        self,
        conn: asyncpg.Connection,
        *,
        now: datetime | None = None,
    ) -> StuckSignal:
        """Read the most recent learning, decide, optionally page.

        Production path (`now=None`): the elapsed-time delta is computed
        inside Postgres via `EXTRACT(EPOCH FROM (now() - created_at))`
        to eliminate cross-host clock-skew as a source of false negatives.

        Test override (`now=` set): delta is computed in Python against
        the injected timestamp so tests can fast-forward without sleeping.
        The injected `now` must be timezone-aware (UTC).
        """
        if now is not None and now.tzinfo is None:
            raise ValueError(
                "now must be timezone-aware (pass datetime.now(UTC) or datetime(..., tzinfo=UTC))"
            )

        if now is None:
            latest, delta = await _latest_with_db_delta(conn)
        else:
            latest_obj = await latest_learning(conn)
            if latest_obj is None:
                latest, delta = None, None
            else:
                latest = latest_obj
                delta = max(0.0, (now - latest.created_at).total_seconds())

        if latest is None:
            return StuckSignal(
                is_stuck=False,
                last_learning_at=None,
                seconds_since_last=None,
                threshold_seconds=self.idle_threshold_seconds,
                summary="No learnings yet — loop hasn't started writing.",
                webhook_fired=False,
            )

        assert delta is not None
        is_stuck = delta > self.idle_threshold_seconds

        if not is_stuck:
            return StuckSignal(
                is_stuck=False,
                last_learning_at=latest.created_at,
                seconds_since_last=delta,
                threshold_seconds=self.idle_threshold_seconds,
                summary=(
                    f"Loop alive: last learning {_format_seconds(delta)} ago "
                    f"(threshold {_format_seconds(self.idle_threshold_seconds)})."
                ),
                webhook_fired=False,
            )

        summary = (
            f":warning: ownEvo loop stuck: no learnings in "
            f"{_format_seconds(delta)} "
            f"(threshold {_format_seconds(self.idle_threshold_seconds)}). "
            f"Last entry was a `{latest.kind}` at "
            f"{latest.created_at.isoformat()}."
        )

        webhook_fired = False
        if self.webhook_url is not None:
            await self._http_post(self.webhook_url, {"text": summary})
            webhook_fired = True

        return StuckSignal(
            is_stuck=True,
            last_learning_at=latest.created_at,
            seconds_since_last=delta,
            threshold_seconds=self.idle_threshold_seconds,
            summary=summary,
            webhook_fired=webhook_fired,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _latest_with_db_delta(
    conn: asyncpg.Connection,
) -> tuple[Learning, float] | tuple[None, None]:
    """Fetch the most recent learning and its age, computed in DB.

    The delta is `EXTRACT(EPOCH FROM (now() - created_at))` — fully
    inside Postgres, eliminating cross-host clock skew. Returns
    (None, None) when the learnings table is empty.
    """
    row = await conn.fetchrow(
        """
        SELECT id, iteration_id, kind, content, created_at,
               EXTRACT(EPOCH FROM (now() - created_at))::float AS delta_seconds
        FROM learnings
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
    )
    if row is None:
        return None, None
    learning = Learning(
        id=row["id"],
        iteration_id=row["iteration_id"],
        kind=row["kind"],
        content=row["content"],
        created_at=row["created_at"],
    )
    return learning, max(0.0, float(row["delta_seconds"]))


# ---------------------------------------------------------------------------
# Default HTTP poster — stdlib only
# ---------------------------------------------------------------------------


async def _default_http_post(url: str, payload: dict) -> int:
    """Synchronous urllib.request POST run on the asyncio threadpool.

    Returns the HTTP status code. Raises `OSError` (including
    `urllib.error.URLError` and `urllib.error.HTTPError` subclasses)
    on transport failures or non-2xx responses. Slack accepts a JSON
    body and answers 200 with body `ok` on success.
    """

    def _post() -> int:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return resp.status

    return await asyncio.to_thread(_post)


def _format_seconds(seconds: float) -> str:
    """Render a duration in human-readable form for the Slack message."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"
