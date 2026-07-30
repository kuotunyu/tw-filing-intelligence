"""Structure-aware chunking, tested against the three rules it claims to enforce."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.synthetic_pdf import build_filing
from twfi.parsing.chunker import StructureChunkConfig, chunk_structure_aware
from twfi.parsing.layout import parse_layout
from twfi.parsing.types import BBox, Block, BlockKind, ParsedDocument, ParsedPage


def block(
    text: str,
    *,
    page: int = 1,
    kind: BlockKind = "paragraph",
    section: tuple[str, ...] = (),
    order: int = 0,
    y: float = 100.0,
) -> Block:
    return Block(
        page=page,
        kind=kind,
        text=text,
        bbox=BBox(60, y, 500, y + 12),
        order=order,
        section_path=section,
        level=2 if kind == "heading" else None,
    )


def document_of(*blocks: Block, doc_id: str = "D") -> ParsedDocument:
    by_page: dict[int, list[Block]] = {}
    for item in blocks:
        by_page.setdefault(item.page, []).append(item)
    return ParsedDocument(
        doc_id=doc_id,
        parser="twfi-layout",
        pages=tuple(
            ParsedPage(number=number, width=595, height=842, blocks=tuple(items))
            for number, items in sorted(by_page.items())
        ),
    )


NO_MERGE = StructureChunkConfig(min_chars=0)


# ------------------------------------------------------------------- config


@pytest.mark.parametrize(
    ("max_chars", "min_chars"),
    [(0, 0), (-1, 0), (100, 200), (100, -1)],
)
def test_config_rejects_nonsense(max_chars: int, min_chars: int) -> None:
    with pytest.raises(ValueError):
        StructureChunkConfig(max_chars=max_chars, min_chars=min_chars)


# --------------------------------------------------- rule 1: section boundaries


def test_a_chunk_never_spans_two_sections() -> None:
    document = document_of(
        block("風險因素的內容說明。", section=("二、風險因素",), order=0),
        block("股利政策的內容說明。", section=("三、股利政策",), order=1, y=200),
    )
    chunks = chunk_structure_aware(document, NO_MERGE)
    assert len(chunks) == 2
    assert {chunk.section_path for chunk in chunks} == {("二、風險因素",), ("三、股利政策",)}


def test_adjacent_paragraphs_in_one_section_share_a_chunk() -> None:
    document = document_of(
        block("短句一。", section=("A",), order=0),
        block("短句二。", section=("A",), order=1, y=150),
        block("短句三。", section=("B",), order=2, y=200),
    )
    chunks = chunk_structure_aware(document, StructureChunkConfig(min_chars=100))
    assert len(chunks) == 2
    assert chunks[0].section_path == ("A",)
    assert "短句一" in chunks[0].text and "短句二" in chunks[0].text
    assert chunks[1].section_path == ("B",)


# The size limit must actually be exceeded for a flush to happen, so these use a
# small limit and a paragraph sized just under it. Picking numbers that never
# overflow makes the test pass without exercising the merge at all.
SMALL_LIMIT = StructureChunkConfig(max_chars=100, min_chars=50)
LONG_PARAGRAPH = "段" * 99
SHORT_CLOSER = "補充說明。"


def test_a_short_remainder_is_folded_back_into_the_previous_chunk() -> None:
    """The case merging exists for: a long paragraph plus a one-line closer.

    99 + 5 exceeds the 100-character limit, so the long paragraph is flushed and
    the closer would otherwise be left as a 7-character chunk -- too little signal
    to rank on. It is folded back instead.
    """
    document = document_of(
        block(LONG_PARAGRAPH, section=("A",), order=0),
        block(SHORT_CLOSER, section=("A",), order=1, y=400),
    )
    without_merge = chunk_structure_aware(
        document, StructureChunkConfig(max_chars=100, min_chars=0)
    )
    assert len(without_merge) == 2, "the size limit must split before merging is tested"

    chunks = chunk_structure_aware(document, SMALL_LIMIT)
    assert len(chunks) == 1
    assert chunks[0].text.endswith(SHORT_CLOSER)
    assert chunks[0].text.startswith("A\n")


def test_merging_does_not_duplicate_the_heading_prefix() -> None:
    document = document_of(
        block(LONG_PARAGRAPH, section=("年報", "一、概況"), order=0),
        block(SHORT_CLOSER, section=("年報", "一、概況"), order=1, y=400),
    )
    chunks = chunk_structure_aware(document, SMALL_LIMIT)
    assert len(chunks) == 1
    assert chunks[0].text.count("年報 > 一、概況") == 1


def test_a_short_remainder_is_not_merged_across_a_section_boundary() -> None:
    document = document_of(
        block(LONG_PARAGRAPH, section=("A",), order=0),
        block("短。", section=("B",), order=1, y=400),
    )
    chunks = chunk_structure_aware(document, SMALL_LIMIT)
    assert len(chunks) == 2
    assert chunks[1].section_path == ("B",)


# ------------------------------------------------------- rule 2: atomic blocks


def test_a_table_is_never_merged_with_prose() -> None:
    """Half a table is worse than none: the surviving numbers still look authoritative."""
    document = document_of(
        block("表格前的說明文字。", section=("A",), order=0),
        block(
            "營業收入 2,894 2,161\n營業成本 1,266 1,053",
            kind="table",
            section=("A",),
            order=1,
            y=150,
        ),
        block("表格後的說明文字。", section=("A",), order=2, y=250),
    )
    chunks = chunk_structure_aware(document, NO_MERGE)
    table_chunks = [chunk for chunk in chunks if "table" in chunk.kinds]
    assert len(table_chunks) == 1
    assert table_chunks[0].kinds == ("table",)
    assert "說明文字" not in table_chunks[0].text


def test_a_large_table_is_not_split_by_the_size_limit() -> None:
    big_table = "\n".join(f"項目{index} {index * 1000:,}" for index in range(200))
    document = document_of(block(big_table, kind="table", section=("A",)))
    chunks = chunk_structure_aware(document, StructureChunkConfig(max_chars=200, min_chars=0))
    assert len(chunks) == 1
    assert chunks[0].char_count > 200


def test_an_undersized_table_is_not_merged_away() -> None:
    document = document_of(
        block("很短的表格 1", kind="table", section=("A",), order=0),
        block("後續的說明段落文字。", section=("A",), order=1, y=200),
    )
    chunks = chunk_structure_aware(document, StructureChunkConfig(min_chars=500))
    assert any(chunk.kinds == ("table",) for chunk in chunks)


# ------------------------------------------------------- rule 3: heading prefix


def test_the_section_path_is_prefixed_into_the_text() -> None:
    document = document_of(
        block("本公司主要銷售地區為北美。", section=("年報", "三、營運概況", "（一）市場分析"))
    )
    chunk = chunk_structure_aware(document, NO_MERGE)[0]
    assert chunk.text.startswith("年報 > 三、營運概況 > （一）市場分析\n")
    assert "本公司主要銷售地區" in chunk.text


def test_the_prefix_can_be_disabled() -> None:
    document = document_of(block("內容。", section=("A", "B")))
    config = StructureChunkConfig(include_heading_prefix=False, min_chars=0)
    assert chunk_structure_aware(document, config)[0].text == "內容。"


def test_a_bare_heading_does_not_become_its_own_chunk() -> None:
    """A heading-only chunk retrieves well and answers nothing."""
    document = document_of(
        block("一、公司概況", kind="heading", section=("一、公司概況",), order=0),
        block("本公司成立於民國七十六年。", section=("一、公司概況",), order=1, y=150),
    )
    chunks = chunk_structure_aware(document, NO_MERGE)
    assert len(chunks) == 1
    assert "heading" not in chunks[0].kinds
    assert chunks[0].section_path == ("一、公司概況",)


# ----------------------------------------------------------------- size limits


def test_prose_is_split_at_the_size_limit() -> None:
    blocks = [
        block("段落" * 40, section=("A",), order=index, y=100 + index * 20) for index in range(5)
    ]
    chunks = chunk_structure_aware(
        document_of(*blocks), StructureChunkConfig(max_chars=200, min_chars=0)
    )
    assert len(chunks) > 1


# -------------------------------------------------------------------- citations


def test_each_chunk_records_one_bbox_per_page() -> None:
    document = document_of(
        block("第一頁內容。", page=1, section=("A",), order=0, y=100),
        block("第一頁後續。", page=1, section=("A",), order=1, y=140),
        block("第二頁續行。", page=2, section=("A",), order=0, y=100),
    )
    chunk = chunk_structure_aware(document, StructureChunkConfig(min_chars=0, max_chars=10_000))[0]
    assert chunk.pages == (1, 2)
    assert len(chunk.refs) == 2
    page_one = next(ref for ref in chunk.refs if ref.page == 1)
    assert page_one.bbox.y0 == 100 and page_one.bbox.y1 == 152


def test_a_cross_page_chunk_is_flagged() -> None:
    document = document_of(
        block("第一頁內容。", page=1, section=("A",), order=0),
        block("第二頁續行。", page=2, section=("A",), order=0),
    )
    chunk = chunk_structure_aware(document, StructureChunkConfig(min_chars=0, max_chars=10_000))[0]
    assert chunk.spans_pages is True


def test_merged_chunks_keep_both_pages_refs() -> None:
    """A fold-back across a page break must not lose the second page's citation."""
    document = document_of(
        block(LONG_PARAGRAPH, page=1, section=("A",), order=0),
        block("續頁補充。", page=2, section=("A",), order=0),
    )
    chunks = chunk_structure_aware(document, SMALL_LIMIT)
    assert len(chunks) == 1
    assert chunks[0].pages == (1, 2)
    assert chunks[0].spans_pages is True


