"""Worklist slots point at evidence and never state what it says."""

from __future__ import annotations

import dataclasses

from twfi.eval.gold import CompanyRef
from twfi.eval.worklist import (
    EXCERPT_CHARS,
    PROBE_TOPICS,
    ProbeTopic,
    page_hits,
    probe_slots,
    statement_pages,
)

COMPANY = CompanyRef("台積電", "2330")

#: Shaped like the real document that exposed the conjunction bug: the statement heading
#: sits on an early page, the figures appear much later in the notes.
PAGES = [
    "合併財務報告目錄 合併綜合損益表 合併資產負債表",  # p1 -- index
    "",  # p2 -- image-only, no text at all
    "(二)編製基礎 本合併財務報告係依歷史成本基礎編製 合併綜合損益表已適當納入",  # p3
    "二一、營業收入 客戶合約收入之細分 產品別 晶圓 其他 合計",  # p4
    "基本每股盈餘 歸屬於母公司業主之本期淨利 加權平均流通在外股數",  # p5
]


def test_a_topic_is_found_even_when_no_heading_shares_its_page() -> None:
    """The regression that mattered.

    Requiring a statement heading on the same page as the figure found nothing in
    2330's FY2024 financial report, whose statements are image-only and whose headline
    figures are readable only in the notes.
    """
    slots = probe_slots(doc_id="2330-FY2024-FS", company=COMPANY, period="FY2024", pages=PAGES)
    keys = {slot.draft_id.rsplit("-", 1)[-1] for slot in slots}
    assert "revenue" in keys
    assert slots[0].page_numbers == (4,), "the notes page, not the index page"


def test_the_heading_locations_are_reported_as_orientation() -> None:
    assert statement_pages(PAGES)["合併綜合損益表"] == [1, 3]


def test_a_slot_records_where_it_looked_and_what_it_found_where() -> None:
    (slot,) = probe_slots(
        doc_id="2330-FY2024-FS",
        company=COMPANY,
        period="FY2024",
        pages=PAGES,
        topics=(ProbeTopic("revenue", "營業收入", ("營業收入",), unit="千元", currency="TWD"),),
    )
    assert "營業收入" in slot.rationale
    assert "合併綜合損益表" in slot.rationale, "heading pages help the annotator orient"
    assert slot.unit == "千元"
    assert slot.currency == "TWD"


def test_a_slot_carries_no_answer_field_at_all() -> None:
    """The structural guarantee: a worklist cannot leak a machine-written answer."""
    slots = probe_slots(doc_id="2330-FY2024-FS", company=COMPANY, period="FY2024", pages=PAGES)
    names = {field.name for field in dataclasses.fields(slots[0])}
    assert not names & {"answer", "annotator", "answer_provenance"}


def test_a_topic_absent_from_the_document_yields_no_slot() -> None:
    """Proving a negative is not the annotator's job."""
    slots = probe_slots(
        doc_id="2330-FY2024-FS",
        company=COMPANY,
        period="FY2024",
        pages=PAGES,
        topics=(ProbeTopic("nonsense", "存貨跌價損失回升利益", ("存貨跌價損失回升利益",)),),
    )
    assert slots == []


def test_empty_pages_are_skipped_without_shifting_page_numbers() -> None:
    """Page 2 has no text; the figure on page 4 must still be reported as page 4."""
    hits = page_hits(PAGES, ("營業收入",), require_context=False)
    assert [hit.page for hit in hits] == [4]


def test_page_numbers_are_one_based() -> None:
    hits = page_hits(["合併綜合損益表 營業收入"], ("營業收入",))
    assert hits[0].page == 1


def test_whitespace_between_characters_does_not_hide_a_term() -> None:
    """PDF extraction routinely splits a heading across spans."""
    hits = page_hits(["合 併 綜 合 損 益 表\n營 業 收 入"], ("營業收入",))
    assert len(hits) == 1


def test_requiring_context_filters_out_prose_mentions() -> None:
    """Still available for callers that want statement pages only."""
    prose = ["本公司營業收入主要來自晶圓代工，詳見附註。"]
    assert page_hits(prose, ("營業收入",), require_context=True) == []
    assert len(page_hits(prose, ("營業收入",), require_context=False)) == 1


def test_an_excerpt_is_bounded() -> None:
    long_page = "合併綜合損益表" + "營業收入" + "0" * 5000
    (hit,) = page_hits(long_page.splitlines() or [long_page], ("營業收入",))
    assert len(hit.excerpt) <= EXCERPT_CHARS


def test_only_the_first_matching_term_per_page_is_reported() -> None:
    """One page yields one hit per topic, not one per synonym."""
    pages = ["合併綜合損益表 本期淨利 淨利（淨損）"]
    hits = page_hits(pages, ("本期淨利", "淨利（淨損）"))
    assert len(hits) == 1


def test_max_pages_per_topic_is_respected() -> None:
    pages = ["營業收入 1"] * 5
    (slot,) = probe_slots(
        doc_id="2330-FY2024-FS",
        company=COMPANY,
        period="FY2024",
        pages=pages,
        topics=(ProbeTopic("revenue", "營業收入", ("營業收入",)),),
        max_pages_per_topic=2,
    )
    assert slot.page_numbers == (1, 2)


def test_the_probe_topics_are_headline_figures() -> None:
    """A probe only tests memorisation if the model plausibly memorised the figure."""
    labels = {topic.label for topic in PROBE_TOPICS}
    assert {"營業收入", "基本每股盈餘"} <= labels


def test_a_suggested_stem_is_a_question_not_an_answer() -> None:
    stem = PROBE_TOPICS[0].question_stem("台積電", "FY2024")
    assert stem.endswith("？")
    assert not any(character.isdigit() for character in stem.replace("FY2024", ""))
