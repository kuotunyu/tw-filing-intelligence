"""A wrong crop means the VLM reads the wrong chart, so the rectangle is load-bearing."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.synthetic_pdf import PAGE_HEIGHT, PAGE_WIDTH, build_filing, build_minimal_pdf
from twfi.errors import ParsingError
from twfi.parsing.figures import (
    Figure,
    FigureConfig,
    cluster_rects,
    detect_figures,
    figures_to_blocks,
    find_caption,
    render_crop,
)
from twfi.parsing.types import BBox

PAGE_AREA = PAGE_WIDTH * PAGE_HEIGHT


def bars(count: int = 10, *, left: float = 90.0, bottom: float = 370.0) -> list[BBox]:
    """A bar chart: several small filled rectangles sharing a baseline."""
    return [
        BBox(left + index * 30, bottom - 40 - index * 15, left + index * 30 + 20, bottom)
        for index in range(count)
    ]


# --------------------------------------------------------------------- clustering


def test_a_bar_chart_becomes_one_cluster() -> None:
    clusters = cluster_rects(bars(), gap=24.0, page_area=PAGE_AREA, config=FigureConfig())
    assert len(clusters) == 1
    box, members = clusters[0]
    assert members == 10
    assert box.x0 == pytest.approx(90.0)
    assert box.y1 == pytest.approx(370.0)


def test_two_distant_charts_stay_separate() -> None:
    rects = bars(left=60.0) + bars(left=60.0, bottom=800.0)
    clusters = cluster_rects(rects, gap=24.0, page_area=PAGE_AREA, config=FigureConfig())
    assert len(clusters) == 2


def test_a_sparse_cluster_is_not_a_chart() -> None:
    """A few paths in a big area is a border, not a chart."""
    rects = [BBox(60, 100, 500, 102), BBox(60, 400, 500, 402)]
    assert cluster_rects(rects, gap=24.0, page_area=PAGE_AREA, config=FigureConfig()) == []


def test_a_table_rule_is_rejected_on_aspect_ratio() -> None:
    rules = [BBox(60, 100 + index * 3, 540, 101 + index * 3) for index in range(12)]
    clusters = cluster_rects(rules, gap=24.0, page_area=PAGE_AREA, config=FigureConfig())
    assert clusters == [], "a stack of hairlines is not a figure"


def test_a_tiny_cluster_is_rejected_on_area() -> None:
    tiny = [BBox(60 + index, 100, 61 + index, 101) for index in range(12)]
    assert cluster_rects(tiny, gap=2.0, page_area=PAGE_AREA, config=FigureConfig()) == []


def test_a_full_page_background_is_rejected() -> None:
    covering = [BBox(0, 0, PAGE_WIDTH, PAGE_HEIGHT) for _ in range(10)]
    assert cluster_rects(covering, gap=24.0, page_area=PAGE_AREA, config=FigureConfig()) == []


def test_clustering_nothing_yields_nothing() -> None:
    assert cluster_rects([], gap=24.0, page_area=PAGE_AREA, config=FigureConfig()) == []


def test_config_rejects_impossible_thresholds() -> None:
    with pytest.raises(ValueError, match="area ratios"):
        FigureConfig(min_area_ratio=0.9, max_area_ratio=0.5)
    with pytest.raises(ValueError, match="min_paths"):
        FigureConfig(min_paths=0)


def test_crop_settings_match_the_protocol() -> None:
    config = FigureConfig()
    assert config.crop_dpi == 200
    assert config.crop_max_edge == 1024


# ----------------------------------------------------------------------- captions


REGION = BBox(70, 200, 400, 380)


def test_a_caption_below_the_figure_is_attached() -> None:
    candidates = [(BBox(70, 390, 300, 402), "圖一：近三年營業收入趨勢")]
    assert find_caption(REGION, candidates, FigureConfig()) == "圖一：近三年營業收入趨勢"


def test_a_caption_above_the_figure_is_attached() -> None:
    candidates = [(BBox(70, 170, 300, 185), "圖二：產品組合")]
    assert find_caption(REGION, candidates, FigureConfig()) == "圖二：產品組合"


def test_the_nearest_caption_wins() -> None:
    candidates = [
        (BBox(70, 430, 300, 445), "圖三：遠的"),
        (BBox(70, 385, 300, 398), "圖四：近的"),
    ]
    assert find_caption(REGION, candidates, FigureConfig()) == "圖四：近的"


def test_a_distant_caption_is_not_attached() -> None:
    candidates = [(BBox(70, 600, 300, 615), "圖五：太遠")]
    assert find_caption(REGION, candidates, FigureConfig()) == ""


def test_a_caption_in_another_column_is_not_stolen() -> None:
    """Horizontal overlap is required, or a neighbouring figure loses its caption."""
    candidates = [(BBox(450, 390, 560, 402), "圖六：右欄的圖")]
    assert find_caption(REGION, candidates, FigureConfig()) == ""


def test_ordinary_prose_is_not_a_caption() -> None:
    candidates = [(BBox(70, 390, 400, 402), "本公司營業收入成長主要係先進製程需求增加。")]
    assert find_caption(REGION, candidates, FigureConfig()) == ""


@pytest.mark.parametrize(
    "text",
    [
        "圖一：趨勢",
        "圖 2 產品組合",
        "附圖3、營收",
        "圖表一：分布",
        "Figure 3 Revenue",
        "Fig. 4 Mix",
    ],
)
def test_recognised_caption_labels(text: str) -> None:
    candidates = [(BBox(70, 390, 400, 402), text)]
    assert find_caption(REGION, candidates, FigureConfig()) == text


@pytest.mark.parametrize(
    "text",
    [
        "圖」之規定，每年將進行滾動式盤點財務報表揭露邊界，適時調整溫室氣體盤查範疇。",
        "圖利他人之行為應予避免",
        "Chart your own course",
        "圖",
    ],
)
def test_prose_beginning_with_a_caption_word_is_not_a_caption(text: str) -> None:
    """Regression: a bare 圖 prefix attached this sentence to a chart on 2882-FY2024-AR.

    A caption is a label, so it carries a number. Requiring one removes the whole
    class of false positive.
    """
    candidates = [(BBox(70, 390, 400, 402), text)]
    assert find_caption(REGION, candidates, FigureConfig()) == ""


# ------------------------------------------------------------------- references


def test_crop_ref_is_citable() -> None:
    figure = Figure(page=214, bbox=BBox(70.4, 200.2, 400.9, 380.1), kind="vector")
    assert figure.crop_ref == "p214:crop:70,200,401,380"


def test_index_text_carries_the_caption_and_the_crop_reference() -> None:
    figure = Figure(page=3, bbox=REGION, kind="vector", caption="圖一：近三年營業收入趨勢")
    text = figure.index_text()
    assert "圖一：近三年營業收入趨勢" in text
    assert figure.crop_ref in text


def test_an_uncaptioned_figure_is_still_indexable() -> None:
    text = Figure(page=7, bbox=REGION, kind="image").index_text()
    assert "未命名圖表" in text
    assert "第 7 頁" in text


def test_figures_become_atomic_blocks() -> None:
    blocks = figures_to_blocks((Figure(page=3, bbox=REGION, kind="vector", caption="圖一：趨勢"),))
    assert blocks[0].kind == "figure"
    assert blocks[0].page == 3
    assert blocks[0].bbox == REGION
    assert "圖一：趨勢" in blocks[0].text


def test_no_figures_yields_no_blocks() -> None:
    assert figures_to_blocks(()) == ()


# ------------------------------------------------------------------ end to end


@pytest.fixture()
def filing(tmp_path: Path):
    return build_filing(tmp_path / "filing.pdf")


def test_the_synthetic_bar_chart_is_detected(filing) -> None:
    figures = detect_figures(filing.path)
    assert figures, "the vector bar chart on page 3 was not found"
    on_page_three = [figure for figure in figures if figure.page == 3]
    assert on_page_three
    assert any(figure.kind == "vector" for figure in on_page_three)


def test_the_detected_figure_carries_its_caption(filing) -> None:
    figures = [f for f in detect_figures(filing.path) if f.page == 3]
    assert any(filing.figure_caption in figure.caption for figure in figures)


def test_a_page_without_drawings_yields_no_figures(tmp_path: Path) -> None:
    path = build_minimal_pdf(tmp_path / "plain.pdf", text="no charts here")
    assert detect_figures(path) == ()


def test_detection_rejects_a_non_pdf(tmp_path: Path) -> None:
    broken = tmp_path / "not.pdf"
    broken.write_bytes(b"nope")
    with pytest.raises(ParsingError, match="cannot open"):
        detect_figures(broken)


def test_detection_is_deterministic(filing) -> None:
    assert detect_figures(filing.path) == detect_figures(filing.path)


# ---------------------------------------------------------------------- crops


def test_a_crop_renders_to_a_png(filing, tmp_path: Path) -> None:
    """This image is the only thing a chart answer may be read from."""
    figures = [f for f in detect_figures(filing.path) if f.kind == "vector"]
    assert figures
    target = render_crop(filing.path, figures[0], tmp_path / "crops" / "chart.png")
    assert target.is_file()
    assert target.stat().st_size > 0


def test_a_crop_is_bounded_in_size(filing, tmp_path: Path) -> None:
    """Image tokens are bounded so a large chart cannot blow the VLM context."""
    pytest.importorskip("pymupdf")
    import pymupdf

    figures = [f for f in detect_figures(filing.path) if f.kind == "vector"]
    target = render_crop(
        filing.path, figures[0], tmp_path / "chart.png", FigureConfig(crop_max_edge=64)
    )
    with pymupdf.open(target) as image:
        pixmap = image.load_page(0).get_pixmap()
    assert max(pixmap.width, pixmap.height) <= 128, "the edge cap was not applied"


def test_rendering_a_crop_from_a_missing_page_fails_loudly(filing, tmp_path: Path) -> None:
    ghost = Figure(page=999, bbox=REGION, kind="vector")
    with pytest.raises(ParsingError, match="cannot render"):
        render_crop(filing.path, ghost, tmp_path / "ghost.png")
