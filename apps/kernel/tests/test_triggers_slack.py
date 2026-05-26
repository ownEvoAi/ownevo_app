"""Unit tests for Slack channel ingestion (Track 17.1.2)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from ownevo_kernel.triggers.models import TriggerDefinition
from ownevo_kernel.triggers.slack import SlackIngester

# The action is imported inside the function in slack.py, so patch the
# canonical location in the actions module.
_INGEST_PATH = "ownevo_kernel.triggers.actions.action_ingest_failures"
# Patch the workspace lookup so tests don't need a live DB.
_FETCH_WS_PATH = "ownevo_kernel.triggers.slack._fetch_workspace_id"
_FAKE_WORKSPACE_ID = "default"


def _make_slack_trigger(config: dict | None = None) -> TriggerDefinition:
    import datetime

    return TriggerDefinition(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        name="slack-trigger",
        kind="slack",
        action="ingest_failures",
        config=config or {
            "mcp_server_id": "srv-slack",
            "channel_id": "C012AB3CD",
        },
        enabled=True,
        created_at=datetime.datetime.now(tz=datetime.timezone.utc),
        updated_at=datetime.datetime.now(tz=datetime.timezone.utc),
        last_fired_at=None,
        fire_count=0,
    )


class TestSlackIngester:
    @pytest.mark.asyncio
    async def test_messages_are_ingested(self):
        ingester = SlackIngester()
        trigger = _make_slack_trigger()

        fake_messages = [
            {"ts": "1000.0", "text": "Demand forecast blew up"},
            {"ts": "1001.0", "text": "Another failure"},
        ]

        pool = AsyncMock()

        with (
            patch.object(ingester, "_fetch_messages", new=AsyncMock(return_value=fake_messages)),
            patch(_FETCH_WS_PATH, new=AsyncMock(return_value=_FAKE_WORKSPACE_ID)),
            patch(_INGEST_PATH, new=AsyncMock(return_value="trace-1")) as mock_ingest,
        ):
            count = await ingester.poll(pool, trigger)

        assert count == 2
        mock_ingest.assert_called_once()
        texts = mock_ingest.call_args[0][2]
        assert any("Demand forecast blew up" in t for t in texts)

    @pytest.mark.asyncio
    async def test_cursor_advances_after_poll(self):
        ingester = SlackIngester()
        trigger = _make_slack_trigger()

        fake_messages = [{"ts": "2000.0", "text": "failure A"}]
        pool = AsyncMock()

        with (
            patch.object(ingester, "_fetch_messages", new=AsyncMock(return_value=fake_messages)),
            patch(_FETCH_WS_PATH, new=AsyncMock(return_value=_FAKE_WORKSPACE_ID)),
            patch(_INGEST_PATH, new=AsyncMock()),
        ):
            await ingester.poll(pool, trigger)

        trigger_id = str(trigger.id)
        assert ingester._cursors[trigger_id] == "2000.0"

    @pytest.mark.asyncio
    async def test_filter_pattern_applied(self):
        ingester = SlackIngester()
        trigger = _make_slack_trigger(config={
            "mcp_server_id": "srv",
            "channel_id": "C-1",
            "filter_pattern": "forecast",
        })

        messages = [
            {"ts": "1.0", "text": "forecast failure"},
            {"ts": "2.0", "text": "login alert unrelated"},
        ]
        pool = AsyncMock()

        with (
            patch.object(ingester, "_fetch_messages", new=AsyncMock(return_value=messages)),
            patch(_FETCH_WS_PATH, new=AsyncMock(return_value=_FAKE_WORKSPACE_ID)),
            patch(_INGEST_PATH, new=AsyncMock()) as mock_ingest,
        ):
            count = await ingester.poll(pool, trigger)

        assert count == 1
        texts = mock_ingest.call_args[0][2]
        assert all("forecast" in t for t in texts)

    @pytest.mark.asyncio
    async def test_empty_messages_returns_zero(self):
        ingester = SlackIngester()
        trigger = _make_slack_trigger()
        pool = AsyncMock()

        with patch.object(ingester, "_fetch_messages", new=AsyncMock(return_value=[])):
            count = await ingester.poll(pool, trigger)

        assert count == 0

    @pytest.mark.asyncio
    async def test_invalid_config_returns_zero(self):
        ingester = SlackIngester()
        trigger = _make_slack_trigger(config={})  # invalid — missing mcp_server_id
        pool = AsyncMock()
        count = await ingester.poll(pool, trigger)
        assert count == 0
