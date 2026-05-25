"""Tie upload bytes -> parse -> store into one call.

`ingest_upload` is what the REST route calls: it detects the kind from the
filename, parses with the right parser, and persists the normalized form. The
parsers (and their heavy deps) are imported lazily inside this module's
functions so importing the package stays cheap.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from .models import DOCUMENT_KINDS, SPREADSHEET_KINDS, DataUpload, UploadKind
from .store import create_upload

if TYPE_CHECKING:
    import asyncpg

# Filename extension -> kind. The declared content-type is advisory only;
# browsers are inconsistent about it (especially for parquet), so the
# extension is authoritative.
_EXT_KIND = {
    ".csv": UploadKind.CSV,
    ".xlsx": UploadKind.EXCEL,
    ".xls": UploadKind.EXCEL,
    ".parquet": UploadKind.PARQUET,
    ".pdf": UploadKind.PDF,
    ".docx": UploadKind.DOCX,
}


class UnsupportedUpload(ValueError):
    """The filename extension maps to no supported upload kind."""


def detect_kind(filename: str) -> UploadKind:
    lower = filename.lower()
    for ext, kind in _EXT_KIND.items():
        if lower.endswith(ext):
            return kind
    raise UnsupportedUpload(
        f"unsupported file type for {filename!r}; supported: "
        f"{', '.join(sorted(_EXT_KIND))}"
    )


async def ingest_upload(
    conn: asyncpg.Connection,
    *,
    name: str,
    data: bytes,
    content_type: str | None = None,
    retention_expires_at: str | None = None,
) -> DataUpload:
    """Parse `data` per its detected kind and persist the parsed form."""
    kind = detect_kind(name)
    sha256 = hashlib.sha256(data).hexdigest()

    schema: dict[str, object]
    row_count: int | None
    content: dict[str, object]

    if kind in SPREADSHEET_KINDS:
        from .spreadsheet import parse_spreadsheet

        parsed = parse_spreadsheet(data, kind)
        schema = parsed.schema_
        row_count = parsed.row_count
        content = {"rows": parsed.rows}
    elif kind in DOCUMENT_KINDS:
        from .documents import parse_document

        doc = parse_document(data, kind)
        schema = doc.metadata
        row_count = None
        content = {"text": doc.text, "sections": doc.sections, "tables": doc.tables}
    else:  # pragma: no cover - detect_kind only returns known kinds
        raise UnsupportedUpload(f"no parser for kind {kind}")

    return await create_upload(
        conn,
        name=name,
        kind=kind,
        content_type=content_type,
        size_bytes=len(data),
        sha256=sha256,
        schema=schema,
        row_count=row_count,
        content=content,
        retention_expires_at=retention_expires_at,
    )


__all__ = ["UnsupportedUpload", "detect_kind", "ingest_upload"]
