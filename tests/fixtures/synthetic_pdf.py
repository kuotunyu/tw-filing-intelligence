"""Synthetic filings that reproduce the layout features real annual reports have.

Built with PyMuPDF at test time, so the suite stays offline and no PDF is ever
committed. The point is not to look like a filing to a human -- it is to contain
the specific structures the parser claims to handle:

* a three-level heading hierarchy, including Traditional Chinese numbering
  (``一、`` at level 2, ``（一）`` at level 3)
* a running footer that repeats on every page, so furniture detection has
  something to detect
* a units row (``單位：新台幣千元``), because getting the unit wrong is a distinct
  scored failure
* parenthesised negatives (``(12,345)``), the accounting convention that plain
  numeric parsing reads as positive
* a table that continues across a page break
* a vector-drawn figure with a caption

PyMuPDF's built-in CJK faces have no bold variant, which is exactly why heading
detection is built on size ratios and numbering rather than on boldness.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

__all__ = [
    "CJK_FONT",
    "LATIN_FONT",
    "PAGE_WIDTH",
    "PAGE_HEIGHT",
    "SyntheticFiling",
    "build_filing",
    "build_minimal_pdf",
    "build_empty_pdf",
]

CJK_FONT = "china-t"
LATIN_FONT = "helv"
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

TITLE_SIZE = 20.0
H2_SIZE = 14.0
H3_SIZE = 12.0
BODY_SIZE = 10.5
SMALL_SIZE = 9.0


@dataclass(frozen=True, slots=True)
class SyntheticFiling:
    """The expectations a test can assert against, alongside the file itself."""

    path: Path
    doc_id: str
    title: str
    level2_headings: tuple[str, ...]
    level3_headings: tuple[str, ...]
    footer_pattern: str
    unit_row: str
    negative_cell: str
    cross_page_row: str
    figure_caption: str
    page_count: int


def _write(page: pymupdf.Page, x: float, y: float, text: str, size: float) -> None:
    page.insert_text((x, y), text, fontname=CJK_FONT, fontsize=size)


def build_filing(path: Path, doc_id: str = "TEST-FY2024-AR") -> SyntheticFiling:
    """Write a three-page synthetic filing and describe what it contains."""
    document = pymupdf.open()  # type: ignore[no-untyped-call]

    # ---------------------------------------------------------------- page 1
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    _write(page, 150, 90, "台灣範例股份有限公司年報", TITLE_SIZE)
    _write(page, 60, 150, "一、公司概況", H2_SIZE)
    _write(page, 60, 180, "本公司成立於民國七十六年，主要從事半導體製造服務。", BODY_SIZE)
    _write(page, 60, 200, "本年度營運規模較上年度成長，主要係先進製程需求增加所致。", BODY_SIZE)
    _write(page, 60, 260, "二、風險因素", H2_SIZE)
    _write(page, 60, 290, "（一）市場風險", H3_SIZE)
    _write(page, 60, 315, "終端需求波動可能影響本公司產能利用率及獲利表現。", BODY_SIZE)
    _write(page, 280, 800, "- 1 -", SMALL_SIZE)

    # ---------------------------------------------------------------- page 2
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    _write(page, 60, 90, "三、財務概況", H2_SIZE)
    _write(page, 60, 120, "（二）合併綜合損益表", H3_SIZE)
    _write(page, 60, 145, "單位：新台幣千元", SMALL_SIZE)
    _write(page, 60, 175, "項目            113年度        112年度", BODY_SIZE)
    _write(page, 60, 195, "營業收入     2,894,308     2,161,736", BODY_SIZE)
    _write(page, 60, 215, "營業成本     1,266,151     1,053,405", BODY_SIZE)
    _write(page, 60, 235, "營業毛利     1,628,157     1,108,331", BODY_SIZE)
    _write(page, 60, 255, "其他損失      (12,345)      (23,456)", BODY_SIZE)
    _write(page, 280, 800, "- 2 -", SMALL_SIZE)

    # ---------------------------------------------------------------- page 3
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    _write(page, 60, 90, "營業利益     1,155,494       856,000", BODY_SIZE)
    _write(page, 60, 110, "稅前淨利     1,300,000       950,000", BODY_SIZE)
    _write(page, 60, 170, "（三）營收趨勢圖", H3_SIZE)
    # A bar chart drawn the way a real one is: frame, gridlines, bars, tick marks.
    # Path count matters -- figure detection requires enough drawing density to tell a
    # chart from a table rule, so a three-rectangle sketch would not be representative.
    frame = pymupdf.Rect(70, 200, 400, 380)
    page.draw_rect(frame, color=(0, 0, 0), width=1.0)
    for row in range(4):
        y = 240 + row * 35
        page.draw_line(pymupdf.Point(70, y), pymupdf.Point(400, y), color=(0.8, 0.8, 0.8))
    for index in range(6):
        left = 88 + index * 52
        height = 30 + index * 22
        page.draw_rect(
            pymupdf.Rect(left, 370 - height, left + 32, 370), color=(0, 0, 0), fill=(0.4, 0.4, 0.4)
        )
        page.draw_line(
            pymupdf.Point(left + 16, 370), pymupdf.Point(left + 16, 375), color=(0, 0, 0)
        )
    _write(page, 70, 400, "圖一：近三年營業收入趨勢", SMALL_SIZE)
    _write(page, 280, 800, "- 3 -", SMALL_SIZE)

    document.save(path)
    document.close()

    return SyntheticFiling(
        path=path,
        doc_id=doc_id,
        title="台灣範例股份有限公司年報",
        level2_headings=("一、公司概況", "二、風險因素", "三、財務概況"),
        level3_headings=("（一）市場風險", "（二）合併綜合損益表", "（三）營收趨勢圖"),
        footer_pattern="- # -",
        unit_row="單位：新台幣千元",
        negative_cell="(12,345)",
        cross_page_row="營業利益     1,155,494       856,000",
        figure_caption="圖一：近三年營業收入趨勢",
        page_count=3,
    )


def build_minimal_pdf(path: Path, text: str = "hello", pages: int = 1) -> Path:
    """A trivially small PDF, for tests that only need "a valid PDF"."""
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    for index in range(pages):
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 100), f"{text} {index + 1}", fontname=LATIN_FONT, fontsize=12)
    document.save(path)
    document.close()
    return path


def build_empty_pdf(path: Path, pages: int = 2) -> Path:
    """A PDF with pages but no text at all -- a scanned filing behaves like this."""
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    for _ in range(pages):
        document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    document.save(path)
    document.close()
    return path
