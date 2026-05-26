"""Email thread ingestion — Gmail + Outlook via MCP bindings (Track 17.1.3).

Polls a configured Gmail label or Outlook folder for new threads via the
registered MCP server and converts thread subjects + bodies to
``production_failure`` AgentEvents that feed the clustering pipeline.

Architecture
------------
* `EmailIngester` wraps the kernel's MCP client.
* For Gmail, it calls the ``search_threads`` MCP tool with the configured
  label filter.
* For Outlook, it calls the equivalent Outlook MCP list-messages tool.
* Each unread thread becomes one failure-description string.
* Thread IDs seen in previous polls are stored in memory to avoid
  re-ingesting the same thread.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from .models import EmailConfig, TriggerDefinition

_log = logging.getLogger(__name__)


class EmailIngester:
    """Polls Gmail / Outlook for new threads and converts them to failure events."""

    def __init__(self) -> None:
        # trigger_id -> set of thread IDs already ingested
        self._seen: dict[str, set[str]] = {}

    async def poll(
        self,
        pool: asyncpg.Pool,
        trigger: TriggerDefinition,
    ) -> int:
        """Poll for new threads and ingest them as failure events.

        Returns the number of threads ingested.
        """
        try:
            cfg = EmailConfig.model_validate(trigger.config)
        except Exception as exc:  # noqa: BLE001
            _log.warning("email: invalid config for trigger %s: %s", trigger.id, exc)
            return 0

        trigger_id = str(trigger.id)
        workflow_id = str(trigger.workflow_id)
        seen = self._seen.setdefault(trigger_id, set())

        if cfg.provider == "gmail":
            threads = await self._fetch_gmail_threads(cfg)
        else:
            threads = await self._fetch_outlook_messages(cfg)

        new_threads = [t for t in threads if t["id"] not in seen]
        if not new_threads:
            return 0

        # Apply optional filters.
        if cfg.filter_from:
            new_threads = [
                t for t in new_threads
                if cfg.filter_from.lower() in (t.get("from") or "").lower()
            ]
        if cfg.filter_subject_pattern:
            try:
                pat = re.compile(cfg.filter_subject_pattern)
                new_threads = [
                    t for t in new_threads if pat.search(t.get("subject") or "")
                ]
            except re.error as exc:
                _log.warning(
                    "email: invalid filter_subject_pattern %r for trigger %s: %s",
                    cfg.filter_subject_pattern,
                    trigger_id,
                    exc,
                )

        if not new_threads:
            return 0

        failure_texts = [
            f"Email [{cfg.provider}]: {t.get('subject', '(no subject)')} — {t.get('snippet', '')}"
            for t in new_threads
        ]

        # Mark all fetched threads as seen (not just the post-filter set) to
        # avoid re-processing threads that were filtered out last time but
        # match after a config change.
        for t in threads:
            seen.add(t["id"])

        from .actions import action_ingest_failures

        await action_ingest_failures(
            pool,
            workflow_id,
            failure_texts,
            source=f"email:{cfg.provider}",
        )
        _log.info(
            "email: ingested %d thread(s) from %s for trigger %s",
            len(failure_texts),
            cfg.provider,
            trigger_id,
        )
        return len(failure_texts)

    async def _fetch_gmail_threads(self, cfg: EmailConfig) -> list[dict]:
        """List unread Gmail threads with the configured label."""
        try:
            from ..mcp_client.client import MCPClient
        except ImportError:
            _log.warning(
                "email ingestion requires the `mcp` extra. "
                "Install ownevo-kernel[mcp]."
            )
            return []

        label_query = f"label:{cfg.label} is:unread" if cfg.label else "is:unread"
        try:
            async with MCPClient.from_server_id(cfg.mcp_server_id) as client:
                result = await client.call_tool(
                    "search_threads",
                    {"query": label_query, "max_results": 50},
                )
            threads = result if isinstance(result, list) else []
            return [
                {
                    "id": str(t.get("id") or t.get("threadId", "")),
                    "subject": str(t.get("subject", "")),
                    "from": str(t.get("from", "")),
                    "snippet": str(t.get("snippet", "")),
                }
                for t in threads
                if t.get("id") or t.get("threadId")
            ]
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "email: Gmail MCP call failed for mcp_server=%s label=%s: %s",
                cfg.mcp_server_id,
                cfg.label,
                exc,
            )
            return []

    async def _fetch_outlook_messages(self, cfg: EmailConfig) -> list[dict]:
        """List unread Outlook messages in the configured folder."""
        try:
            from ..mcp_client.client import MCPClient
        except ImportError:
            _log.warning(
                "email ingestion requires the `mcp` extra. "
                "Install ownevo-kernel[mcp]."
            )
            return []

        try:
            async with MCPClient.from_server_id(cfg.mcp_server_id) as client:
                result = await client.call_tool(
                    "list_messages",
                    {
                        "folder": cfg.folder,
                        "filter": "isRead eq false",
                        "top": 50,
                    },
                )
            messages = result if isinstance(result, list) else []
            return [
                {
                    "id": str(m.get("id", "")),
                    "subject": str(m.get("subject", "")),
                    "from": str(
                        m.get("from", {}).get("emailAddress", {}).get("address", "")
                    ),
                    "snippet": str(m.get("bodyPreview", "")),
                }
                for m in messages
                if m.get("id")
            ]
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "email: Outlook MCP call failed for mcp_server=%s folder=%s: %s",
                cfg.mcp_server_id,
                cfg.folder,
                exc,
            )
            return []
