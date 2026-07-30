"""The F0 baseline parser: plain text and fixed-size chunks.

This is deliberately naive, and the naivety is the point. F0 exists so that any
gain attributed to structure-aware parsing is measured against something a
competent engineer would actually build first, not against a strawman -- so the
baseline still gets real text extraction, real page attribution, and the same
answer and citation contract as the candidate. What it does not get is any notion
of headings, sections, tables, or figures.

Page-level attribution is kept even here, because a baseline that could not cite a
page would fail the citation metrics for a reason unrelated to parsing quality,
and that would flatter the candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from twfi.errors import ParsingError
from twfi.parsing.types import BBox, Block, Chunk, PageRef, ParsedDocument, ParsedPage

__all__ = ["PARSER_NAME", "FixedChunkConfig", "parse_baseline", "chunk_fixed"]

PARSER_NAME = "pymupdf-plain"


@dataclass(frozen=True, slots=True)
class FixedChunkConfig:
    """Protocol 2.5 fixes these values; they are not tuned per document."""

    size: int = 800
    overlap: int = 100

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError("chunk size must be positive")
        if not 0 <= self.overlap < self.size:
            raise ValueError("overlap must be in [0, size)")


def parse_baseline(pdf_path: Path, doc_id: str) -> ParsedDocument:
    """Extract each page's text as a single undifferentiated block.

    Raises:
        ParsingError: If the file cannot be opened as a PDF.
    """
    try:
        document = pymupdf.open(pdf_path)  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise ParsingError(f"cannot open {pdf_path} as a PDF: {exc}") from exc

    pages: list[ParsedPage] = []
    with document:
        for index in range(1, document.page_count + 1):
            page = document.load_page(index - 1)  # type: ignore[no-untyped-call]
            rect = page.rect
            text = str(page.get_text()).strip()
            bbox = BBox(0.0, 0.0, float(rect.width), float(rect.height))
            blocks = (
                (
                    Block(
                        page=index,
                        kind="paragraph",
                        text=text,
                        bbox=bbox,
                        order=0,
                    ),
                )
                if text
                else ()
            )
            pages.append(
                ParsedPage(
                    number=index,
                    width=float(rect.width),
                    height=float(rect.height),
                    blocks=blocks,
                )
            )

    return ParsedDocument(doc_id=doc_id, parser=PARSER_NAME, pages=tuple(pages))


def chunk_fixed(document: ParsedDocument, config: FixedChunkConfig | None = None) -> list[Chunk]:
    """Slide a fixed character window over the document's text.

    Chunks may cut mid-sentence and mid-table; that is what a fixed chunker does.
    Page attribution is preserved by tracking each page's offset range in the
    concatenated text, so a chunk that straddles a page boundary cites both pages.
    """
    config = config or FixedChunkConfig()

    pieces: list[str] = []
    spans: list[tuple[int, int, int, BBox]] = []  # (start, end, page, page bbox)
    cursor = 0
    for page in document.pages:
        text = page.text
        if not text:
            continue
        if pieces:
            pieces.append("\n")
            cursor += 1
        pieces.append(text)
        spans.append(
            (cursor, cursor + len(text), page.number, BBox(0.0, 0.0, page.width, page.height))
        )
        cursor += len(text)

    full_text = "".join(pieces)
    if not full_text:
        return []

    step = config.size - config.overlap
    chunks: list[Chunk] = []
    start = 0
    while start < len(full_text):
        end = min(start + config.size, len(full_text))
        body = full_text[start:end]
        if body.strip():
            refs = tuple(
                PageRef(page=page, bbox=bbox)
                for (page_start, page_end, page, bbox) in spans
                if page_start < end and page_end > start
            )
            chunks.append(
                Chunk(
                    chunk_id=f"{document.doc_id}:fixed:{len(chunks):05d}",
                    doc_id=document.doc_id,
                    text=body,
                    refs=refs,
                    kinds=("paragraph",),
                    parser=document.parser,
                )
            )
        if end == len(full_text):
            break
        start += step

    return chunks
