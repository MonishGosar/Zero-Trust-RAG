import re
from functools import lru_cache
from pathlib import Path

import tiktoken

from backend.models import Block, Chunk

ENCODER = tiktoken.get_encoding("cl100k_base")


def tokens(text: str) -> int:
    return len(ENCODER.encode(text, disallowed_special=()))


def parse_markdown(text: str) -> list[Block]:
    """Preserve headings, fenced code, and contiguous Markdown tables."""
    blocks: list[Block] = []
    section = ""
    paragraph: list[str] = []
    kind = "text"
    fence = False

    def flush():
        if paragraph:
            blocks.append(
                Block(text="\n".join(paragraph).strip(), section=section, content_type=kind)
            )
            paragraph.clear()

    lines = text.replace("\r\n", "\n").splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        heading = re.match(r"^#{1,6}\s+(.+)$", line) if not fence else None
        if heading:
            flush()
            section = heading.group(1).strip()
            kind = "text"
            continue
        next_is_separator = i + 1 < len(lines) and re.match(
            r"^\s*\|?\s*:?-{3,}:?\s*\|", lines[i + 1]
        )
        is_table = not fence and "|" in line and (next_is_separator or kind == "table")
        new_kind = "table" if is_table else "text"
        if new_kind != kind:
            flush()
            kind = new_kind
        if not line.strip() and not fence:
            flush()
            kind = "text"
        else:
            paragraph.append(line)
    flush()
    return blocks


@lru_cache(maxsize=2)
def converter(do_ocr: bool = True):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(
        pipeline_options=PdfPipelineOptions(do_ocr=do_ocr)
    )})


def parse_document(path: Path, *, details: dict | None = None,
                   pdf_mode: str = "auto") -> tuple[list[Block], list[str]]:
    details = details if details is not None else {}
    if path.suffix.lower() in {".txt", ".md"}:
        details.update(parser="native-text", ocr_enabled=False)
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".md":
            return parse_markdown(text), []
        return [Block(text=p.strip()) for p in text.split("\n\n") if p.strip()], []

    do_ocr = True
    if path.suffix.lower() == ".pdf":
        from backend.pdf_native import inspect_pdf

        profile = inspect_pdf(path)
        details.update(pages=profile.pages, routing_reasons=profile.reasons)
        if profile.fast_eligible and pdf_mode == "auto":
            details.update(parser="pdf-native", ocr_enabled=False)
            return profile.blocks, []
        do_ocr = profile.needs_ocr
    else:
        do_ocr = False
    details.update(parser="docling", ocr_enabled=do_ocr)

    from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TextItem

    result = converter(do_ocr).convert(path, max_num_pages=200)
    # Partial conversion would silently omit evidence; require a complete conversion.
    if result.status.value != "success":
        raise ValueError("Document conversion was incomplete. Try a smaller or repaired document.")
    doc = result.document
    blocks = []
    section = ""
    warnings = []
    for item, _ in doc.iterate_items():
        page = item.prov[0].page_no if item.prov and path.suffix.lower() == ".pdf" else None
        if isinstance(item, SectionHeaderItem):
            section = item.text
        elif isinstance(item, TableItem):
            blocks.append(
                Block(
                    text=item.export_to_markdown(doc=doc),
                    content_type="table",
                    section=section,
                    page=page,
                )
            )
        elif isinstance(item, PictureItem):
            caption = item.caption_text(doc=doc)
            if caption:
                blocks.append(
                    Block(text=caption, content_type="picture", section=section, page=page)
                )
            warnings.append(
                "Images/charts are indexed by extracted captions only; visual values "
                "are not interpreted."
            )
        elif isinstance(item, TextItem) and item.text.strip():
            blocks.append(Block(text=item.text, section=section, page=page))
    return blocks, list(dict.fromkeys(warnings))


def chunk_document(
    blocks: list[Block], document_id: str, filename: str, target_tokens: int = 650
) -> list[Chunk]:
    from uuid import uuid4

    chunks: list[Chunk] = []
    pending: list[int] = []
    current: Block | None = None

    def emit(block: Block, text: str):
        full = f"{block.section}\n\n{text}" if block.section else text
        if tokens(full) > 6000:
            raise ValueError(
                "A table or section exceeds 6,000 tokens. Split it into smaller "
                "logical tables/sections before uploading; rows will not be cut."
            )
        chunks.append(
            Chunk(
                **block.model_dump(exclude={"text"}),
                text=full,
                chunk_id=str(uuid4()),
                document_id=document_id,
                filename=filename,
                token_count=tokens(full),
                page_end=block.page,
            )
        )

    def flush():
        nonlocal pending
        if pending and current:
            emit(current, ENCODER.decode(pending, errors="strict"))
        pending = []

    for block in blocks:
        if not block.text.strip():
            continue
        if block.content_type != "text":
            flush()
            emit(block, block.text)
            continue
        if current and (block.section, block.page) != (current.section, current.page):
            flush()
        current = block
        prefix = f"{block.section}\n\n" if block.section else ""
        budget = target_tokens - tokens(prefix)
        if budget < 8:
            budget = 8
        # Encode each paragraph once. Decode only completed chunks, keeping UTF-8
        # boundaries intact even for languages whose characters span multiple BPE tokens.
        encoded = ENCODER.encode(block.text.strip() + "\n\n", disallowed_special=())
        offset = 0
        while offset < len(encoded):
            available = budget - len(pending)
            if available < 4:
                flush()
                available = budget
            end = min(len(encoded), offset + available)
            while end > offset:
                try:
                    ENCODER.decode(encoded[offset:end], errors="strict")
                    break
                except UnicodeDecodeError:
                    end -= 1
            if end == offset:
                raise ValueError("Document contains text that cannot be split safely.")
            # Prefer whitespace boundaries near the end so prose words remain intact.
            if end < len(encoded):
                for candidate in range(end, max(offset, end - 24), -1):
                    if ENCODER.decode_single_token_bytes(encoded[candidate]).startswith(b" "):
                        end = candidate
                        break
            pending.extend(encoded[offset:end])
            offset = end
            if offset < len(encoded):
                flush()
    flush()
    if not chunks:
        raise ValueError("No readable text was found in this document.")
    if len(chunks) > 2000:
        raise ValueError("Document exceeds the 2,000 chunk limit. Upload smaller documents.")
    return chunks
