"""Background trigger scheduler (Track 17.1).

`TriggerScheduler` is the long-lived background task that wakes on
schedule and dispatches cron, threshold, Slack, email, and calendar
triggers.  Lifecycle mirrors `ClusterAutoTrigger`:

* `await start()` — spawns the background poll loop.
* `await stop()` — gracefully halts the loop.

Cron triggers
-------------
Each cron trigger's next tick is computed via `next_tick_utc` (croniter).
The scheduler sleeps at most `_CRON_POLL_INTERVAL_SECONDS` at a time and
checks whether any cron trigger's next tick has elapsed.  Once fired, the
next tick is recomputed.

Threshold triggers
------------------
The `ThresholdEvaluator` is called at each trigger's configured
`poll_interval_minutes`.  Next-poll timestamps are tracked in memory.

Slack / Email triggers
-----------------------
`SlackIngester` and `EmailIngester` are called at each trigger's
configured `poll_interval_seconds`.

Calendar triggers
-----------------
`CalendarPoller` is called at each trigger's configured
`poll_interval_seconds`.

Robustness
----------
* All per-trigger failures are caught and logged; a failing trigger
  does not halt the loop.
* On restart, per-trigger state (cron next-tick, threshold state,
  ingestor cursors) is recomputed from scratch.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from .calendar import CalendarPoller
from .cron import next_tick_utc
from .dispatcher import TriggerDispatcher
from .email import EmailIngester
from .models import CronConfig, TriggerDefinition
from .registry import TriggerRegistry
from .slack import SlackIngester
from .threshold import ThresholdEvaluator

_log = logging.getLogger(__name__)

# How often the main loop wakes to check for due triggers.
_LOOP_POLL_SECONDS = 10.0
# How often the scheduler re-reads trigger definitions from the DB.
_RELOAD_INTERVAL_SECONDS = 60.0


class TriggerScheduler:
    """Manages cron / threshold / Slack / email / calendar triggers.

    Lifecycle: `await start()` → run → `await stop()`.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        poll_interval: float = _LOOP_POLL_SECONDS,
        reload_interval: float = _RELOAD_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pool = pool
        self._poll_interval = poll_interval
        self._reload_interval = reload_interval
        self._clock = clock

        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

        # Per-kind runtime state.
        self._threshold = ThresholdEvaluator()
        self._slack = SlackIngester()
        self._email = EmailIngester()
        self._calendar = CalendarPoller()

        # trigger_id -> monotonic timestamp of the next cron tick.
        self._cron_next: dict[str, float] = {}
        # trigger_id -> monotonic timestamp of the next threshold/Slack/email/calendar poll.
        self._next_poll: dict[str, float] = {}

        # Cached trigger definitions. Refreshed every `_reload_interval`.
        self._triggers: list[TriggerDefinition] = []
        self._last_reload: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="trigger-scheduler")
        _log.info("trigger scheduler: started")

    async def stop(self, timeout: float = 30.0) -> None:
        self._stopping.set()
        if self._task is not None:
            done, _ = await asyncio.wait({self._task}, timeout=timeout)
            if not done:
                _log.warning(
                    "trigger scheduler: stop() timed out after %.0fs; cancelling",
                    timeout,
                )
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task
            self._task = None
        _log.info("trigger scheduler: stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._poll_interval
                )
            if self._stopping.is_set():
                break
            await self._tick()

    async def _tick(self) -> None:
        now = self._clock()
        await self._maybe_reload(now)

        for trigger in self._triggers:
            kind = trigger.kind
            try:
                if kind == "cron":
                    await self._tick_cron(trigger, now)
                elif kind == "threshold":
                    await self._tick_threshold(trigger, now)
                elif kind == "slack":
                    await self._tick_slack(trigger, now)
                elif kind == "email":
                    await self._tick_email(trigger, now)
                elif kind == "calendar":
                    await self._tick_calendar(trigger, now)
                # "webhook" triggers are purely reactive (fired by the API
                # route); the scheduler doesn't poll them.
            except Exception:  # noqa: BLE001
                _log.exception(
                    "trigger scheduler: unhandled error for trigger %s (kind=%s)",
                    trigger.id,
                    kind,
                )

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    async def _tick_cron(self, trigger: TriggerDefinition, now: float) -> None:
        trigger_id = str(trigger.id)
        if trigger_id not in self._cron_next:
            self._cron_next[trigger_id] = self._compute_cron_next(trigger, now)

        if now < self._cron_next[trigger_id]:
            return

        _log.info("trigger scheduler: cron trigger %s firing", trigger_id)
        dispatcher = TriggerDispatcher(self._pool)
        await dispatcher.dispatch(trigger, payload_summary="cron tick")
        # Advance to the next tick.
        self._cron_next[trigger_id] = self._compute_cron_next(trigger, now + 1)

    def _compute_cron_next(self, trigger: TriggerDefinition, after_monotonic: float) -> float:
        """Return the monotonic timestamp of the next cron tick."""
        try:
            cfg = CronConfig.model_validate(trigger.config)
        except Exception:
            return after_monotonic + 3600  # back off 1h on bad config

        # Convert monotonic offset to a wall-clock datetime.
        wall_now = datetime.datetime.now(tz=datetime.timezone.utc)
        offset = datetime.timedelta(seconds=after_monotonic - self._clock())
        after_utc = wall_now + offset

        try:
            next_utc = next_tick_utc(cfg.schedule, after=after_utc, timezone=cfg.timezone)
        except Exception as exc:
            _log.warning("cron: failed to compute next tick for trigger %s: %s", trigger.id, exc)
            return after_monotonic + 3600

        delta = (next_utc - wall_now).total_seconds()
        return self._clock() + max(delta, 0.0)

    # ------------------------------------------------------------------
    # Threshold
    # ------------------------------------------------------------------

    async def _tick_threshold(self, trigger: TriggerDefinition, now: float) -> None:
        trigger_id = str(trigger.id)
        from .models import ThresholdConfig

        try:
            cfg = ThresholdConfig.model_validate(trigger.config)
        except Exception:
            return

        interval = cfg.poll_interval_minutes * 60.0
        if now < self._next_poll.get(trigger_id, 0.0):
            return

        self._next_poll[trigger_id] = now + interval

        async with self._pool.acquire() as conn:
            should_fire = await self._threshold.evaluate(conn, trigger)

        if should_fire:
            dispatcher = TriggerDispatcher(self._pool)
            await dispatcher.dispatch(
                trigger,
                payload_summary=(
                    f"threshold: {cfg.aggregation}({cfg.metric_name}) "
                    f"{cfg.operator} {cfg.threshold_value}"
                ),
            )

    # ------------------------------------------------------------------
    # Slack
    # ------------------------------------------------------------------

    async def _tick_slack(self, trigger: TriggerDefinition, now: float) -> None:
        trigger_id = str(trigger.id)
        from .models import SlackConfig

        try:
            cfg = SlackConfig.model_validate(trigger.config)
        except Exception:
            return

        interval = float(cfg.poll_interval_seconds)
        if now < self._next_poll.get(trigger_id, 0.0):
            return

        self._next_poll[trigger_id] = now + interval
        await self._slack.poll(self._pool, trigger)

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------

    async def _tick_email(self, trigger: TriggerDefinition, now: float) -> None:
        trigger_id = str(trigger.id)
        from .models import EmailConfig

        try:
            cfg = EmailConfig.model_validate(trigger.config)
        except Exception:
            return

        interval = float(cfg.poll_interval_seconds)
        if now < self._next_poll.get(trigger_id, 0.0):
            return

        self._next_poll[trigger_id] = now + interval
        await self._email.poll(self._pool, trigger)

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    async def _tick_calendar(self, trigger: TriggerDefinition, now: float) -> None:
        trigger_id = str(trigger.id)
        from .models import CalendarConfig

        try:
            cfg = CalendarConfig.model_validate(trigger.config)
        except Exception:
            return

        interval = float(cfg.poll_interval_seconds)
        if now < self._next_poll.get(trigger_id, 0.0):
            return

        self._next_poll[trigger_id] = now + interval
        await self._calendar.poll(self._pool, trigger)

    # ------------------------------------------------------------------
    # Trigger reload
    # ------------------------------------------------------------------

    async def _maybe_reload(self, now: float) -> None:
        if now - self._last_reload < self._reload_interval:
            return
        try:
            async with self._pool.acquire() as conn:
                # Load all enabled triggers for all kinds except webhook.
                rows = await conn.fetch(
                    """
                    SELECT * FROM trigger_definitions
                    WHERE enabled = TRUE AND kind != 'webhook'
                    ORDER BY kind, workflow_id
                    """
                )
                from ..api.jsonb import jsonb_to_dict  # noqa: PLC0415
                import json

                self._triggers = []
                for row in rows:
                    config = row["config"]
                    if isinstance(config, str):
                        config = json.loads(config)
                    from .models import TriggerDefinition as TD
                    self._triggers.append(
                        TD(
                            id=row["id"],
                            workflow_id=row["workflow_id"],
                            name=row["name"],
                            kind=row["kind"],
                            action=row["action"],
                            config=config,
                            enabled=row["enabled"],
                            created_at=row["created_at"],
                            updated_at=row["updated_at"],
                            last_fired_at=row["last_fired_at"],
                            fire_count=row["fire_count"],
                        )
                    )
            self._last_reload = now
            _log.debug("trigger scheduler: loaded %d trigger(s)", len(self._triggers))
        except Exception:  # noqa: BLE001
            _log.exception("trigger scheduler: failed to reload trigger definitions")
