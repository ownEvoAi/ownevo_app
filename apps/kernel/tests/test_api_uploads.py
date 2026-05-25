"""Integration tests for /api/uploads against a real DB.

The headline path: upload a 100-row CSV via multipart, then read its rows back
through the content endpoint. Skipped without the `data-ingest` extra (the
upload route parses with pandas).
"""

from __future__ import annotations

import os

import httpx
import pytest
from ownevo_kernel.db import ENV_VAR

pytest.importorskip("pandas", reason="data-ingest extra (pandas) not installed")

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping uploads API tests",
)


def _csv_100() -> bytes:
    lines = ["sku,units"] + [f"S{i},{i}" for i in range(100)]
    return ("\n".join(lines) + "\n").encode()


async def test_upload_csv_then_read_rows(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.post(
        "/api/uploads",
        files={"file": ("orders.csv", _csv_100(), "text/csv")},
    )
    assert resp.status_code == 201
    meta = resp.json()
    assert meta["kind"] == "csv"
    assert meta["row_count"] == 100
    assert [c["name"] for c in meta["schema"]["columns"]] == ["sku", "units"]
    upload_id = meta["id"]

    # Metadata view via GET.
    got = await api_client.get(f"/api/uploads/{upload_id}")
    assert got.status_code == 200
    assert got.json()["row_count"] == 100

    # Parsed content via the content endpoint.
    content = await api_client.get(f"/api/uploads/{upload_id}/content")
    assert content.status_code == 200
    rows = content.json()["rows"]
    assert len(rows) == 100
    assert rows[0] == {"sku": "S0", "units": 0}

    listed = await api_client.get("/api/uploads")
    assert any(u["id"] == upload_id for u in listed.json())

    deleted = await api_client.delete(f"/api/uploads/{upload_id}")
    assert deleted.status_code == 204
    assert (await api_client.get(f"/api/uploads/{upload_id}")).status_code == 404


async def test_unsupported_type_is_415(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.post(
        "/api/uploads",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 415


async def test_empty_file_is_422(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.post(
        "/api/uploads",
        files={"file": ("orders.csv", b"", "text/csv")},
    )
    assert resp.status_code == 422
