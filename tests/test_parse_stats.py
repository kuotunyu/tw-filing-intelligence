"""Parse statistics must make the two parsers comparable on the same document."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.synthetic_pdf import build_empty_pdf, build_filing
from twfi.parsing.baseline import PARSER_NAME as BASELINE_PARSER
from twfi.parsing.layout import PARSER_NAME as CANDIDATE_PARSER
from twfi.parsing.stats import chunk_stats, compare_parsers
from twfi.parsing.types import BBox, Chunk, PageRef


def chunk(text: str, *, pages: tuple[int, ...] = (1,), section: tuple[str, ...] = ()) -> Chunk:
    return Chunk(
        chunk_id="c",
        doc_id="d",
        text=text,
        refs=tuple(PageRef(page=page, bbox=BBox(0, 0, 10, 10)) for page in pages),
        section_path=section,
    )


# ------------------------------------------------------------------ chunk stats


def test_chunk_stats_of_nothing_is_all_zero() -> None:
    stats = chunk_stats([])
    assert stats.count == 0
    assert stats.mean_chars == 0.0
    assert stats.max_chars == 0
    assert stats.cross_page == 0


def test_chunk_stats_aggregates() -> None:
    stats = chunk_stats(
        [
            chunk("abc", section=("A",)),
            chunk("abcdefghij", pages=(1, 2)),
        ]
    )
    assert stats.count == 2
    assert stats.mean_chars == pytest.approx(6.5)
    assert stats.max_chars == 10
    assert stats.with_section_path == 1
    assert stats.cross_page == 1


def test_chunk_stats_json_rounds_the_mean() -> None:
    payload = chunk_stats([chunk("ab"), chunk("abcd")]).to_json()
    assert payload["mean_chars"] == 3.0
    assert payload["count"] == 2


# -------------------------------------------------------------------- comparison


@pytest.fixture()
def filing(tmp_path: Path):
    return build_filing(tmp_path / "filing.pdf")


def test_both_parsers_run_on_the_same_document(filing) -> None:
    result = compare_parsers(filing.path, filing.doc_id)
    assert result.doc_id == filing.doc_id
    assert result.pages == filing.page_count
    assert result.bytes > 0
    assert result.baseline.parser == BASELINE_PARSER
    assert result.candidate.parser == CANDIDATE_PARSER


def test_the_candidate_recovers_structure_the_baseline_does_not(filing) -> None:
    result = compare_parsers(filing.path, filing.doc_id)
    assert result.baseline.blocks["heading"] == 0
    assert result.candidate.blocks["heading"] > 0
    assert result.baseline.chunks.with_section_path == 0
    assert result.candidate.chunks.with_section_path == result.candidate.chunks.count


def test_timings_are_recorded_for_ingestion_latency(filing) -> None:
    """Ingestion latency is a declared systems metric, so it is measured here."""
    ticks = iter([0.0, 1.5, 2.0, 2.25, 10.0, 14.0, 14.5, 14.75])
    result = compare_parsers(filing.path, filing.doc_id, monotonic=lambda: next(ticks))
    assert result.baseline.parse_seconds == pytest.approx(1.5)
    assert result.baseline.chunk_seconds == pytest.approx(0.25)
    assert result.candidate.parse_seconds == pytest.approx(4.0)
    assert result.candidate.chunk_seconds == pytest.approx(0.25)


def test_seconds_per_page_uses_the_page_count(filing) -> None:
    ticks = iter([0.0, 3.0, 3.0, 3.0, 3.0, 9.0, 9.0, 9.0])
    result = compare_parsers(filing.path, filing.doc_id, monotonic=lambda: next(ticks))
    assert result.candidate.seconds_per_page == pytest.approx(6.0 / filing.page_count)


def test_seconds_per_page_of_an_empty_document_is_zero(tmp_path: Path) -> None:
    path = build_empty_pdf(tmp_path / "blank.pdf", pages=2)
    result = compare_parsers(path, "BLANK")
    assert result.baseline.chunks.count == 0
    assert result.candidate.chunks.count == 0
    assert result.candidate.seconds_per_page >= 0.0


def test_json_shape_is_stable(filing) -> None:
    payload = compare_parsers(filing.path, filing.doc_id).to_json()
    assert set(payload) == {"doc_id", "pages", "bytes", "baseline", "candidate"}
    assert set(payload["baseline"]) == {  # type: ignore[arg-type]
        "parser",
        "parse_seconds",
        "chunk_seconds",
        "seconds_per_page",
        "blocks",
        "chunks",
    }
