"""Spreadsheet parser tests — CSV / Excel / Parquet.

Fixtures are generated with pandas (the same backend the parser uses), so the
tests are self-contained. Skipped when the `data-ingest` extra isn't installed.
"""

from __future__ import annotations

import io

import pytest

pd = pytest.importorskip("pandas", reason="data-ingest extra (pandas) not installed")

from ownevo_kernel.data_ingest.models import UploadKind  # noqa: E402
from ownevo_kernel.data_ingest.spreadsheet import (  # noqa: E402
    SpreadsheetParseError,
    parse_spreadsheet,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sku": ["A1", "B2", "C3"],
            "units": [10, 20, 30],
            "price": [1.5, 2.0, None],  # a NaN to exercise JSON coercion
        }
    )


def test_parse_csv_schema_and_rows() -> None:
    data = _frame().to_csv(index=False).encode()
    parsed = parse_spreadsheet(data, UploadKind.CSV)
    assert parsed.row_count == 3
    names = [c["name"] for c in parsed.schema_["columns"]]
    assert names == ["sku", "units", "price"]
    assert parsed.rows[0] == {"sku": "A1", "units": 10, "price": 1.5}
    # NaN coerces to None so the rows are JSON-safe.
    assert parsed.rows[2]["price"] is None


def test_parse_excel() -> None:
    pytest.importorskip("openpyxl", reason="data-ingest extra (openpyxl) not installed")
    buf = io.BytesIO()
    _frame().to_excel(buf, index=False)
    parsed = parse_spreadsheet(buf.getvalue(), UploadKind.EXCEL)
    assert parsed.row_count == 3
    assert {c["name"] for c in parsed.schema_["columns"]} == {"sku", "units", "price"}


def test_parse_parquet() -> None:
    # Parquet needs a pandas engine (pyarrow/fastparquet), which isn't pinned
    # in the extra; skip when none is installed.
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        try:
            import fastparquet  # noqa: F401
        except ImportError:
            pytest.skip("no parquet engine (pyarrow/fastparquet) installed")
    buf = io.BytesIO()
    _frame().to_parquet(buf, index=False)
    parsed = parse_spreadsheet(buf.getvalue(), UploadKind.PARQUET)
    assert parsed.row_count == 3
    assert parsed.rows[1]["sku"] == "B2"


def test_hundred_row_csv_round_trips() -> None:
    df = pd.DataFrame({"i": list(range(100)), "v": [f"row-{i}" for i in range(100)]})
    parsed = parse_spreadsheet(df.to_csv(index=False).encode(), UploadKind.CSV)
    assert parsed.row_count == 100
    assert len(parsed.rows) == 100
    assert parsed.rows[99] == {"i": 99, "v": "row-99"}


def test_malformed_parquet_raises() -> None:
    with pytest.raises(SpreadsheetParseError):
        parse_spreadsheet(b"not a parquet file", UploadKind.PARQUET)
