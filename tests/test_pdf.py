"""Real PDF conversion smoke check; run separately because models download on first use."""

import os

import pytest

from backend.parsing import parse_document


@pytest.mark.skipif(os.getenv("RUN_PDF_SMOKE") != "1", reason="Opt-in Docling PDF model download")
def test_pdf_page_provenance(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    path = tmp_path / "finance.pdf"
    canvas = Canvas(str(path))
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawString(72, 750, "Quarterly Revenue")
    canvas.setFont("Helvetica", 12)
    canvas.drawString(72, 700, "APAC revenue was $82M. Growth was 12 percent.")
    canvas.showPage()
    canvas.setFont("Helvetica", 12)
    canvas.drawString(72, 700, "Operating expenses were $171M.")
    canvas.save()
    blocks, _ = parse_document(path)
    assert any("82M" in b.text and b.page == 1 for b in blocks)
    assert any("171M" in b.text and b.page == 2 for b in blocks)


@pytest.mark.skipif(os.getenv("RUN_PDF_SMOKE") != "1", reason="Opt-in Docling PDF models")
def test_docling_pdf_preserves_table_and_page(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    path = tmp_path / "table.pdf"
    canvas = Canvas(str(path))
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawString(72, 750, "Quarterly Revenue")
    canvas.setFont("Helvetica", 12)
    canvas.drawString(72, 710, "Regional revenue for the quarter is reported in the table below.")
    for y, left, right in [(660, "Region", "Revenue"), (620, "APAC", "$82M"),
                           (580, "Europe", "$65M")]:
        canvas.drawString(85, y, left)
        canvas.drawString(300, y, right)
    for y in [685, 645, 605, 565]:
        canvas.line(72, y, 450, y)
    for x in [72, 280, 450]:
        canvas.line(x, 565, x, 685)
    canvas.save()
    details = {}
    blocks, _ = parse_document(path, details=details, pdf_mode="docling")
    assert details["parser"] == "docling"
    assert details["ocr_enabled"] is False
    assert any(b.content_type == "table" and "82M" in b.text and "65M" in b.text
               and b.page == 1 for b in blocks)
