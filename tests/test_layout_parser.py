"""The candidate parser's claims, tested as claims.

Structure detection is pure, so most of this exercises :func:`classify_pages` on
hand-built lines. The end-to-end tests then confirm the same behaviour survives
real PyMuPDF extraction from a synthetic filing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.synthetic_pdf import build_empty_pdf, build_filing
from twfi.errors import ParsingError
from twfi.parsing.layout import (
    PARSER_NAME,
    LayoutConfig,
    RawPage,
    _span_from_dict,
    body_font_size,
    classify_pages,
    detect_numbering_level,
    extract_raw_pages,
    looks_tabular,
    parse_layout,
    reading_order,
    repeated_furniture,
)
from twfi.parsing.types import BBox, Line, Span


def make_line(
    text: str, *, y: float, size: float = 10.5, bold: bool = False, x: float = 60.0
) -> Line:
    bbox = BBox(x, y - size, x + max(len(text), 1) * size * 0.6, y)
    return Line(spans=(Span(text=text, bbox=bbox, size=size, bold=bold),), bbox=bbox)


def page_of(*lines: Line, number: int = 1, height: float = 842.0) -> RawPage:
    return RawPage(number=number, width=595.0, height=height, lines=lines)


# ------------------------------------------------------------------- numbering


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("第一章 公司概況", 1),
        ("壹、公司概況", 1),
        ("第二節 營運概況", 2),
        ("一、公司概況", 2),
        ("十、其他事項", 2),
        ("（一）市場風險", 3),
        ("(三)匯率風險", 3),
        ("（1）細項", 4),
        # A bare arabic number is level 4, matching （1）: in these filings the hierarchy runs
        # 壹/貳 -> 一/二 -> （一）/（二） -> 1./2., so it is the deepest customary level. This
        # asserted 1 until the consequence was measured -- every numbered list item became a
        # new top-level section, 518 of them in 1301-FY2023-AR against the ten a filing has.
        ("1. 概述", 4),
        ("1.2 明細", 2),
        ("3.4.5 子項", 3),
    ],
)
def test_numbering_implies_a_level(text: str, level: int) -> None:
    assert detect_numbering_level(text) == level


@pytest.mark.parametrize(
    "text",
    ["本公司成立於民國七十六年。", "營業收入 2,894,308", "", "   ", "營業毛利"],
)
def test_prose_has_no_numbering(text: str) -> None:
    assert detect_numbering_level(text) is None


def test_deep_numbering_is_capped() -> None:
    assert detect_numbering_level("1.2.3.4.5 very deep") == 5


@pytest.mark.parametrize(
    "text",
    [
        "1 現金及約當現金 1,234,567",
        "3 應收帳款淨額 987,654",
        "12 存貨 456,789",
    ],
)
def test_a_table_row_beginning_with_a_figure_is_not_numbering(text: str) -> None:
    """The defect this fixes: on a 707-page filing it produced 23,677 "headings".

    A bare number followed by a space matched every statement row that starts with a
    line number, and each one then hijacked the section path for what followed.
    """
    assert detect_numbering_level(text) is None


@pytest.mark.parametrize(
    ("text", "level"),
    [("1. 概述", 4), ("1、概述", 4), ("1.2 明細", 2), ("1.2.3 子項", 3)],
)
def test_real_arabic_numbering_still_works(text: str, level: int) -> None:
    """A dot or 、 is what makes numbering unambiguous; a space alone is not.

    A single number is level 4 and a dotted one takes its depth from the dots, so 1.2 is
    shallower than 1. -- which reads oddly and is right: 1.2 names a second-level item
    explicitly, while a bare 1. is a list marker at the bottom of the hierarchy.
    """
    assert detect_numbering_level(text) == level


@pytest.mark.parametrize(
    "text",
    [
        "一、營業收入 2,894,308 2,161,736",
        "營業成本 1,266,151 1,053,405",
        "（一）毛利率 45.6 38.2",
    ],
)
def test_lines_carrying_two_or_more_figures_look_tabular(text: str) -> None:
    assert looks_tabular(text) is True


@pytest.mark.parametrize(
    "text",
    ["1.2 民國112年度概況", "一、公司概況", "三、財務概況", "營業收入", ""],
)
def test_a_heading_may_carry_a_single_figure(text: str) -> None:
    """A year in a heading is normal; two figures side by side is a table row."""
    assert looks_tabular(text) is False


def test_a_tabular_line_is_never_a_heading_however_numbered() -> None:
    pages = (
        page_of(
            make_line("一、營業收入 2,894,308 2,161,736", y=100, size=14.0),
            make_line("本文內容，維持一般字級大小說明。", y=140, size=10.5),
            make_line("第二段本文內容，同樣字級。", y=160, size=10.5),
        ),
    )
    assert classify_pages(pages, "D").blocks_of_kind("heading") == ()


# ------------------------------------------------------------------- body size


def test_body_size_is_the_character_weighted_mode() -> None:
    pages = (
        page_of(
            make_line("巨大標題", y=100, size=24.0),
            make_line("這是一段很長的本文內容用來壓過標題的權重", y=140, size=10.5),
            make_line("這是第二段同樣大小的本文內容", y=160, size=10.5),
        ),
    )
    assert body_font_size(pages) == 10.5


def test_body_size_of_an_empty_document_is_zero() -> None:
    assert body_font_size((page_of(),)) == 0.0


def test_body_size_ignores_blank_lines() -> None:
    pages = (page_of(make_line("   ", y=100, size=30.0), make_line("內容", y=120, size=11.0)),)
    assert body_font_size(pages) == 11.0


# ------------------------------------------------------------------- furniture


def test_a_repeated_footer_is_furniture() -> None:
    pages = tuple(
        page_of(
            make_line(f"第{n}章 內容", y=100),
            make_line(f"- {n} -", y=800),
            number=n,
        )
        for n in (1, 2, 3, 4)
    )
    assert repeated_furniture(pages, LayoutConfig()) == {"- # -"}


def test_a_heading_near_the_top_is_not_furniture() -> None:
    """Position alone must not condemn a line; only repetition does."""
    titles = ("公司概況", "風險因素", "股利政策", "關係企業")
    pages = tuple(
        page_of(make_line(title, y=40), make_line("本文", y=400), number=number)
        for number, title in enumerate(titles, start=1)
    )
    assert repeated_furniture(pages, LayoutConfig()) == set()


def test_digits_are_normalised_so_numbered_furniture_still_matches() -> None:
    """Deliberate consequence: lines differing only by a number are one key.

    That is what lets ``- 1 -`` … ``- 9 -`` be recognised as a single footer. The
    cost is that a *numbered heading* sitting in the margin zone on most pages
    would also be treated as furniture. Section headings do not normally live in
    the margin, so this trade favours the common case -- but it is a real limitation
    rather than an invisible one.
    """
    pages = tuple(page_of(make_line(f"第{number}章", y=40), number=number) for number in (1, 2, 3))
    assert repeated_furniture(pages, LayoutConfig()) == {"第#章"}


def test_furniture_detection_needs_enough_pages() -> None:
    """On a two-page document, a repeated line is not yet evidence of furniture."""
    pages = tuple(page_of(make_line("- 1 -", y=800), number=n) for n in (1, 2))
    assert repeated_furniture(pages, LayoutConfig()) == set()


def test_page_numbers_are_normalised_before_comparison() -> None:
    pages = tuple(page_of(make_line(f"第 {n} 頁，共 10 頁", y=800), number=n) for n in (1, 2, 3, 4))
    assert repeated_furniture(pages, LayoutConfig()) == {"第 # 頁，共 # 頁"}


def test_content_in_the_middle_of_the_page_is_never_furniture() -> None:
    pages = tuple(page_of(make_line("重複的本文段落", y=400), number=n) for n in (1, 2, 3, 4))
    assert repeated_furniture(pages, LayoutConfig()) == set()


# --------------------------------------------------------------- reading order


def test_reading_order_is_top_to_bottom_then_left_to_right() -> None:
    lines = (
        make_line("right", y=100, x=300),
        make_line("left", y=100, x=60),
        make_line("below", y=200, x=60),
    )
    assert [line.text for line in reading_order(lines, 3.0)] == ["left", "right", "below"]


def test_reading_order_tolerates_sub_point_jitter() -> None:
    """Cells on one visual row must not be scrambled by fractional y differences."""
    lines = (
        make_line("b", y=100.9, x=200),
        make_line("a", y=100.0, x=60),
    )
    assert [line.text for line in reading_order(lines, 3.0)] == ["a", "b"]


# ------------------------------------------------------------------ classifying


def test_size_alone_promotes_a_heading() -> None:
    pages = (
        page_of(
            make_line("公司概況", y=100, size=18.0),
            make_line("本公司從事半導體製造，年度營收成長顯著。", y=140, size=10.5),
            make_line("第二段本文，補充說明營運狀況與展望。", y=160, size=10.5),
        ),
    )
    document = classify_pages(pages, "D")
    kinds = [(block.kind, block.text) for block in document.blocks]
    assert kinds[0][0] == "heading"
    assert kinds[1][0] == "paragraph"


def test_a_line_ending_in_a_full_stop_is_prose_however_large() -> None:
    """A large first sentence is not a heading; punctuation settles it."""
    pages = (
        page_of(
            make_line("這是一句很大的話。", y=100, size=18.0),
            make_line("後續本文內容，維持一般字級大小。", y=140, size=10.5),
            make_line("再一段本文內容，用來確立本文字級。", y=160, size=10.5),
        ),
    )
    assert classify_pages(pages, "D").blocks_of_kind("heading") == ()


def test_an_overlong_line_is_not_a_heading() -> None:
    long_text = "標題" * 60
    pages = (
        page_of(
            make_line(long_text, y=100, size=18.0),
            make_line("本文內容一，維持一般字級。", y=200, size=10.5),
            make_line("本文內容二，維持一般字級。", y=220, size=10.5),
        ),
    )
    assert classify_pages(pages, "D").blocks_of_kind("heading") == ()


def test_numbering_beats_typography() -> None:
    """A body-sized numbered line is still a heading; numbering is the stronger signal."""
    pages = (
        page_of(
            make_line("一、公司概況", y=100, size=10.5),
            make_line("本文內容，字級與標題相同。", y=140, size=10.5),
            make_line("第二段本文內容，同樣字級。", y=160, size=10.5),
        ),
    )
    headings = classify_pages(pages, "D").blocks_of_kind("heading")
    assert [block.text for block in headings] == ["一、公司概況"]
    assert headings[0].level == 2


def test_prose_that_merely_starts_with_numbering_is_not_a_heading() -> None:
    """Regression: numbering must not override the sentence-punctuation check.

    "第二節內容，與第一節無關的敘述。" begins with a heading-like prefix but is plainly
    body text. When numbering bypassed the shape checks, this became a heading and
    hijacked the section path for everything after it.
    """
    pages = (
        page_of(
            make_line("一、第一節", y=100, size=14.0),
            make_line("第二節內容，與第一節無關的敘述。", y=140, size=10.5),
            make_line("補充說明本節的細項規範與適用範圍。", y=160, size=10.5),
        ),
    )
    document = classify_pages(pages, "D")
    assert [block.text for block in document.blocks_of_kind("heading")] == ["一、第一節"]
    assert document.blocks_of_kind("paragraph")[0].section_path == ("一、第一節",)


def test_an_overlong_numbered_line_is_not_a_heading() -> None:
    long_numbered = "一、" + "細項說明" * 30
    pages = (
        page_of(
            make_line(long_numbered, y=100, size=10.5),
            make_line("後續本文內容，維持一般字級。", y=200, size=10.5),
        ),
    )
    assert classify_pages(pages, "D").blocks_of_kind("heading") == ()


def test_bold_at_body_size_is_a_heading() -> None:
    pages = (
        page_of(
            make_line("財務概況", y=100, size=10.5, bold=True),
            make_line("本文內容，非粗體，字級相同。", y=140, size=10.5),
            make_line("第二段本文內容，非粗體。", y=160, size=10.5),
        ),
    )
    assert [b.text for b in classify_pages(pages, "D").blocks_of_kind("heading")] == ["財務概況"]


def test_heading_levels_nest_into_a_section_path() -> None:
    pages = (
        page_of(
            make_line("年報", y=60, size=20.0),
            make_line("一、公司概況", y=100, size=14.0),
            make_line("（一）市場風險", y=140, size=12.0),
            make_line("終端需求波動可能影響產能利用率。", y=180, size=10.5),
            make_line("補充說明本公司因應措施與規劃。", y=200, size=10.5),
        ),
    )
    document = classify_pages(pages, "D")
    paragraph = document.blocks_of_kind("paragraph")[0]
    assert paragraph.section_path == ("年報", "一、公司概況", "（一）市場風險")


def test_a_sibling_heading_pops_the_deeper_level() -> None:
    pages = (
        page_of(
            make_line("一、第一節", y=100, size=14.0),
            make_line("（一）子節", y=130, size=12.0),
            make_line("子節內容，說明第一節的細項規範。", y=160, size=10.5),
            make_line("二、第二節", y=200, size=14.0),
            make_line("第二節內容，與第一節無關的敘述。", y=230, size=10.5),
        ),
    )
    paragraphs = classify_pages(pages, "D").blocks_of_kind("paragraph")
    assert paragraphs[0].section_path == ("一、第一節", "（一）子節")
    assert paragraphs[1].section_path == ("二、第二節",), "（一）子節 must be popped"


def test_furniture_is_kept_as_a_block_but_excluded_from_content() -> None:
    """Dropping furniture entirely would hide parser behaviour from the parse stats."""
    pages = tuple(
        page_of(
            make_line("內容段落，字級一般，長度足夠。", y=400),
            make_line(f"- {n} -", y=800),
            number=n,
        )
        for n in (1, 2, 3)
    )
    document = classify_pages(pages, "D")
    assert len(document.blocks_of_kind("header_footer")) == 3
    assert all(block.kind == "paragraph" for block in document.content_blocks())


def test_a_large_vertical_gap_splits_paragraphs() -> None:
    pages = (
        page_of(
            make_line("第一段內容，說明營運概況。", y=100, size=10.5),
            make_line("第二段內容，距離很遠，應該分開。", y=300, size=10.5),
        ),
    )
    assert len(classify_pages(pages, "D").blocks_of_kind("paragraph")) == 2


def test_consecutive_lines_join_into_one_paragraph() -> None:
    pages = (
        page_of(
            make_line("第一行內容，屬於同一段。", y=100, size=10.5),
            make_line("第二行內容，緊接在後。", y=115, size=10.5),
        ),
    )
    paragraphs = classify_pages(pages, "D").blocks_of_kind("paragraph")
    assert len(paragraphs) == 1
    assert "第一行" in paragraphs[0].text and "第二行" in paragraphs[0].text


def test_paragraph_bbox_covers_all_its_lines() -> None:
    pages = (
        page_of(
            make_line("第一行內容，屬於同一段。", y=100, size=10.5),
            make_line("第二行內容，緊接在後。", y=115, size=10.5),
        ),
    )
    paragraph = classify_pages(pages, "D").blocks_of_kind("paragraph")[0]
    assert paragraph.bbox.y0 <= 89.5
    assert paragraph.bbox.y1 >= 115.0


def test_a_section_continues_onto_the_next_page() -> None:
    """Cross-page evidence depends on this: page 2's rows keep page 1's section."""
    pages = (
        page_of(
            make_line("三、財務概況", y=100, size=14.0),
            make_line("營業收入 2,894,308 2,161,736", y=140, size=10.5),
            number=1,
        ),
        page_of(make_line("營業利益 1,155,494 856,000", y=100, size=10.5), number=2),
    )
    document = classify_pages(pages, "D")
    second_page_block = document.page(2).blocks[0]
    assert second_page_block.section_path == ("三、財務概況",)


def test_layout_config_rejects_impossible_thresholds() -> None:
    with pytest.raises(ValueError, match="heading_size_ratio"):
        LayoutConfig(heading_size_ratio=1.0)
    with pytest.raises(ValueError, match="repeat_threshold"):
        LayoutConfig(repeat_threshold=0.0)
    with pytest.raises(ValueError, match="repeat_threshold"):
        LayoutConfig(repeat_threshold=1.5)


def test_classifying_nothing_yields_an_empty_document() -> None:
    document = classify_pages((), "D")
    assert document.pages == ()
    assert document.parser == PARSER_NAME


def test_a_document_with_no_body_text_falls_back_to_boldness() -> None:
    """With no measurable body size, boldness is the only signal left."""
    pages = (page_of(make_line("標題", y=100, size=12.0, bold=True)),)
    document = classify_pages(pages, "D")
    assert document.blocks_of_kind("heading")[0].text == "標題"


# ------------------------------------------------------------------ end to end


@pytest.fixture()
def filing(tmp_path: Path):
    return build_filing(tmp_path / "filing.pdf")


def test_extraction_recovers_font_sizes_and_text(filing) -> None:
    pages = extract_raw_pages(filing.path)
    assert len(pages) == filing.page_count
    assert body_font_size(pages) == 10.5
    assert any("營業收入" in line.text for page in pages for line in page.lines)


def test_end_to_end_detects_the_heading_hierarchy(filing) -> None:
    document = parse_layout(filing.path, filing.doc_id)
    headings = {block.text: block.level for block in document.blocks_of_kind("heading")}
    assert headings[filing.title] == 1
    for text in filing.level2_headings:
        assert headings[text] == 2
    for text in filing.level3_headings:
        assert headings[text] == 3


def test_end_to_end_detects_the_running_footer(filing) -> None:
    document = parse_layout(filing.path, filing.doc_id)
    assert len(document.blocks_of_kind("header_footer")) == filing.page_count


def test_end_to_end_carries_sections_across_the_page_break(filing) -> None:
    document = parse_layout(filing.path, filing.doc_id)
    continuation = [
        block for block in document.page(3).blocks if filing.cross_page_row.split()[0] in block.text
    ]
    assert continuation, "the table continuation row was not found on page 3"
    assert "（二）合併綜合損益表" in continuation[0].section_path


def test_end_to_end_keeps_the_units_row_and_the_negative_cell(filing) -> None:
    text = parse_layout(filing.path, filing.doc_id).text
    assert filing.unit_row in text
    assert filing.negative_cell in text


def test_layout_parser_is_deterministic(filing) -> None:
    """Same PDF in, same blocks out -- the property a rule-based parser buys."""
    first = parse_layout(filing.path, filing.doc_id)
    second = parse_layout(filing.path, filing.doc_id)
    assert first == second


def test_layout_parser_beats_the_baseline_on_structure(filing) -> None:
    from twfi.parsing.baseline import parse_baseline

    baseline = parse_baseline(filing.path, filing.doc_id)
    candidate = parse_layout(filing.path, filing.doc_id)
    assert baseline.stats()["heading"] == 0
    assert candidate.stats()["heading"] >= 7
    assert candidate.stats()["header_footer"] == 3


def test_layout_parser_handles_a_page_with_no_text(tmp_path: Path) -> None:
    path = build_empty_pdf(tmp_path / "blank.pdf", pages=2)
    document = parse_layout(path, "BLANK")
    assert document.page_count == 2
    assert document.blocks == ()


def test_layout_parser_rejects_a_non_pdf(tmp_path: Path) -> None:
    broken = tmp_path / "not.pdf"
    broken.write_bytes(b"nope")
    with pytest.raises(ParsingError, match="cannot open"):
        parse_layout(broken, "BROKEN")


# ------------------------------------------------------- malformed span payloads


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "", "bbox": (0, 0, 10, 10), "size": 10.0},
        {"text": "   ", "bbox": (0, 0, 10, 10), "size": 10.0},
        {"text": "x", "size": 10.0},  # no bbox
        {"text": "x", "bbox": (0, 0, 10), "size": 10.0},  # short bbox
        {"text": "x", "bbox": "not-a-box", "size": 10.0},
    ],
)
def test_malformed_spans_are_dropped_not_guessed(payload: dict[str, object]) -> None:
    """A PDF can contain nonsense; inventing geometry would corrupt citations."""
    assert _span_from_dict(payload) is None


def test_a_span_flagged_bold_is_recognised() -> None:
    span = _span_from_dict({"text": "標題", "bbox": (0, 0, 10, 10), "size": 14.0, "flags": 16})
    assert span is not None
    assert span.bold is True


def test_boldness_is_also_inferred_from_the_font_name() -> None:
    """Some producers set no bold flag but name the face "…-Bold"."""
    span = _span_from_dict(
        {"text": "標題", "bbox": (0, 0, 10, 10), "size": 14.0, "font": "Helvetica-Bold"}
    )
    assert span is not None
    assert span.bold is True


def test_missing_size_and_flags_default_safely() -> None:
    span = _span_from_dict({"text": "x", "bbox": (0, 0, 10, 10)})
    assert span is not None
    assert span.size == 0.0
    assert span.bold is False


# ------------------------------ a decimal is not a heading number (D-031)


@pytest.mark.parametrize("text", ["0.00", "0.0005%", "1.5", "12.34", "0.00中央化工", "3.14159"])
def test_a_decimal_is_not_heading_numbering(text: str) -> None:
    r"""The bug: `^(\d{1,2})[.、]` matched the 0. of 0.00, so every decimal in every table
    became a level-1 heading.

    Measured before the fix: 1,083 distinct top-level sections in 1301-FY2023-AR named things
    like 0.0036,204,112, against the ten a filing actually has. The tabular guard could not
    catch it -- 0.00 is a single figure, and one figure is permitted in a heading.
    """
    assert detect_numbering_level(text) is None


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("1. 產品方面", 4),
        ("2、公司概況", 4),
        ("10.結論", 4),
        ("1.2 明細", 2),
        ("一、營運概況", 2),
        ("壹、公司簡介", 1),
        ("（一）財務狀況", 3),
    ],
)
def test_real_headings_still_detected_after_the_decimal_fix(text: str, level: int) -> None:
    """The lookahead must reject decimals without rejecting numbered headings."""
    assert detect_numbering_level(text) == level


def test_a_numbered_list_item_does_not_become_a_top_level_section() -> None:
    """The consequence that made the level wrong, stated as a property.

    A bare `1.` at level 1 resets the section stack, so every numbered list in the document
    starts a new root. Measured: 518 top-level sections in 1301-FY2023-AR and 424 in
    2412-FY2023-AR, against 22 and 18 after the fix -- and 2882-FY2024-AR came out at exactly
    ten, which is what an annual report has (壹 through 拾).
    """
    assert detect_numbering_level("1. 概述") is not None
    assert detect_numbering_level("1. 概述") > detect_numbering_level("一、公司概況")  # type: ignore[operator]
    assert detect_numbering_level("1. 概述") == detect_numbering_level("（1）細項")