# ------------------------------------------------------------------- identifiers


def test_chunk_ids_are_contiguous_after_merging() -> None:
    document = document_of(
        block("短句一。", section=("A",), order=0),
        block("短句二。", section=("A",), order=1, y=150),
        block("內容三，屬於另一節。", section=("B",), order=2, y=200),
    )
    chunks = chunk_structure_aware(document, StructureChunkConfig(min_chars=100))
    assert [chunk.chunk_id for chunk in chunks] == ["D:struct:00000", "D:struct:00001"]


def test_chunks_record_the_parser() -> None:
    document = document_of(block("內容。", section=("A",)))
    assert chunk_structure_aware(document, NO_MERGE)[0].parser == "twfi-layout"


def test_furniture_never_reaches_a_chunk() -> None:
    document = document_of(
        block("- 1 -", kind="header_footer", order=0),
        block("真正的內容段落。", section=("A",), order=1, y=200),
    )
    chunks = chunk_structure_aware(document, NO_MERGE)
    assert len(chunks) == 1
    assert "- 1 -" not in chunks[0].text


def test_an_empty_document_yields_no_chunks() -> None:
    assert chunk_structure_aware(document_of()) == []


# ------------------------------------------------------------------ end to end


def test_end_to_end_chunks_follow_the_filing_structure(tmp_path: Path) -> None:
    filing = build_filing(tmp_path / "filing.pdf")
    document = parse_layout(filing.path, filing.doc_id)
    chunks = chunk_structure_aware(document)

    assert chunks
    assert all(chunk.pages for chunk in chunks), "every chunk must cite a page"
    assert all(chunk.section_path for chunk in chunks), "every chunk sits under a heading"
    assert any("營業收入" in chunk.text for chunk in chunks)
    assert all("- 1 -" not in chunk.text for chunk in chunks)


def test_end_to_end_produces_more_focused_chunks_than_fixed_windows(tmp_path: Path) -> None:
    """Structure-aware chunks carry their heading; fixed-window chunks cannot."""
    from twfi.parsing.baseline import chunk_fixed, parse_baseline

    filing = build_filing(tmp_path / "filing.pdf")
    fixed = chunk_fixed(parse_baseline(filing.path, filing.doc_id))
    structured = chunk_structure_aware(parse_layout(filing.path, filing.doc_id))

    assert all(chunk.section_path == () for chunk in fixed)
    assert all(chunk.section_path != () for chunk in structured)
