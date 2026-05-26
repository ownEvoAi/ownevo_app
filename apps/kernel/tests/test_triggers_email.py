"""Unit tests for email thread ingestion (Track 17.1.3)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from ownevo_kernel.triggers.models import TriggerDefinition
from ownevo_kernel.triggers.email import EmailIngester

# Patch at canonical location (action is imported inside function body).
_INGEST_PATH = "ownevo_kernel.triggers.actions.action_ingest_failures"


def _make_email_trigger(config: dict) -> TriggerDefinition:
    import datetime

    return TriggerDefinition(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        name="email-trigger",
        kind="email",
        action="ingest_failures",
        config=config,
        enabled=True,
        created_at=datetime.datetime.now(tz=datetime.timezone.utc),
        updated_at=datetime.datetime.now(tz=datetime.timezone.utc),
        last_fired_at=None,
        fire_count=0,
    )


_GMAIL_CFG = {
    "provider": "gmail",
    "mcp_server_id": "srv-gmail",
    "label": "ownevo-failures",
}

_OUTLOOK_CFG = {
    "provider": "outlook",
    "mcp_server_id": "srv-outlook",
    "folder": "Agent Failures",
}


class TestEmailIngester:
    @pytest.mark.asyncio
    async def test_gmail_threads_ingested(self):
        ingester = EmailIngester()
        trigger = _make_email_trigger(_GMAIL_CFG)
        pool = AsyncMock()

        fake_threads = [
            {"id": "t1", "subject": "SMAPE spike", "from": "ops@acme.com", "snippet": "smape rose"},
            {"id": "t2", "subject": "OOM in solver", "from": "ops@acme.com", "snippet": "killed"},
        ]

        with (
            patch.object(
                ingester, "_fetch_gmail_threads", new=AsyncMock(return_value=fake_threads)
            ),
            patch(_INGEST_PATH, new=AsyncMock(return_value="trace-1")) as mock_ingest,
        ):
            count = await ingester.poll(pool, trigger)

        assert count == 2
        texts = mock_ingest.call_args[0][2]
        assert any("SMAPE spike" in t for t in texts)

    @pytest.mark.asyncio
    async def test_seen_threads_not_re_ingested(self):
        ingester = EmailIngester()
        trigger = _make_email_trigger(_GMAIL_CFG)
        pool = AsyncMock()
        trigger_id = str(trigger.id)

        fake_threads = [
            {"id": "t1", "subject": "Already seen", "from": "x", "snippet": ""},
        ]
        # Mark t1 as already seen.
        ingester._seen[trigger_id] = {"t1"}

        with (
            patch.object(
                ingester, "_fetch_gmail_threads", new=AsyncMock(return_value=fake_threads)
            ),
            patch(_INGEST_PATH, new=AsyncMock()) as mock_ingest,
        ):
            count = await ingester.poll(pool, trigger)

        assert count == 0
        mock_ingest.assert_not_called()

    @pytest.mark.asyncio
    async def test_outlook_threads_ingested(self):
        ingester = EmailIngester()
        trigger = _make_email_trigger(_OUTLOOK_CFG)
        pool = AsyncMock()

        fake_messages = [
            {
                "id": "m1",
                "subject": "Forecast failure",
                "from": {"emailAddress": {"address": "a@b.com"}},
                "bodyPreview": "smape=0.9",
            },
        ]

        with (
            patch.object(
                ingester, "_fetch_outlook_messages", new=AsyncMock(return_value=fake_messages)
            ),
            patch(_INGEST_PATH, new=AsyncMock(return_value="trace-2")) as mock_ingest,
        ):
            count = await ingester.poll(pool, trigger)

        assert count == 1
        texts = mock_ingest.call_args[0][2]
        assert any("Forecast failure" in t for t in texts)

    @pytest.mark.asyncio
    async def test_subject_filter_applied(self):
        ingester = EmailIngester()
        config = {**_GMAIL_CFG, "filter_subject_pattern": "SMAPE"}
        trigger = _make_email_trigger(config)
        pool = AsyncMock()

        fake_threads = [
            {"id": "t1", "subject": "SMAPE spike alert", "from": "", "snippet": ""},
            {"id": "t2", "subject": "Login notification", "from": "", "snippet": ""},
        ]

        with (
            patch.object(
                ingester, "_fetch_gmail_threads", new=AsyncMock(return_value=fake_threads)
            ),
            patch(_INGEST_PATH, new=AsyncMock()) as mock_ingest,
        ):
            count = await ingester.poll(pool, trigger)

        assert count == 1
        texts = mock_ingest.call_args[0][2]
        assert all("SMAPE" in t for t in texts)

    @pytest.mark.asyncio
    async def test_empty_threads_returns_zero(self):
        ingester = EmailIngester()
        trigger = _make_email_trigger(_GMAIL_CFG)
        pool = AsyncMock()

        with patch.object(
            ingester, "_fetch_gmail_threads", new=AsyncMock(return_value=[])
        ):
            count = await ingester.poll(pool, trigger)

        assert count == 0
