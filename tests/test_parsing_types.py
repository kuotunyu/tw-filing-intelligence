"""The document model carries the citation contract, so its geometry must be exact."""

from __future__ import annotations

import pytest

from twfi.parsing.types import BBox, Block, Chunk, Line, PageRef, ParsedDocument, ParsedPage, Span


def box(x0: float = 0, y0: float = 0, x1: float = 10, y1: float = 10) -> BBox:
    return BBox(x0, y0, x1, y1)


# ---------------------------------------------------------------------- BBox


def test_bbox_geometry() -> None:
    bbox = BBox(10, 20, 40, 60)
    assert bbox.width == 30
    assert bbox.height == 40
    assert bbox.area == 1200
    assert bbox.center_y == 40
    assert bbox.as_tuple() == (10, 20, 40, 60)


def test_bbox_rejects_inverted_coordinates() -> None:
    with pytest.raises(ValueError, match="degenerate bbox"):
        BBox(10, 10, 5, 20)
    with pytest.raises(ValueError, match="degenerate bbox"):
        BBox(10, 10, 20, 5)


def test_zero_area_bbox_is_allowed() -> None:
    """A zero-height line box is degenerate visually but not a programming error."""
    assert BBox(10, 10, 10, 10).area == 0


def test_bbox_from_tuple_coerces_to_float() -> None:
    bbox = BBox.from_tuple((1, 2, 3, 4))
    assert bbox == BBox(1.0, 2.0, 3.0, 4.0)


def test_union_covers_both_boxes() -> None:
    assert BBox(0, 0, 10, 10).union(BBox(20, 30, 25, 35)) == BBox(0, 0, 25, 35)


def test_iou_of_identical_boxes_is_one() -> None:
    assert box().iou(box()) == pytest.approx(1.0)


def test_iou_of_disjoint_boxes_is_zero() -> None:
    assert BBox(0, 0, 10, 10).iou(BBox(50, 50, 60, 60)) == 0.0


def test_iou_of_touching_boxes_is_zero() -> None:
    """Edge contact is not overlap; the citation gate must not accept it."""
    assert BBox(0, 0, 10, 10).iou(BBox(10, 0, 20, 10)) == 0.0


def test_iou_matches_the_gate_threshold() -> None:
    """The protocol accepts a bbox citation at IoU >= 0.3; check a known case."""
    a = BBox(0, 0, 10, 10)
    b = BBox(5, 0, 15, 10)  # half overlap: 50 / 150
    assert a.iou(b) == pytest.approx(1 / 3)
    assert a.iou(b) >= 0.3


def test_intersection_area() -> None:
    assert BBox(0, 0, 10, 10).intersection_area(BBox(5, 5, 15, 15)) == 25


def test_expanded_grows_on_every_side() -> None:
    assert box().expanded(2) == BBox(-2, -2, 12, 12)


def test_bboxes_are_hashable_and_ordered() -> None:
    assert len({box(), box()}) == 1
    assert BBox(0, 0, 1, 1) < BBox(0, 1, 1, 2)


# ---------------------------------------------------------------------- Line


def line_of(*parts: tuple[str, float, bool]) -> Line:
    spans = tuple(
        Span(text=text, bbox=BBox(index * 10, 0, index * 10 + 10, 10), size=size, bold=bold)
        for index, (text, size, bold) in enumerate(parts)
    )
    bbox = spans[0].bbox
    for span in spans[1:]:
        bbox = bbox.union(span.bbox)
    return Line(spans=spans, bbox=bbox)


def test_line_text_joins_spans() -> None:
    assert line_of(("營業", 10.0, False), ("收入", 10.0, False)).text == "營業收入"


def test_line_size_is_weighted_by_characters() -> None:
    """One large character must not outvote a line of body text."""
    assert line_of(("A", 24.0, False), ("body text here", 10.0, False)).size == 10.0


def test_line_bold_needs_a_majority_of_characters() -> None:
    assert line_of(("bold", 10.0, True), ("x", 10.0, False)).bold is True
    assert line_of(("b", 10.0, True), ("plain text", 10.0, False)).bold is False


