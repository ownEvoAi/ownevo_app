"""`/api/uploads` — direct file uploads as agent data sources.

A reviewer uploads a CSV / Excel / Parquet / PDF / DOCX file; ownEvo parses it
once and stores the normalized form. List/detail return metadata (+ detected
schema); the parsed content (rows / text) is fetched from the `/content`
endpoint so a listing stays small.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, UploadFile, status

from ...data_ingest import (
    DataUpload,
    UnsupportedUpload,
    get_upload,
    get_upload_content,
    ingest_upload,
    list_uploads,
)
from ...data_ingest import delete_upload as _delete_upload
from ..deps import ConnDep, DemoModeCheck

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# Bounds the parsed-content footprint in Postgres + the request body. Generous
# for the 100-row CSV / 20-page PDF the connectors target.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_OVERSIZE_DETAIL = f"file exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit"


@router.get("", response_model=list[DataUpload])
async def list_data_uploads(conn: ConnDep) -> list[DataUpload]:
    return await list_uploads(conn)


@router.post("", response_model=DataUpload, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    conn: ConnDep,
    _demo: DemoModeCheck,
    file: UploadFile,
) -> DataUpload:
    """Upload + parse a file. 415 for an unsupported type, 422 for parse errors."""
    # Reject oversized uploads before buffering the body. Content-Length is
    # advisory (clients may omit it), so we also re-check after the read.
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=_OVERSIZE_DETAIL,
                )
        except ValueError:
            pass  # malformed Content-Length — fall through to the post-read check
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty file"
        )
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=_OVERSIZE_DETAIL,
        )
    name = file.filename or "upload"
    try:
        return await ingest_upload(
            conn, name=name, data=data, content_type=file.content_type
        )
    except UnsupportedUpload as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from None
    except ValueError as exc:
        # Parser errors (malformed CSV, corrupt PDF) — the file type is
        # supported but the bytes didn't parse.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None


@router.get("/{upload_id}", response_model=DataUpload)
async def get_data_upload(upload_id: UUID, conn: ConnDep) -> DataUpload:
    upload = await get_upload(conn, upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no upload with id {upload_id}",
        )
    return upload


@router.get("/{upload_id}/content")
async def get_data_upload_content(upload_id: UUID, conn: ConnDep) -> dict[str, Any]:
    """The parsed content: {"rows": [...]} for spreadsheets, {"text", "sections",
    "tables"} for documents."""
    content = await get_upload_content(conn, upload_id)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no upload with id {upload_id}",
        )
    return content


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_upload(
    upload_id: UUID, conn: ConnDep, _demo: DemoModeCheck
) -> None:
    """Remove an upload. Idempotent."""
    await _delete_upload(conn, upload_id)
