"""Parse CSV / Excel / Parquet uploads into a schema + JSON-safe rows.

pandas (and its parquet/xlsx backends) are imported lazily so the kernel core
stays pandas-free; only a process that actually parses an upload needs the
`data-ingest` extra installed.

Cells are coerced to JSON-safe values via pandas' own `to_json` (NaN -> null,
numpy scalars -> native, timestamps -> ISO), so the rows round-trip cleanly
through the `data_uploads.content` JSONB column.
"""

from __future__ import annotations

import io
import json

from .models import ParsedSpreadsheet, UploadKind


class SpreadsheetParseError(ValueError):
    """The uploaded bytes could not be parsed as the declared kind."""


def parse_spreadsheet(data: bytes, kind: UploadKind) -> ParsedSpreadsheet:
    """Parse spreadsheet bytes into columns + rows.

    Raises `SpreadsheetParseError` on malformed input or a missing backend.
    """
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SpreadsheetParseError(
            "the `data-ingest` extra (pandas) is required to parse spreadsheets"
        ) from exc

    buf = io.BytesIO(data)
    try:
        if kind is UploadKind.CSV:
            df = pd.read_csv(buf)
        elif kind is UploadKind.EXCEL:
            df = pd.read_excel(buf)
        elif kind is UploadKind.PARQUET:
            df = pd.read_parquet(buf)
        else:  # pragma: no cover - guarded by caller
            raise SpreadsheetParseError(f"not a spreadsheet kind: {kind}")
    except SpreadsheetParseError:
        raise
    except Exception as exc:
        raise SpreadsheetParseError(f"could not parse {kind} upload: {exc}") from exc

    columns = [
        {"name": str(name), "dtype": str(dtype)}
        for name, dtype in zip(df.columns, df.dtypes, strict=True)
    ]
    # to_json coerces NaN -> null, numpy scalars -> native, timestamps -> ISO;
    # parsing it back yields plain JSON-safe Python objects.
    rows = json.loads(df.to_json(orient="records", date_format="iso"))
    return ParsedSpreadsheet(
        schema={"columns": columns},
        row_count=int(len(df)),
        rows=rows,
    )


__all__ = ["SpreadsheetParseError", "parse_spreadsheet"]
