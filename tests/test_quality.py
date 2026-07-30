"""A document that cannot be read must be named as such, not scored as a failure."""

from __future__ import annotations

from twfi.parsing.quality import (
    ANCHOR_TERMS,
    MIN_PAGES_FOR_JUDGEMENT,
    STATEMENT_TERMS,
    assess_pages,
)

GOOD_PAGE = "本公司民國112年度營業收入成長，財務狀況穩健，股東權益增加，董事會決議如下。"
STATEMENT_PAGE = "合併資產負債表\n合併綜合損益表\n會計師查核報告"
#: What 2317-FY2024 actually extracted: glyph codes, not characters.
MOJIBAKE_PAGE = "Ҟ ᒵ ൘ǵठިܿ ൔ֋ਜ ມǵϦљݯ౛ൔ֋ ႜӼ֡Ⴋ" * 20


def pages(*texts: str, count: int = 0) -> list[str]:
    """A page list padded to ``count`` pages with the last text."""
    body = list(texts)
    while len(body) < count:
        body.append(texts[-1])
    return body


# ---------------------------------------------------------------------- usable


def test_a_normal_filing_is_usable() -> None:
    result = assess_pages("D", pages(GOOD_PAGE, STATEMENT_PAGE, count=100))
    assert result.verdict == "usable"
    assert result.is_usable is True
    assert result.reasons == ()
    assert result.has_financial_statements is True


def test_statement_pages_are_reported_by_page_number() -> None:
    body = pages(GOOD_PAGE, count=50)
    body[29] = STATEMENT_PAGE
    result = assess_pages("D", body)
    assert result.statement_pages["合併資產負債表"] == 30
    assert result.statement_pages["合併綜合損益表"] == 30


def test_one_statement_marker_is_enough() -> None:
    """1301's report only matched two of the three markers and is still usable."""
    body = pages(GOOD_PAGE, count=60)
    body[10] = "合併綜合損益表"
    result = assess_pages("D", body)
    assert result.verdict == "usable"
    assert result.statement_pages["合併資產負債表"] is None


def test_anchor_hits_are_counted_per_page() -> None:
    result = assess_pages("D", pages(GOOD_PAGE, count=30))
    assert result.anchor_hits["公司"] == 30
    assert set(result.anchor_hits) == set(ANCHOR_TERMS)


def test_characters_and_density_are_reported() -> None:
    result = assess_pages("D", pages(GOOD_PAGE, STATEMENT_PAGE, count=40))
    assert result.characters > 0
    assert result.chars_per_page > 0


# -------------------------------------------------------- unusable text layer


def test_mojibake_is_diagnosed_as_an_unusable_text_layer() -> None:
    """The 2317-FY2024 case: plenty of characters, none of them readable."""
    result = assess_pages("2317-FY2024-AR", pages(MOJIBAKE_PAGE, count=136))
    assert result.verdict == "unusable_text_layer"
    assert result.characters > 50_000, "the point is that there is plenty of text"
    assert sum(result.anchor_hits.values()) == 0, "and that none of it is readable"
    assert "ToUnicode" in result.reasons[0]


def test_an_empty_text_layer_is_also_unusable() -> None:
    """A scanned filing extracts nothing at all."""
    result = assess_pages("D", pages("", count=100))
    assert result.verdict == "unusable_text_layer"
    assert result.characters == 0


def test_unusable_beats_missing_statements() -> None:
    """If the text cannot be read, the statements question is unanswerable."""
    result = assess_pages("D", pages(MOJIBAKE_PAGE, count=100))
    assert result.verdict == "unusable_text_layer"


# --------------------------------------------------- missing financial statements


def test_statements_filed_separately_is_diagnosed() -> None:
    """The 2330-FY2024 case: readable narrative, statements are a different filing.

    MOPS lists exactly one 股東會年報 file for that year, so the statements were not
    split off -- from FY2024 the annual report simply does not embed them.
    """
    narrative = "公司治理報告\n風險事項\n本公司營業收入概況，股東會決議，董事名單。"
    result = assess_pages("2330-FY2024-AR", pages(narrative, count=91))
    assert result.verdict == "missing_financial_statements"
    assert result.has_financial_statements is False
    assert "財務報告書" in result.reasons[0]
    assert "IFRSs合併財報" in result.reasons[0]


# --------------------------------------------------------------------- too short


def test_a_very_short_document_is_flagged_before_anything_else() -> None:
    result = assess_pages("D", pages(GOOD_PAGE, count=5))
    assert result.verdict == "too_short"
    assert "5 pages" in result.reasons[0]


def test_the_short_threshold_is_the_declared_one() -> None:
    short = assess_pages("D", pages(GOOD_PAGE, count=MIN_PAGES_FOR_JUDGEMENT - 1))
    long_enough = assess_pages("D", pages(GOOD_PAGE, STATEMENT_PAGE, count=MIN_PAGES_FOR_JUDGEMENT))
    assert short.verdict == "too_short"
    assert long_enough.verdict == "usable"


def test_an_empty_document_is_too_short_not_unreadable() -> None:
    result = assess_pages("D", [])
    assert result.verdict == "too_short"
    assert result.chars_per_page == 0.0


# ------------------------------------------------------------------- reporting


def test_json_shape_is_stable() -> None:
    payload = assess_pages("D", pages(GOOD_PAGE, STATEMENT_PAGE, count=30)).to_json()
    assert set(payload) == {
        "doc_id",
        "pages",
        "characters",
        "chars_per_page",
        "anchor_hits",
        "statement_pages",
        "verdict",
        "reasons",
    }
    assert payload["doc_id"] == "D"


def test_statement_terms_cover_both_industry_families() -> None:
    """金控 and 一般業 both file 合併資產負債表 and a 會計師查核報告."""
    assert "合併資產負債表" in STATEMENT_TERMS
    assert "會計師查核報告" in STATEMENT_TERMS
