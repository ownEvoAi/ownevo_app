"""read_upload agent-tool dispatch — scope enforcement + content shaping.

DB-backed (the dispatcher reads uploads through the store) but no parsing libs
needed: uploads are seeded directly via create_upload.
"""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest
from ownevo_kernel.data_ingest import UploadKind, create_upload
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.middleware.claude_sdk import KernelContext
from ownevo_kernel.middleware.claude_sdk.tool_definitions import dispatch_tool

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping read_upload dispatch tests",
)


def _ctx(db: asyncpg.Connection, upload_ids: tuple[str, ...]) -> KernelContext:
    return KernelContext(
        conn=db,
        sandbox=None,  # type: ignore[arg-type]
        actor="agent:test",
        upload_ids=upload_ids,
    )


async def _seed_csv(db: asyncpg.Connection):
    return await create_upload(
        db,
        name="orders.csv",
        kind=UploadKind.CSV,
        content_type="text/csv",
        size_bytes=10,
        sha256="x",
        schema={"columns": [{"name": "sku", "dtype": "object"}]},
        row_count=3,
        content={"rows": [{"sku": "A1"}, {"sku": "B2"}, {"sku": "C3"}]},
    )


async def test_read_spreadsheet_upload(db: asyncpg.Connection) -> None:
    upload = await _seed_csv(db)
    res = await dispatch_tool(
        "read_upload", {"upload_id": str(upload.id)}, _ctx(db, (str(upload.id),))
    )
    assert res.is_error is False
    assert res.output["kind"] == "csv"
    assert res.output["row_count"] == 3
    assert res.output["rows"][0] == {"sku": "A1"}
    assert res.output["truncated"] is False


async def test_max_rows_caps_and_flags_truncation(db: asyncpg.Connection) -> None:
    upload = await _seed_csv(db)
    res = await dispatch_tool(
        "read_upload",
        {"upload_id": str(upload.id), "max_rows": 2},
        _ctx(db, (str(upload.id),)),
    )
    assert len(res.output["rows"]) == 2
    assert res.output["truncated"] is True


async def test_document_upload_returns_text(db: asyncpg.Connection) -> None:
    upload = await create_upload(
        db,
        name="brief.pdf",
        kind=UploadKind.PDF,
        content_type="application/pdf",
        size_bytes=10,
        sha256="y",
        schema={"title": "Brief", "page_count": 2},
        row_count=None,
        content={"text": "the body", "sections": [{"level": 1, "heading": "Intro"}], "tables": []},
    )
    res = await dispatch_tool(
        "read_upload", {"upload_id": str(upload.id)}, _ctx(db, (str(upload.id),))
    )
    assert res.is_error is False
    assert res.output["text"] == "the body"
    assert res.output["sections"][0]["heading"] == "Intro"


async def test_undeclared_upload_rejected(db: asyncpg.Connection) -> None:
    upload = await _seed_csv(db)
    res = await dispatch_tool(
        "read_upload", {"upload_id": str(upload.id)}, _ctx(db, (str(uuid4()),))
    )
    assert res.is_error is True


async def test_missing_upload_is_error(db: asyncpg.Connection) -> None:
    missing = str(uuid4())
    res = await dispatch_tool("read_upload", {"upload_id": missing}, _ctx(db, (missing,)))
    assert res.is_error is True
    assert res.output["found"] is False