def test_empty_line_has_no_size_or_boldness() -> None:
    empty = Line(spans=(), bbox=box())
    assert empty.size == 0.0
    assert empty.bold is False
    assert empty.text == ""


# --------------------------------------------------------------------- Block


def test_header_footer_blocks_are_not_content() -> None:
    """Page furniture never carries an answer, so it must be excludable."""
    furniture = Block(page=1, kind="header_footer", text="- 1 -", bbox=box(), order=0)
    body = Block(page=1, kind="paragraph", text="營業收入成長", bbox=box(), order=1)
    assert furniture.is_content is False
    assert body.is_content is True


# ------------------------------------------------------------- ParsedDocument


def document_with_blocks() -> ParsedDocument:
    return ParsedDocument(
        doc_id="TEST-FY2024-AR",
        parser="twfi-layout",
        pages=(
            ParsedPage(
                number=1,
                width=595,
                height=842,
                blocks=(
                    Block(
                        page=1, kind="heading", text="一、公司概況", bbox=box(), order=0, level=2
                    ),
                    Block(page=1, kind="paragraph", text="本公司成立於…", bbox=box(), order=1),
                    Block(page=1, kind="header_footer", text="- 1 -", bbox=box(), order=2),
                ),
            ),
            ParsedPage(
                number=2,
                width=595,
                height=842,
                blocks=(Block(page=2, kind="table", text="營業收入 1,000", bbox=box(), order=0),),
            ),
        ),
    )


def test_document_aggregates_blocks_across_pages() -> None:
    document = document_with_blocks()
    assert document.page_count == 2
    assert len(document.blocks) == 4
    assert len(document.content_blocks()) == 3


def test_page_text_excludes_furniture() -> None:
    document = document_with_blocks()
    assert "- 1 -" not in document.page(1).text
    assert "本公司成立於…" in document.page(1).text


def test_document_text_concatenates_pages() -> None:
    text = document_with_blocks().text
    assert "一、公司概況" in text
    assert "營業收入 1,000" in text


def test_blocks_of_kind_filters() -> None:
    assert len(document_with_blocks().blocks_of_kind("table")) == 1


def test_page_lookup_raises_for_a_missing_page() -> None:
    with pytest.raises(KeyError):
        document_with_blocks().page(99)


def test_stats_reports_every_kind() -> None:
    stats = document_with_blocks().stats()
    assert stats["pages"] == 2
    assert stats["heading"] == 1
    assert stats["table"] == 1
    assert stats["header_footer"] == 1
    assert stats["content_blocks"] == 3
    assert stats["figure"] == 0


def test_parser_is_part_of_the_document_identity() -> None:
    """Comparing two parsers requires knowing which one produced an artifact."""
    assert document_with_blocks().parser == "twfi-layout"


# --------------------------------------------------------------------- Chunk


def test_chunk_pages_are_sorted_and_deduplicated() -> None:
    chunk = Chunk(
        chunk_id="c1",
        doc_id="d",
        text="x",
        refs=(
            PageRef(page=3, bbox=box()),
            PageRef(page=2, bbox=box()),
            PageRef(page=3, bbox=box()),
        ),
    )
    assert chunk.pages == (2, 3)


def test_spans_pages_detects_cross_page_chunks() -> None:
    single = Chunk(chunk_id="c", doc_id="d", text="x", refs=(PageRef(1, box()),))
    across = Chunk(chunk_id="c", doc_id="d", text="x", refs=(PageRef(1, box()), PageRef(2, box())))
    assert single.spans_pages is False
    assert across.spans_pages is True


def test_chunk_heading_is_the_deepest_section() -> None:
    chunk = Chunk(
        chunk_id="c",
        doc_id="d",
        text="x",
        section_path=("年報", "三、財務概況", "（二）合併綜合損益表"),
    )
    assert chunk.heading == "（二）合併綜合損益表"


def test_chunk_without_a_section_has_no_heading() -> None:
    assert Chunk(chunk_id="c", doc_id="d", text="x").heading == ""


def test_chunk_char_count() -> None:
    assert Chunk(chunk_id="c", doc_id="d", text="12345").char_count == 5
