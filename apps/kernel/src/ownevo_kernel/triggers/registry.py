"""DB CRUD for trigger_definitions and trigger_fires (Track 17.1).

All writes go through `TriggerRegistry`.  Reads return typed
`TriggerDefinition` / `TriggerFire` model instances.

Pattern: every method accepts a raw `asyncpg.Connection` (not a pool)
so callers can participate in a transaction if needed.  The API layer
acquires connections via `ConnDep`; the scheduler acquires them via
`pool.acquire()`.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg

from .models import TriggerAction, TriggerDefinition, TriggerFire, TriggerKind

_log = logging.getLogger(__name__)


def _row_to_definition(row: Any) -> TriggerDefinition:
    config = row["config"]
    if isinstance(config, str):
        config = json.loads(config)
    return TriggerDefinition(
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


def _row_to_fire(row: Any) -> TriggerFire:
    return TriggerFire(
        id=row["id"],
        trigger_id=row["trigger_id"],
        workflow_id=row["workflow_id"],
        fired_at=row["fired_at"],
        action=row["action"],
        status=row["status"],
        error_message=row["error_message"],
        payload_summary=row["payload_summary"],
    )


class TriggerRegistry:
    """Read/write access to `trigger_definitions` and `trigger_fires`."""

    # ------------------------------------------------------------------
    # trigger_definitions
    # ------------------------------------------------------------------

    @staticmethod
    async def list_for_workflow(
        conn: asyncpg.Connection,
        workflow_id: str,
        *,
        include_disabled: bool = False,
    ) -> list[TriggerDefinition]:
        """Return all triggers for *workflow_id*, newest first."""
        if include_disabled:
            rows = await conn.fetch(
                "SELECT * FROM trigger_definitions "
                "WHERE workflow_id = $1 "
                "ORDER BY created_at DESC",
                workflow_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM trigger_definitions "
                "WHERE workflow_id = $1 AND enabled = TRUE "
                "ORDER BY created_at DESC",
                workflow_id,
            )
        return [_row_to_definition(r) for r in rows]

    @staticmethod
    async def list_by_kind(
        conn: asyncpg.Connection,
        kind: TriggerKind,
    ) -> list[TriggerDefinition]:
        """Return all enabled triggers of a given kind across all workflows."""
        rows = await conn.fetch(
            "SELECT * FROM trigger_definitions "
            "WHERE kind = $1 AND enabled = TRUE "
            "ORDER BY workflow_id, created_at",
            kind,
        )
        return [_row_to_definition(r) for r in rows]

    @staticmethod
    async def get(
        conn: asyncpg.Connection,
        trigger_id: str,
    ) -> TriggerDefinition | None:
        row = await conn.fetchrow(
            "SELECT * FROM trigger_definitions WHERE id = $1",
            trigger_id,
        )
        return _row_to_definition(row) if row else None

    @staticmethod
    async def create(
        conn: asyncpg.Connection,
        *,
        workflow_id: str,
        name: str,
        kind: TriggerKind,
        action: TriggerAction,
        config: dict[str, Any],
        enabled: bool = True,
    ) -> TriggerDefinition:
        """Insert a new trigger definition and return the created row."""
        row = await conn.fetchrow(
            """
            INSERT INTO trigger_definitions
                (workflow_id, name, kind, action, config, enabled)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            RETURNING *
            """,
            workflow_id,
            name,
            kind,
            action,
            json.dumps(config),
            enabled,
        )
        return _row_to_definition(row)

    @staticmethod
    async def update(
        conn: asyncpg.Connection,
        trigger_id: str,
        *,
        name: str | None = None,
        action: TriggerAction | None = None,
        config: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> TriggerDefinition | None:
        """Partial-update a trigger.  Returns None when not found."""
        sets: list[str] = ["updated_at = now()"]
        params: list[Any] = []
        idx = 1

        if name is not None:
            params.append(name)
            sets.append(f"name = ${idx}")
            idx += 1
        if action is not None:
            params.append(action)
            sets.append(f"action = ${idx}")
            idx += 1
        if config is not None:
            params.append(json.dumps(config))
            sets.append(f"config = ${idx}::jsonb")
            idx += 1
        if enabled is not None:
            params.append(enabled)
            sets.append(f"enabled = ${idx}")
            idx += 1

        params.append(trigger_id)
        row = await conn.fetchrow(
            f"UPDATE trigger_definitions SET {', '.join(sets)} "  # noqa: S608
            f"WHERE id = ${idx} RETURNING *",
            *params,
        )
        return _row_to_definition(row) if row else None

    @staticmethod
    async def delete(conn: asyncpg.Connection, trigger_id: str) -> bool:
        """Hard-delete a trigger definition.  Returns True if a row was deleted."""
        result = await conn.execute(
            "DELETE FROM trigger_definitions WHERE id = $1",
            trigger_id,
        )
        return result == "DELETE 1"

    @staticmethod
    async def record_fire(
        conn: asyncpg.Connection,
        *,
        trigger_id: str,
        workflow_id: str,
        action: TriggerAction,
        status: str = "ok",
        error_message: str | None = None,
        payload_summary: str | None = None,
    ) -> TriggerFire:
        """Append a fire record and update last_fired_at + fire_count."""
        async with conn.transaction():
            fire_row = await conn.fetchrow(
                """
                INSERT INTO trigger_fires
                    (trigger_id, workflow_id, action, status,
                     error_message, payload_summary)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                trigger_id,
                workflow_id,
                action,
                status,
                error_message,
                payload_summary,
            )
            await conn.execute(
                """
                UPDATE trigger_definitions
                SET last_fired_at = now(),
                    fire_count = fire_count + 1,
                    updated_at = now()
                WHERE id = $1
                """,
                trigger_id,
            )
        return _row_to_fire(fire_row)

    # ------------------------------------------------------------------
    # trigger_fires
    # ------------------------------------------------------------------

    @staticmethod
    async def list_fires(
        conn: asyncpg.Connection,
        trigger_id: str,
        *,
        limit: int = 50,
    ) -> list[TriggerFire]:
        rows = await conn.fetch(
            "SELECT * FROM trigger_fires WHERE trigger_id = $1 "
            "ORDER BY fired_at DESC LIMIT $2",
            trigger_id,
            limit,
        )
        return [_row_to_fire(r) for r in rows]

    # ------------------------------------------------------------------
    # metric_samples
    # ------------------------------------------------------------------

    @staticmethod
    async def record_metric_sample(
        conn: asyncpg.Connection,
        *,
        workflow_id: str,
        metric_name: str,
        value: float,
        source: str | None = None,
    ) -> None:
        """Append a metric sample row for threshold evaluation."""
        await conn.execute(
            """
            INSERT INTO metric_samples (workflow_id, metric_name, value, source)
            VALUES ($1, $2, $3, $4)
            """,
            workflow_id,
            metric_name,
            value,
            source,
        )

    @staticmethod
    async def compute_rolling_aggregate(
        conn: asyncpg.Connection,
        *,
        workflow_id: str,
        metric_name: str,
        window_minutes: int,
        aggregation: str,
    ) -> float | None:
        """Return the rolling aggregate for the most recent `window_minutes`."""
        valid_aggs = {"avg", "sum", "count", "min", "max"}
        if aggregation not in valid_aggs:
            raise ValueError(f"aggregation must be one of {valid_aggs}")

        # Safe: `aggregation` is validated against a whitelist above.
        # asyncpg does not support dynamic identifiers via parameters.
        sql = f"""  # noqa: S608
            SELECT {aggregation}(value)
            FROM metric_samples
            WHERE workflow_id = $1
              AND metric_name = $2
              AND recorded_at >= now() - ($3 || ' minutes')::interval
        """
        result = await conn.fetchval(sql, workflow_id, metric_name, str(window_minutes))
        return float(result) if result is not None else None
