"""F0 must be a fair baseline: naive about structure, but honest about pages."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.synthetic_pdf import build_empty_pdf, build_filing, build_minimal_pdf
from twfi.errors import ParsingError
from twfi.parsing.baseline import PARSER_NAME, FixedChunkConfig, chunk_fixed, parse_baseline


@pytest.fixture()
def filing(tmp_path: Path):
    return build_filing(tmp_path / "filing.pdf")


# --------------------------------------------------------------------- parsing


def test_baseline_reads_every_page(filing) -> None:
    document = parse_baseline(filing.path, filing.doc_id)
    assert document.page_count == filing.page_count
    assert document.parser == PARSER_NAME
    assert document.doc_id == filing.doc_id


def test_baseline_extracts_traditional_chinese(filing) -> None:
    text = parse_baseline(filing.path, filing.doc_id).text
    assert "營業收入" in text
    assert filing.title in text


def test_baseline_preserves_parenthesised_negatives_verbatim(filing) -> None:
    """Normalisation happens at scoring time, not at parse time."""
    assert filing.negative_cell in parse_baseline(filing.path, filing.doc_id).text


def test_baseline_has_no_structural_knowledge(filing) -> None:
    """The whole point of F0: one undifferentiated block per page."""
    document = parse_baseline(filing.path, filing.doc_id)
    assert {block.kind for block in document.blocks} == {"paragraph"}
    assert all(block.level is None for block in document.blocks)
    assert all(block.section_path == () for block in document.blocks)


def test_baseline_still_attributes_pages(filing) -> None:
    """A baseline that could not cite a page would flatter the candidate."""
    document = parse_baseline(filing.path, filing.doc_id)
    assert [block.page for block in document.blocks] == [1, 2, 3]


def test_baseline_block_bbox_covers_the_page(filing) -> None:
    document = parse_baseline(filing.path, filing.doc_id)
    page = document.page(1)
    assert page.blocks[0].bbox.width == pytest.approx(page.width)
    assert page.blocks[0].bbox.height == pytest.approx(page.height)


def test_baseline_keeps_pages_without_text_but_emits_no_block(tmp_path: Path) -> None:
    """A scanned page has no text layer; that must be visible, not silently dropped."""
    path = build_empty_pdf(tmp_path / "blank.pdf", pages=2)
    document = parse_baseline(path, "BLANK")
    assert document.page_count == 2
    assert document.blocks == ()


def test_baseline_rejects_a_non_pdf(tmp_path: Path) -> None:
    broken = tmp_path / "not.pdf"
    broken.write_bytes(b"this is not a pdf")
    with pytest.raises(ParsingError, match="cannot open"):
        parse_baseline(broken, "BROKEN")


# -------------------------------------------------------------------- chunking


def test_fixed_chunking_uses_the_frozen_defaults() -> None:
    config = FixedChunkConfig()
    assert config.size == 800
    assert config.overlap == 100


@pytest.mark.parametrize(
    ("size", "overlap"),
    [(0, 0), (-10, 0), (100, 100), (100, 150), (100, -1)],
)
def test_fixed_chunk_config_rejects_nonsense(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        FixedChunkConfig(size=size, overlap=overlap)


def test_chunks_respect_the_window_size(filing) -> None:
    document = parse_baseline(filing.path, filing.doc_id)
    chunks = chunk_fixed(document, FixedChunkConfig(size=120, overlap=20))
    assert chunks
    assert all(chunk.char_count <= 120 for chunk in chunks)


def test_chunks_overlap_as_configured(filing) -> None:
    document = parse_baseline(filing.path, filing.doc_id)
    chunks = chunk_fixed(document, FixedChunkConfig(size=120, overlap=30))
    if len(chunks) >= 2:
        assert chunks[0].text[-30:] == chunks[1].text[:30]


def test_chunks_cover_the_whole_document(filing) -> None:
    document = parse_baseline(filing.path, filing.doc_id)
    chunks = chunk_fixed(document, FixedChunkConfig(size=200, overlap=0))
    assert "".join(chunk.text for chunk in chunks) == document.text


def test_every_chunk_cites_at_least_one_page(filing) -> None:
    document = parse_baseline(filing.path, filing.doc_id)
    for chunk in chunk_fixed(document):
        assert chunk.pages, f"{chunk.chunk_id} has no page reference"


def test_a_chunk_straddling_a_page_break_cites_both_pages(filing) -> None:
    """Fixed chunking cuts across pages; the citation must reflect that honestly."""
    document = parse_baseline(filing.path, filing.doc_id)
    chunks = chunk_fixed(document, FixedChunkConfig(size=800, overlap=0))
    assert any(chunk.spans_pages for chunk in chunks)


def test_chunk_ids_are_stable_and_unique(filing) -> None:
    document = parse_baseline(filing.path, filing.doc_id)
    first = [chunk.chunk_id for chunk in chunk_fixed(document)]
    second = [chunk.chunk_id for chunk in chunk_fixed(document)]
    assert first == second
    assert len(set(first)) == len(first)
    assert all(chunk_id.startswith(f"{filing.doc_id}:fixed:") for chunk_id in first)


def test_chunks_record_which_parser_produced_them(filing) -> None:
    document = parse_baseline(filing.path, filing.doc_id)
    assert all(chunk.parser == PARSER_NAME for chunk in chunk_fixed(document))


def test_chunking_an_empty_document_yields_nothing(tmp_path: Path) -> None:
    path = build_empty_pdf(tmp_path / "blank.pdf")
    assert chunk_fixed(parse_baseline(path, "BLANK")) == []


def test_single_page_document_produces_one_page_refs(tmp_path: Path) -> None:
    path = build_minimal_pdf(tmp_path / "one.pdf", text="short", pages=1)
    chunks = chunk_fixed(parse_baseline(path, "ONE"))
    assert len(chunks) == 1
    assert chunks[0].pages == (1,)
