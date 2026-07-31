"""Three extractors read the same pages, so assembly has to decide who wins."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.synthetic_pdf import build_filing
from twfi.parsing.chunker import chunk_structure_aware
from twfi.parsing.document import DocumentConfig, assemble, parse_document
from twfi.parsing.figures import Figure
from twfi.parsing.tables import Table, UnitSpec
from twfi.parsing.types import BBox, Block, ParsedDocument, ParsedPage

TABLE_REGION = BBox(60, 200, 500, 400)
FIGURE_REGION = BBox(60, 500, 400, 700)


def layout_document(*blocks: Block, doc_id: str = "D") -> ParsedDocument:
    by_page: dict[int, list[Block]] = {}
    for block in blocks:
        by_page.setdefault(block.page, []).append(block)
    return ParsedDocument(
        doc_id=doc_id,
        parser="twfi-layout",
        pages=tuple(
            ParsedPage(number=number, width=595, height=842, blocks=tuple(items))
            for number, items in sorted(by_page.items())
        ),
    )


def paragraph(text: str, bbox: BBox, *, page: int = 1, section: tuple[str, ...] = ()) -> Block:
    return Block(page=page, kind="paragraph", text=text, bbox=bbox, order=0, section_path=section)


def heading(text: str, bbox: BBox, *, page: int = 1) -> Block:
    return Block(
        page=page, kind="heading", text=text, bbox=bbox, order=0, level=2, section_path=(text,)
    )


def table(*, page: int = 1, bbox: BBox = TABLE_REGION) -> Table:
    return Table(
        page=page,
        bbox=bbox,
        rows=(("項目", "113年度"), ("營業收入", "2,894,308")),
        units=UnitSpec(unit="千元", currency="TWD"),
    )


def figure(*, page: int = 1, bbox: BBox = FIGURE_REGION, labels: int = 8) -> Figure:
    return Figure(page=page, bbox=bbox, kind="vector", path_count=30, numeric_labels=labels)


# ---------------------------------------------------------------- overlap rules


def test_prose_inside_a_table_gives_way_to_the_table() -> None:
    """Otherwise the same figures enter the index twice, once badly parsed."""
    inside = paragraph("營業收入 2,894,308", BBox(70, 250, 480, 270))
    result = assemble(layout_document(inside), (table(),), ())
    kinds = [block.kind for block in result.document.blocks]
    assert kinds == ["table"]
    assert result.dropped_overlapping_blocks == 1


def test_prose_merely_clipped_by_a_table_is_kept() -> None:
    """A long paragraph that a table corner touches is still a paragraph."""
    clipped = paragraph("本公司營運概況說明如下，內容涵蓋多個面向。", BBox(60, 380, 500, 600))
    result = assemble(layout_document(clipped), (table(),), ())
    assert "paragraph" in [block.kind for block in result.document.blocks]
    assert result.dropped_overlapping_blocks == 0


def test_a_heading_inside_a_table_region_survives() -> None:
    """Only prose gives way; losing a heading would break the section path."""
    result = assemble(
        layout_document(heading("三、財務概況", BBox(70, 210, 300, 230))), (table(),), ()
    )
    assert "heading" in [block.kind for block in result.document.blocks]


def test_overlap_ratio_is_configurable() -> None:
    half_in = paragraph("邊界情況", BBox(60, 350, 500, 450))
    strict = assemble(layout_document(half_in), (table(),), (), DocumentConfig(overlap_ratio=0.9))
    loose = assemble(layout_document(half_in), (table(),), (), DocumentConfig(overlap_ratio=0.2))
    assert strict.dropped_overlapping_blocks == 0
    assert loose.dropped_overlapping_blocks == 1


def test_config_rejects_an_impossible_ratio() -> None:
    with pytest.raises(ValueError, match="overlap_ratio"):
        DocumentConfig(overlap_ratio=0.0)


# ------------------------------------------------------------- chart filtering


def test_a_figure_sitting_on_a_table_is_not_a_chart() -> None:
    """Vector clustering finds table grids; the table extractor settles the argument."""
    grid = figure(bbox=TABLE_REGION, labels=200)
    result = assemble(layout_document(), (table(),), (grid,))
    assert result.figures == ()
    assert result.discarded_figures == 1
    assert result.document.blocks_of_kind("figure") == ()


def test_artwork_without_numeric_labels_is_not_a_chart() -> None:
    logo = figure(labels=0)
    result = assemble(layout_document(), (), (logo,))
    assert result.figures == ()
    assert result.discarded_figures == 1


def test_a_real_chart_survives_both_filters() -> None:
    result = assemble(layout_document(), (table(),), (figure(),))
    assert len(result.figures) == 1
    assert result.discarded_figures == 0
    assert len(result.document.blocks_of_kind("figure")) == 1


def test_discarded_figures_are_counted_not_hidden() -> None:
    """A bounded pipeline that does not say what it bounded reads as full coverage."""
    figures = (figure(labels=0), figure(bbox=TABLE_REGION, labels=99), figure())
    stats = assemble(layout_document(), (table(),), figures).stats()
    assert stats["chart_candidates"] == 1
    assert stats["discarded_figures"] == 2


# ------------------------------------------------------------- section context


def test_a_table_inherits_the_open_section() -> None:
    """A table chunk with no section path loses which statement it belongs to."""
    result = assemble(
        layout_document(heading("三、財務概況", BBox(60, 100, 300, 120))),
        (table(),),
        (),
    )
    table_block = result.document.blocks_of_kind("table")[0]
    assert table_block.section_path == ("三、財務概況",)


def test_a_figure_inherits_the_open_section() -> None:
    result = assemble(
        layout_document(heading("（三）營收趨勢圖", BBox(60, 100, 300, 120))),
        (),
        (figure(),),
    )
    assert result.document.blocks_of_kind("figure")[0].section_path == ("（三）營收趨勢圖",)


def test_a_table_before_any_heading_has_no_section() -> None:
    """Inventing a section would be worse than admitting there is none."""
    result = assemble(layout_document(), (table(),), ())
    assert result.document.blocks_of_kind("table")[0].section_path == ()


def test_sections_do_not_leak_backwards() -> None:
    blocks = layout_document(
        heading("一、公司概況", BBox(60, 100, 300, 120)),
        heading("二、財務概況", BBox(60, 450, 300, 470)),
    )
    result = assemble(blocks, (table(bbox=BBox(60, 200, 500, 300)),), ())
    assert result.document.blocks_of_kind("table")[0].section_path == ("一、公司概況",)


# ------------------------------------------------------------- reading order


def test_blocks_are_renumbered_into_one_reading_order() -> None:
    blocks = layout_document(
        heading("三、財務概況", BBox(60, 100, 300, 120)),
        paragraph("表格後的說明。", BBox(60, 450, 500, 470)),
    )
    result = assemble(blocks, (table(),), (figure(),))
    page = result.document.page(1)
    assert [block.order for block in page.blocks] == list(range(len(page.blocks)))
    assert [block.kind for block in page.blocks] == ["heading", "table", "paragraph", "figure"]


def test_the_assembled_document_is_labelled_as_such() -> None:
    """Comparing parsers requires knowing which produced an artifact."""
    assert assemble(layout_document(), (), ()).document.parser == "twfi-full"


def test_multiple_pages_keep_their_own_structured_blocks() -> None:
    blocks = layout_document(
        paragraph("第一頁", BBox(60, 100, 500, 120), page=1),
        paragraph("第二頁", BBox(60, 100, 500, 120), page=2),
    )
    result = assemble(blocks, (table(page=2),), ())
    assert result.document.page(1).blocks[0].kind == "paragraph"
    assert "table" in [block.kind for block in result.document.page(2).blocks]


# ------------------------------------------------------------------ end to end


@pytest.fixture()
def filing(tmp_path: Path):
    return build_filing(tmp_path / "filing.pdf")


def test_full_parse_produces_every_block_kind(filing) -> None:
    result = parse_document(filing.path, filing.doc_id)
    kinds = {block.kind for block in result.document.blocks}
    assert "heading" in kinds
    assert "header_footer" in kinds
    assert result.document.parser == "twfi-full"


def test_extractors_can_be_skipped_for_speed(filing) -> None:
    """Table extraction costs ~0.16 s/page; a text-only caller should not pay it."""
    result = parse_document(filing.path, filing.doc_id, with_tables=False, with_figures=False)
    assert result.tables == ()
    assert result.figures == ()
    assert result.document.blocks_of_kind("table") == ()


def test_the_assembled_document_chunks_cleanly(filing) -> None:
    result = parse_document(filing.path, filing.doc_id)
    chunks = chunk_structure_aware(result.document)
    assert chunks
    assert all(chunk.pages for chunk in chunks)
    assert all("- 1 -" not in chunk.text for chunk in chunks)


def test_full_parse_is_deterministic(filing) -> None:
    first = parse_document(filing.path, filing.doc_id)
    second = parse_document(filing.path, filing.doc_id)
    assert first.document == second.document
    assert first.stats() == second.stats()
