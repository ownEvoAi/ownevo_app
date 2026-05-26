"""Unit tests for the threshold evaluator (Track 17.1.5)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from ownevo_kernel.triggers.models import TriggerDefinition
from ownevo_kernel.triggers.threshold import ThresholdEvaluator


def _make_trigger(config: dict) -> TriggerDefinition:
    import datetime

    return TriggerDefinition(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        name="test-threshold",
        kind="threshold",
        action="run_clustering",
        config=config,
        enabled=True,
        created_at=datetime.datetime.now(tz=datetime.timezone.utc),
        updated_at=datetime.datetime.now(tz=datetime.timezone.utc),
        last_fired_at=None,
        fire_count=0,
    )


class TestThresholdEvaluator:
    @pytest.mark.asyncio
    async def test_fire_on_rising_edge(self):
        """Evaluator fires on 0→1 transition (value crosses threshold)."""
        trigger = _make_trigger({
            "metric_name": "smape",
            "window_minutes": 60,
            "aggregation": "avg",
            "operator": ">",
            "threshold_value": 0.30,
            "poll_interval_minutes": 5,
        })
        ev = ThresholdEvaluator()

        conn = AsyncMock()
        # First call: value below threshold (0.25) — should NOT fire.
        with patch(
            "ownevo_kernel.triggers.threshold.TriggerRegistry.compute_rolling_aggregate",
            new=AsyncMock(return_value=0.25),
        ):
            fired = await ev.evaluate(conn, trigger)
        assert fired is False

        # Second call: value above threshold (0.40) — rising edge → SHOULD fire.
        with patch(
            "ownevo_kernel.triggers.threshold.TriggerRegistry.compute_rolling_aggregate",
            new=AsyncMock(return_value=0.40),
        ):
            fired = await ev.evaluate(conn, trigger)
        assert fired is True

        # Third call: still above (0.35) — no new crossing → should NOT fire again.
        with patch(
            "ownevo_kernel.triggers.threshold.TriggerRegistry.compute_rolling_aggregate",
            new=AsyncMock(return_value=0.35),
        ):
            fired = await ev.evaluate(conn, trigger)
        assert fired is False

    @pytest.mark.asyncio
    async def test_no_data_returns_false(self):
        trigger = _make_trigger({
            "metric_name": "smape",
            "window_minutes": 60,
            "operator": ">",
            "threshold_value": 0.10,
        })
        ev = ThresholdEvaluator()
        conn = AsyncMock()
        with patch(
            "ownevo_kernel.triggers.threshold.TriggerRegistry.compute_rolling_aggregate",
            new=AsyncMock(return_value=None),
        ):
            fired = await ev.evaluate(conn, trigger)
        assert fired is False

    @pytest.mark.asyncio
    async def test_invalid_config_returns_false(self):
        trigger = _make_trigger({})  # missing required fields
        ev = ThresholdEvaluator()
        conn = AsyncMock()
        fired = await ev.evaluate(conn, trigger)
        assert fired is False

    @pytest.mark.asyncio
    async def test_all_operators(self):
        """Verify each operator evaluates correctly."""
        cases = [
            (">", 0.5, 0.6, True),
            (">", 0.5, 0.4, False),
            (">=", 0.5, 0.5, True),
            ("<", 0.5, 0.4, True),
            ("<=", 0.5, 0.5, True),
            ("==", 0.5, 0.5, True),
            ("!=", 0.5, 0.6, True),
        ]
        for op, threshold, value, expected in cases:
            trigger = _make_trigger({
                "metric_name": "m",
                "window_minutes": 10,
                "operator": op,
                "threshold_value": threshold,
            })
            ev = ThresholdEvaluator()
            conn = AsyncMock()
            with patch(
                "ownevo_kernel.triggers.threshold.TriggerRegistry.compute_rolling_aggregate",
                new=AsyncMock(return_value=value),
            ):
                fired = await ev.evaluate(conn, trigger)
            assert fired is expected, f"op={op} threshold={threshold} value={value}"

    def test_reset_clears_state(self):
        ev = ThresholdEvaluator()
        trigger_id = "trig-1"
        ev._state[trigger_id] = True
        ev.reset(trigger_id)
        assert trigger_id not in ev._state
