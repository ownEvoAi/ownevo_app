"""Typed models for direct file uploads as agent data sources.

`DataUpload` is the non-bulky metadata view (what list/detail endpoints
return); the parsed content (spreadsheet rows / document text) is fetched
separately so a listing stays token-bounded.

The parsers return `ParsedSpreadsheet` / `ParsedDocument`; the store flattens
those into the `schema` + `content` JSONB columns.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadKind(StrEnum):
    CSV = "csv"
    EXCEL = "excel"
    PARQUET = "parquet"
    PDF = "pdf"
    DOCX = "docx"


# Spreadsheet kinds parse to rows; document kinds parse to text + metadata.
SPREADSHEET_KINDS = frozenset({UploadKind.CSV, UploadKind.EXCEL, UploadKind.PARQUET})
DOCUMENT_KINDS = frozenset({UploadKind.PDF, UploadKind.DOCX})


class DataUpload(_Base):
    """Metadata view of an uploaded file (no parsed content)."""

    id: UUID
    name: str
    kind: UploadKind
    content_type: str | None = None
    size_bytes: int
    sha256: str
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    row_count: int | None = None
    uploaded_at: str
    retention_expires_at: str | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ParsedSpreadsheet(_Base):
    """Output of the spreadsheet parser.

    `schema` describes columns + dtypes; `rows` are JSON-coerced records
    (NaN -> None, timestamps -> ISO strings) so they round-trip through JSONB.
    """

    schema_: dict[str, Any] = Field(alias="schema")
    row_count: int
    rows: list[dict[str, Any]]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ParsedDocument(_Base):
    """Output of the document parser.

    `metadata` carries the surfaced structured fields (title, section
    headings, table count); `text` is the full extracted text; `sections`
    and `tables` are the structured extractions.
    """

    metadata: dict[str, Any]
    text: str
    sections: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)


__all__ = [
    "DOCUMENT_KINDS",
    "SPREADSHEET_KINDS",
    "DataUpload",
    "ParsedDocument",
    "ParsedSpreadsheet",
    "UploadKind",
]
