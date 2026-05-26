"""Threshold-based trigger evaluator (Track 17.1.5).

Polls `metric_samples` on a per-trigger schedule and fires when a rolling
aggregate crosses the configured threshold.

Rising-edge semantics
----------------------
The evaluator tracks the *previous* state (above / below threshold) for
each trigger.  A fire only happens on the 0→1 transition (value crosses
from below to above, or vice versa depending on the operator).  This
prevents repeated fires while a metric is stuck above a threshold.

The `_above` state is stored in-memory (keyed by trigger_id) and does
NOT survive a restart — after a kernel restart the evaluator treats every
trigger as "not yet above" and will fire once if the condition is currently
met.  This means a restart can cause a single spurious fire; the
improvement-loop actions are idempotent so this is acceptable.

Integration with `TriggerScheduler`
-------------------------------------
The scheduler calls `ThresholdEvaluator.tick(trigger)` for each enabled
threshold trigger at the configured `poll_interval_minutes`.  The
evaluator queries the DB and, when the condition is met (rising-edge),
calls `dispatcher.dispatch(trigger)`.
"""

from __future__ import annotations

import logging
import operator as _op
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from .models import ThresholdConfig, TriggerDefinition
from .registry import TriggerRegistry

_log = logging.getLogger(__name__)

# Map operator string to Python comparison function.
_OPERATORS: dict[str, object] = {
    ">": _op.gt,
    ">=": _op.ge,
    "<": _op.lt,
    "<=": _op.le,
    "==": _op.eq,
    "!=": _op.ne,
}


class ThresholdEvaluator:
    """Evaluates threshold conditions for a set of trigger definitions.

    One shared instance per `TriggerScheduler`; per-trigger state is
    tracked in `_state` (dict of trigger_id → bool indicating whether the
    condition was truthy on the last poll).
    """

    def __init__(self) -> None:
        # trigger_id -> was_above on the last evaluation
        self._state: dict[str, bool] = {}

    async def evaluate(
        self,
        conn: asyncpg.Connection,
        trigger: TriggerDefinition,
    ) -> bool:
        """Evaluate the threshold condition for `trigger`.

        Returns True when the trigger should fire (rising-edge transition).
        Updates internal state to track the current condition value.

        Logs a warning and returns False when the config is invalid or the
        aggregate query returns no data.
        """
        try:
            cfg = ThresholdConfig.model_validate(trigger.config)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "threshold: invalid config for trigger %s: %s",
                trigger.id,
                exc,
            )
            return False

        cmp_fn = _OPERATORS.get(cfg.operator)
        if cmp_fn is None:
            _log.warning(
                "threshold: unknown operator %r for trigger %s",
                cfg.operator,
                trigger.id,
            )
            return False

        aggregate = await TriggerRegistry.compute_rolling_aggregate(
            conn,
            workflow_id=str(trigger.workflow_id),
            metric_name=cfg.metric_name,
            window_minutes=cfg.window_minutes,
            aggregation=cfg.aggregation,
        )

        if aggregate is None:
            _log.debug(
                "threshold: no metric_samples for %r / workflow %s in %d-minute window",
                cfg.metric_name,
                trigger.workflow_id,
                cfg.window_minutes,
            )
            return False

        currently_met: bool = bool(cmp_fn(aggregate, cfg.threshold_value))
        trigger_id = str(trigger.id)
        was_met = self._state.get(trigger_id, False)

        # Rising-edge: fire only on 0→1 transition.
        should_fire = currently_met and not was_met
        self._state[trigger_id] = currently_met

        if should_fire:
            _log.info(
                "threshold: trigger %s fired — %s(%r, %dmin) %s %g (value=%g)",
                trigger_id,
                cfg.aggregation,
                cfg.metric_name,
                cfg.window_minutes,
                cfg.operator,
                cfg.threshold_value,
                aggregate,
            )

        return should_fire

    def reset(self, trigger_id: str) -> None:
        """Clear the stored state for `trigger_id` (used in tests)."""
        self._state.pop(trigger_id, None)
