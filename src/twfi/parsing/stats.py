"""Parse statistics: what each parser actually recovers, per document.

This is the artifact behind the P3 definition of done, and it is also the first
honest look at whether the structure-aware parser earns its place. It reports the
same counts for both parsers plus ingestion timing, so the comparison is visible
before any retrieval or generation is involved -- a parser that finds no headings
in a real filing cannot be rescued by a downstream stage.

Timing is measured here rather than inferred later, because ingestion latency is a
declared systems metric (protocol 3.6).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from twfi.parsing.baseline import chunk_fixed, parse_baseline
from twfi.parsing.chunker import chunk_structure_aware
from twfi.parsing.layout import parse_layout
from twfi.parsing.types import Chunk, ParsedDocument

__all__ = ["ChunkStats", "ParserRun", "DocumentComparison", "chunk_stats", "compare_parsers"]


@dataclass(frozen=True, slots=True)
class ChunkStats:
    """Aggregate shape of one chunking strategy's output."""

    count: int
    mean_chars: float
    max_chars: int
    with_section_path: int
    cross_page: int

    def to_json(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "mean_chars": round(self.mean_chars, 1),
            "max_chars": self.max_chars,
            "with_section_path": self.with_section_path,
            "cross_page": self.cross_page,
        }


def chunk_stats(chunks: list[Chunk]) -> ChunkStats:
    """Summarise a chunk list, tolerating an empty one."""
    if not chunks:
        return ChunkStats(count=0, mean_chars=0.0, max_chars=0, with_section_path=0, cross_page=0)
    lengths = [chunk.char_count for chunk in chunks]
    return ChunkStats(
        count=len(chunks),
        mean_chars=sum(lengths) / len(lengths),
        max_chars=max(lengths),
        with_section_path=sum(1 for chunk in chunks if chunk.section_path),
        cross_page=sum(1 for chunk in chunks if chunk.spans_pages),
    )


@dataclass(frozen=True, slots=True)
class ParserRun:
    """One parser's output on one document, with the time it took."""

    parser: str
    parse_seconds: float
    chunk_seconds: float
    blocks: dict[str, int] = field(default_factory=dict)
    chunks: ChunkStats = field(default_factory=lambda: ChunkStats(0, 0.0, 0, 0, 0))

    @property
    def seconds_per_page(self) -> float:
        pages = self.blocks.get("pages", 0)
        return self.parse_seconds / pages if pages else 0.0

    def to_json(self) -> dict[str, object]:
        return {
            "parser": self.parser,
            "parse_seconds": round(self.parse_seconds, 3),
            "chunk_seconds": round(self.chunk_seconds, 3),
            "seconds_per_page": round(self.seconds_per_page, 4),
            "blocks": dict(self.blocks),
            "chunks": self.chunks.to_json(),
        }


@dataclass(frozen=True, slots=True)
class DocumentComparison:
    """Both parsers on one document."""

    doc_id: str
    pages: int
    bytes: int
    baseline: ParserRun
    candidate: ParserRun

    def to_json(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "pages": self.pages,
            "bytes": self.bytes,
            "baseline": self.baseline.to_json(),
            "candidate": self.candidate.to_json(),
        }


def _timed(
    action: Callable[[], ParsedDocument | list[Chunk]], monotonic: Callable[[], float]
) -> tuple[ParsedDocument | list[Chunk], float]:
    started = monotonic()
    result = action()
    return result, monotonic() - started


def compare_parsers(
    pdf_path: Path,
    doc_id: str,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> DocumentComparison:
    """Run both parsers over one document and report what each recovered."""
    baseline_document, baseline_parse = _timed(lambda: parse_baseline(pdf_path, doc_id), monotonic)
    assert isinstance(baseline_document, ParsedDocument)
    baseline_chunks, baseline_chunk_time = _timed(lambda: chunk_fixed(baseline_document), monotonic)
    assert isinstance(baseline_chunks, list)

    candidate_document, candidate_parse = _timed(lambda: parse_layout(pdf_path, doc_id), monotonic)
    assert isinstance(candidate_document, ParsedDocument)
    candidate_chunks, candidate_chunk_time = _timed(
        lambda: chunk_structure_aware(candidate_document), monotonic
    )
    assert isinstance(candidate_chunks, list)

    return DocumentComparison(
        doc_id=doc_id,
        pages=candidate_document.page_count,
        bytes=pdf_path.stat().st_size,
        baseline=ParserRun(
            parser=baseline_document.parser,
            parse_seconds=baseline_parse,
            chunk_seconds=baseline_chunk_time,
            blocks=baseline_document.stats(),
            chunks=chunk_stats(baseline_chunks),
        ),
        candidate=ParserRun(
            parser=candidate_document.parser,
            parse_seconds=candidate_parse,
            chunk_seconds=candidate_chunk_time,
            blocks=candidate_document.stats(),
            chunks=chunk_stats(candidate_chunks),
        ),
    )
