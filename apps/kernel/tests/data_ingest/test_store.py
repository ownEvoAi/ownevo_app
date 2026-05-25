"""DB round-trip for the data_uploads store."""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.data_ingest import (
    UploadKind,
    create_upload,
    delete_upload,
    get_upload,
    get_upload_content,
    list_uploads,
)
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping data_uploads store tests",
)


async def test_create_get_list_content_delete(db: asyncpg.Connection) -> None:
    upload = await create_upload(
        db,
        name="orders.csv",
        kind=UploadKind.CSV,
        content_type="text/csv",
        size_bytes=42,
        sha256="abc123",
        schema={"columns": [{"name": "sku", "dtype": "object"}]},
        row_count=3,
        content={"rows": [{"sku": "A1"}, {"sku": "B2"}, {"sku": "C3"}]},
    )
    assert upload.kind is UploadKind.CSV
    assert upload.row_count == 3
    # The metadata view carries no bulky content.
    assert not hasattr(upload, "content")

    fetched = await get_upload(db, upload.id)
    assert fetched is not None
    assert fetched.name == "orders.csv"
    assert fetched.schema_["columns"][0]["name"] == "sku"

    content = await get_upload_content(db, upload.id)
    assert content == {"rows": [{"sku": "A1"}, {"sku": "B2"}, {"sku": "C3"}]}

    listed = await list_uploads(db)
    assert any(u.id == upload.id for u in listed)

    assert await delete_upload(db, upload.id) is True
    assert await get_upload(db, upload.id) is None
    assert await get_upload_content(db, upload.id) is None
