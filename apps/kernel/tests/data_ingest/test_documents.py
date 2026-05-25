"""Document parser tests — DOCX (full structure) + PDF (structural + errors).

DOCX fixtures are generated with python-docx; the PDF structural test uses a
pypdf-written page. Skipped when the `data-ingest` extra isn't installed.
"""

from __future__ import annotations

import io

import pytest
from ownevo_kernel.data_ingest.documents import (
    DocumentParseError,
    _looks_like_heading,
    parse_document,
)
from ownevo_kernel.data_ingest.models import UploadKind

docx = pytest.importorskip("docx", reason="data-ingest extra (python-docx) not installed")
pypdf = pytest.importorskip("pypdf", reason="data-ingest extra (pypdf) not installed")


def test_docx_text_sections_and_tables() -> None:
    document = docx.Document()
    document.add_heading("Quarterly Review", level=1)
    document.add_paragraph("Intro paragraph.")
    document.add_heading("Risks", level=2)
    document.add_paragraph("Body text.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "metric"
    table.cell(0, 1).text = "value"
    table.cell(1, 0).text = "lift"
    table.cell(1, 1).text = "0.5"
    buf = io.BytesIO()
    document.save(buf)

    parsed = parse_document(buf.getvalue(), UploadKind.DOCX)
    assert "Intro paragraph." in parsed.text
    headings = [(s["level"], s["heading"]) for s in parsed.sections]
    assert (1, "Quarterly Review") in headings
    assert (2, "Risks") in headings
    assert parsed.metadata["table_count"] == 1
    assert parsed.tables[0][0] == ["metric", "value"]
    assert parsed.metadata["tables_supported"] is True


def test_pdf_structural_path() -> None:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)

    parsed = parse_document(buf.getvalue(), UploadKind.PDF)
    assert parsed.metadata["page_count"] == 1
    assert parsed.metadata["tables_supported"] is False
    assert isinstance(parsed.text, str)


def test_corrupt_pdf_raises() -> None:
    with pytest.raises(DocumentParseError):
        parse_document(b"%PDF-not-really", UploadKind.PDF)


def test_heading_heuristic() -> None:
    assert _looks_like_heading("EXECUTIVE SUMMARY")
    assert _looks_like_heading("Demand Forecast Overview")
    assert not _looks_like_heading("This is a normal sentence that ends with a period.")
    assert not _looks_like_heading("")
    assert not _looks_like_heading("a" * 200)
