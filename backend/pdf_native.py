"""Conservative PDF inspection. Call only from the single parsing worker (PDFium is not
thread-safe). Ambiguous layouts always go through Docling rather than flattening tables.
"""

import re
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from backend.models import Block


@dataclass
class PdfInspection:
    blocks: list[Block]
    pages: int
    fast_eligible: bool
    needs_ocr: bool
    reasons: list[str]


def inspect_pdf(path: Path) -> PdfInspection:
    blocks = []
    reasons: set[str] = set()
    needs_ocr = False
    section = ""
    with pdfium.PdfDocument(path) as pdf:
        if len(pdf) > 200:
            raise ValueError("Document exceeds the 200 page limit.")
        pages = len(pdf)
        for number in range(pages):
            with closing(pdf[number]) as page, closing(page.get_textpage()) as textpage:
                native = textpage.get_text_range()
                visible = re.sub(r"\s", "", native)
                runs = []
                has_images = False
                has_graphics = False
                for obj in page.get_objects(textpage=textpage):
                    if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                        has_images = True
                        reasons.add("image content")
                    elif obj.type in {raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_SHADING}:
                        has_graphics = True
                        reasons.add("vector graphics or table rules")
                    elif obj.type == raw.FPDF_PAGEOBJ_FORM:
                        reasons.add("nested form content")
                    elif obj.type == raw.FPDF_PAGEOBJ_TEXT:
                        matrix = obj.get_matrix()
                        if abs(matrix.b) > 0.001 or abs(matrix.c) > 0.001:
                            reasons.add("rotated or skewed text")
                        text = obj.extract().strip()
                        if text:
                            left, bottom, right, top = obj.get_bounds()
                            runs.append((left, bottom, right, top, obj.get_font_size(), text))
                if (has_images or ((runs or has_graphics) and not visible)
                        or "\ufffd" in native):
                    needs_ocr = True
                    reasons.add("OCR coverage required")
                if page.get_rotation():
                    reasons.add("rotated page")
                # Missing/garbled native extraction must not be accepted as a fast success.
                extracted = re.sub(r"\s", "", "".join(run[5] for run in runs))
                if len(extracted) != len(visible):
                    reasons.add("ambiguous text extraction")
                if not runs:
                    if visible or has_images:
                        needs_ocr = True
                    continue
                # Group adjacent text runs into lines; distant cells on the same baseline
                # indicate columns or a borderless table, requiring structure reconstruction.
                lines: list[list] = []
                for run in sorted(runs, key=lambda r: (-r[3], r[0])):
                    matching = next((line for line in reversed(lines[-3:])
                                     if abs(line[3] - run[3]) <= 3), None)
                    if matching is None:
                        lines.append(list(run))
                    else:
                        if run[0] - matching[2] > 20 or run[2] < matching[0] - 20:
                            reasons.add("columns or table cells")
                        matching[5] += " " + run[5]
                        matching[2] = max(matching[2], run[2])
                        matching[4] = max(matching[4], run[4])
                # Large jumps in indentation across lines can also indicate staggered columns.
                body_lines = [line for line in lines if len(line[5]) > 45]
                if body_lines and max(line[0] for line in body_lines) - min(
                    line[0] for line in body_lines
                ) > page.get_width() * 0.18:
                    reasons.add("ambiguous reading order")
                body_size = median([r[4] for r in runs for _ in range(min(len(r[5]), 200))])
                paragraph = []

                def flush():
                    if paragraph:
                        blocks.append(Block(text="\n".join(paragraph), section=section,
                                            page=number + 1))
                        paragraph.clear()

                for line in lines:
                    text = line[5]
                    if len(text) < 140 and line[4] >= body_size * 1.18:
                        flush()
                        section = text
                    paragraph.append(text)
                flush()
    return PdfInspection(blocks, pages, bool(blocks) and not reasons,
                         needs_ocr, sorted(reasons))
