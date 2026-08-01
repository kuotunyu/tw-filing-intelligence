"""Detecting a text layer that decoded to the wrong characters.

The first version of this detector was withdrawn for scoring a document with a quarter of its
pages broken at 0% broken, so the test that matters most here is the one asserting a page of
another script is caught -- and, just as much, that clean Chinese with figures and table marks
is not.
"""

from __future__ import annotations

from twfi.parsing.garbled import (
    GARBLED_THRESHOLD,
    MIN_CHARACTERS,
    PageDefects,
    document_defects,
    is_core,
    page_defects,
)

#: A page as a sound filing extracts: Chinese, figures, fullwidth punctuation, a table mark.
CLEAN = (
    "四、存貨（附註三、四及十）　○　本公司民國112年12月31日之存貨淨額為 287,868,810 仟元，"
    "較民國111年12月31日之 250,997,088 仟元增加 14.69%；其中製成品 35,177,009 仟元。"
    "單位：新台幣仟元　①製成品　②在製品　③原料　※ 詳見附註十二。"
)

#: The same length of text as a wrong CMap returns it.
MOJIBAKE = "Ψҗᇙ୍ܺԱשׁฐໜབྷΨҗᇙ୍ܺԱשׁฐໜབྷΨҗᇙ୍ܺԱשׁฐໜབྷΨҗᇙ୍ܺԱשׁฐໜབྷΨҗᇙ୍ܺԱשׁฐໜབྷ"


def test_clean_filing_text_is_not_flagged() -> None:
    defects = page_defects(1, CLEAN)
    assert defects.characters >= MIN_CHARACTERS
    assert defects.defects == 0
    assert not defects.garbled
    assert defects.mode == ""


def test_a_page_of_another_script_is_flagged_as_off_script() -> None:
    """The failure the withdrawn version missed."""
    defects = page_defects(7, MOJIBAKE)
    assert defects.garbled
    assert defects.rate > 0.9
    assert defects.mode == "off_script"


def test_control_characters_are_counted_and_named_separately() -> None:
    """1301-FY2023-AR carries C0 controls; that is a different fault from a wrong CMap."""
    defects = page_defects(3, "\x10\x16" * 40 + "存貨")
    assert defects.controls == 80
    assert defects.off_script == 0
    assert defects.mode == "control_characters"


def test_tab_and_newline_are_whitespace_rather_than_control_characters() -> None:
    defects = page_defects(1, "存貨\t250,997,088\r\n本期\n")
    assert defects.controls == 0
    assert defects.defects == 0


def test_private_use_characters_are_flagged_and_named() -> None:
    """No character is assigned there, so a reader cannot read one whatever the font shows."""
    defects = page_defects(2, "" * 60)
    assert defects.private_use == 60
    assert defects.mode == "private_use"


def test_a_page_too_short_to_judge_is_not_judged() -> None:
    """One stray glyph on a six-character cover page is not a broken text layer."""
    defects = page_defects(1, "Ψҗᇙ")
    assert defects.rate == 1.0
    assert not defects.judged
    assert not defects.garbled


def test_the_threshold_sits_where_the_corpus_left_a_gap() -> None:
    """Sound documents peak at 2.73% per page; corrupt ones sit near 51%."""
    assert 0.0273 < GARBLED_THRESHOLD < 0.43


def test_a_rate_just_under_the_threshold_is_not_flagged() -> None:
    page = PageDefects(page=1, characters=1000, off_script=50, controls=0, private_use=0, other=0)
    assert page.rate == GARBLED_THRESHOLD
    assert not page.garbled, "the threshold is exclusive; equality is not a defect"


def test_whitespace_is_neither_core_nor_a_defect() -> None:
    defects = page_defects(1, "   \n\t   ")
    assert defects.characters == 0
    assert defects.rate == 0.0


def test_is_core_covers_the_marks_filings_use_in_tables() -> None:
    for character in "○●△□■①②③※→℃±×≦　（）0123456789Ａ":
        assert is_core(character), character
    for character in "Ψҗᇙ୍ܺ\x10":
        assert not is_core(character), character


def test_latin_characters_are_allowed_one_at_a_time_rather_than_by_range() -> None:
    """Allowing U+00C0-U+024F wholesale halved a corrupt document's measured rate.

    1301-FY2023-AR's corruption sits in exactly that range, so permitting the block took it from
    17.32% off-core to 8.09% -- still over the threshold for the document, but under it on many
    of its pages, which is the false negative the withdrawn first version produced.
    """
    for character in "®é×§±Δü":
        assert is_core(character), character
    for character in "ǴǶȐȑϐ":
        assert not is_core(character), character


def test_document_share_is_over_judged_pages_only() -> None:
    """Otherwise the figure drifts with how many blank pages a filing happens to carry."""
    document = document_defects(
        "X-FY2023-AR",
        [(1, MOJIBAKE), (2, CLEAN), (3, "短"), (4, "")],
    )
    assert len(document.judged_pages) == 2
    assert len(document.garbled_pages) == 1
    assert document.garbled_share == 0.5


def test_document_reports_every_mode_it_saw() -> None:
    document = document_defects(
        "X-FY2023-AR",
        [(1, MOJIBAKE), (2, "\x10" * 80), (3, "\x16" * 80), (4, CLEAN)],
    )
    assert document.modes == ("control_characters", "off_script")


def test_an_empty_document_reports_zero_rather_than_dividing_by_zero() -> None:
    document = document_defects("X-FY2023-AR", [])
    assert document.rate == 0.0
    assert document.garbled_share == 0.0
    assert document.modes == ()
