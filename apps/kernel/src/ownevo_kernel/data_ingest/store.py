"""DB access for the `data_uploads` table (migration 0028).

Stores the parsed representation of an upload — schema + content — and returns
the non-bulky `DataUpload` metadata view everywhere except the explicit
content accessor.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from .models import DataUpload, UploadKind

if TYPE_CHECKING:
    import asyncpg


def _row_to_upload(row: asyncpg.Record) -> DataUpload:
    schema = row["schema"]
    if isinstance(schema, str):
        schema = json.loads(schema)
    return DataUpload(
        id=row["id"],
        name=row["name"],
        kind=UploadKind(row["kind"]),
        content_type=row["content_type"],
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
        schema=schema or {},
        row_count=row["row_count"],
        uploaded_at=row["uploaded_at"].isoformat(),
        retention_expires_at=(
            row["retention_expires_at"].isoformat()
            if row["retention_expires_at"]
            else None
        ),
    )


async def create_upload(
    conn: asyncpg.Connection,
    *,
    name: str,
    kind: UploadKind,
    content_type: str | None,
    size_bytes: int,
    sha256: str,
    schema: dict[str, Any],
    row_count: int | None,
    content: dict[str, Any],
    retention_expires_at: str | None = None,
) -> DataUpload:
    row = await conn.fetchrow(
        """
        INSERT INTO data_uploads (
            name, kind, content_type, size_bytes, sha256,
            schema, row_count, content, retention_expires_at
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9)
        RETURNING id, name, kind, content_type, size_bytes, sha256,
                  schema, row_count, uploaded_at, retention_expires_at
        """,
        name,
        kind.value,
        content_type,
        size_bytes,
        sha256,
        json.dumps(schema),
        row_count,
        json.dumps(content),
        retention_expires_at,
    )
    return _row_to_upload(row)


_META_COLS = (
    "id, name, kind, content_type, size_bytes, sha256, schema, row_count, "
    "uploaded_at, retention_expires_at"
)


async def get_upload(conn: asyncpg.Connection, upload_id: UUID) -> DataUpload | None:
    row = await conn.fetchrow(
        f"SELECT {_META_COLS} FROM data_uploads WHERE id = $1", upload_id
    )
    return _row_to_upload(row) if row is not None else None


async def list_uploads(conn: asyncpg.Connection) -> list[DataUpload]:
    rows = await conn.fetch(
        f"SELECT {_META_COLS} FROM data_uploads ORDER BY uploaded_at DESC"
    )
    return [_row_to_upload(r) for r in rows]


async def get_upload_content(
    conn: asyncpg.Connection, upload_id: UUID
) -> dict[str, Any] | None:
    """Return the parsed content blob (rows / text), or None if absent."""
    raw = await conn.fetchval(
        "SELECT content FROM data_uploads WHERE id = $1", upload_id
    )
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


async def delete_upload(conn: asyncpg.Connection, upload_id: UUID) -> bool:
    result = await conn.execute("DELETE FROM data_uploads WHERE id = $1", upload_id)
    return result.endswith("1")


__all__ = [
    "create_upload",
    "delete_upload",
    "get_upload",
    "get_upload_content",
    "list_uploads",
]
