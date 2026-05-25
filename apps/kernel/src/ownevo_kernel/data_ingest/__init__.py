"""Direct file uploads as agent data sources (Track 17.0.3 / 17.0.4).

For data that lives in spreadsheets and documents rather than connected
systems: a reviewer uploads a CSV / Excel / Parquet / PDF / DOCX file, ownEvo
parses it once into a normalized form, and the workflow's agent reads that form
by id on every iteration via the `read_upload` tool.

Layering:
  models      — typed upload + parsed-result records
  spreadsheet — CSV / Excel / Parquet -> schema + JSON-safe rows (lazy pandas)
  documents   — PDF / DOCX -> text + title/sections/tables (lazy pypdf/docx)
  store       — DB access for the data_uploads table
  ingest      — detect kind, parse, persist (the REST route's entry point)
"""

from .ingest import UnsupportedUpload, detect_kind, ingest_upload
from .models import (
    DOCUMENT_KINDS,
    SPREADSHEET_KINDS,
    DataUpload,
    ParsedDocument,
    ParsedSpreadsheet,
    UploadKind,
)
from .store import (
    create_upload,
    delete_upload,
    get_upload,
    get_upload_content,
    list_uploads,
)

__all__ = [
    "DOCUMENT_KINDS",
    "SPREADSHEET_KINDS",
    "DataUpload",
    "ParsedDocument",
    "ParsedSpreadsheet",
    "UnsupportedUpload",
    "UploadKind",
    "create_upload",
    "delete_upload",
    "detect_kind",
    "get_upload",
    "get_upload_content",
    "ingest_upload",
    "list_uploads",
]
