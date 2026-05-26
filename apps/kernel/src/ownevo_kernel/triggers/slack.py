"""Slack channel ingestion → production_failure AgentEvents (Track 17.1.2).

Polls a configured Slack channel via the MCP-backed Slack integration for
new messages and converts them to ``ToolCallResultEvent(status="error")``
AgentEvents that flow into the clustering pipeline.

Architecture
------------
* The `SlackIngester` class wraps the kernel's MCP client to list channel
  messages via the registered Slack MCP server.
* Each new message becomes one failure-description string fed to
  `action_ingest_failures`.
* A cursor (timestamp of the most-recently-seen message) is stored in
  memory; on restart the ingester falls back to `lookback_hours`.
* The `TriggerScheduler` calls `SlackIngester.poll(trigger)` at the
  configured `poll_interval_seconds`.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from ..tenant_session import DEFAULT_WORKSPACE_ID, acquire_workspace_conn
from .models import SlackConfig, TriggerDefinition

_log = logging.getLogger(__name__)


async def _fetch_workspace_id(pool: asyncpg.Pool, workflow_id: str) -> str:
    """Return the workspace_id for *workflow_id*, falling back to the default."""
    async with acquire_workspace_conn(pool, DEFAULT_WORKSPACE_ID) as conn:
        row = await conn.fetchrow(
            "SELECT workspace_id FROM workflows WHERE id = $1", workflow_id
        )
    return str(row["workspace_id"]) if row else DEFAULT_WORKSPACE_ID


class SlackIngester:
    """Polls Slack for new channel messages and converts them to failure events.

    One shared instance per `TriggerScheduler`; per-trigger cursors are
    tracked in `_cursors` (trigger_id → latest message timestamp string).
    """

    def __init__(self) -> None:
        # trigger_id -> "oldest" cursor: the Slack ts of the last message seen
        self._cursors: dict[str, str] = {}

    async def poll(
        self,
        pool: asyncpg.Pool,
        trigger: TriggerDefinition,
    ) -> int:
        """Poll for new messages and ingest them as failure events.

        Returns the number of messages ingested.
        """
        try:
            cfg = SlackConfig.model_validate(trigger.config)
        except Exception as exc:  # noqa: BLE001
            _log.warning("slack: invalid config for trigger %s: %s", trigger.id, exc)
            return 0

        trigger_id = str(trigger.id)
        workflow_id = str(trigger.workflow_id)

        # Determine the "oldest" cursor for this trigger.
        oldest = self._cursors.get(trigger_id)
        if oldest is None:
            # Fall back to lookback window on first poll / after restart.
            oldest = str(time.time() - cfg.lookback_hours * 3600)

        messages = await self._fetch_messages(cfg, oldest=oldest)
        if not messages:
            return 0

        # Apply optional regex filter.
        if cfg.filter_pattern:
            try:
                pattern = re.compile(cfg.filter_pattern)
                messages = [m for m in messages if pattern.search(m["text"] or "")]
            except re.error as exc:
                _log.warning(
                    "slack: invalid filter_pattern %r for trigger %s: %s",
                    cfg.filter_pattern,
                    trigger_id,
                    exc,
                )

        if not messages:
            return 0

        failure_texts = [
            f"Slack #{cfg.channel_id}: {m['text']}" for m in messages
        ]

        # Update cursor to the ts of the newest message.
        newest_ts = max(m["ts"] for m in messages)
        self._cursors[trigger_id] = newest_ts

        workspace_id = await _fetch_workspace_id(pool, workflow_id)

        from .actions import action_ingest_failures

        await action_ingest_failures(
            pool,
            workflow_id,
            failure_texts,
            workspace_id,
            source=f"slack:{cfg.channel_id}",
        )
        _log.info(
            "slack: ingested %d message(s) from channel %s for trigger %s",
            len(failure_texts),
            cfg.channel_id,
            trigger_id,
        )
        return len(failure_texts)

    async def _fetch_messages(
        self,
        cfg: SlackConfig,
        oldest: str,
    ) -> list[dict]:
        """Call the Slack MCP server to list channel messages since `oldest`.

        Returns a list of ``{"ts": str, "text": str}`` dicts, newest-first.
        """
        try:
            from ..mcp_client.client import MCPClient
        except ImportError as exc:
            _log.warning(
                "slack ingestion requires the `mcp` extra. "
                "Install ownevo-kernel[mcp]."
            )
            return []

        try:
            async with MCPClient.from_server_id(cfg.mcp_server_id) as client:
                result = await client.call_tool(
                    "slack_list_messages",
                    {
                        "channel": cfg.channel_id,
                        "oldest": oldest,
                        "limit": 200,
                    },
                )
            # The MCP tool returns a list of message objects with at minimum
            # `ts` and `text` fields. Unknown keys are ignored.
            messages = result if isinstance(result, list) else []
            return [
                {"ts": str(m.get("ts", "")), "text": str(m.get("text", ""))}
                for m in messages
                if m.get("ts") and m.get("text")
            ]
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "slack: MCP call failed for trigger mcp_server=%s channel=%s: %s",
                cfg.mcp_server_id,
                cfg.channel_id,
                exc,
            )
            return []
