"""Equal-looking characters have to compare equal, and nothing else may move.

The bug: 1301-FY2023-AR sets 年 as U+F98E and 度 as U+FA01, so 「年度」 appears on none of 91
pages where a reader sees it on every one. The trap: the obvious fix, NFKC, also rewrites
fullwidth punctuation, and this codebase matches Chinese punctuation literally in regexes
several modules away. So these tests pin both halves -- what must change, and what must not.
"""

from __future__ import annotations

from twfi.parsing.normalise import normalise

#: The two characters that caused this module to exist.
COMPAT_YEAR = "年度"  # 年度, as 1301-FY2023-AR encodes it


def test_compatibility_ideographs_become_their_canonical_form() -> None:
    assert COMPAT_YEAR != "年度", "the fixture must actually differ, or this proves nothing"
    assert normalise(COMPAT_YEAR) == "年度"


def test_the_anchor_term_becomes_findable() -> None:
    """The failure as it was seen: a substring search that a reader would swear must hit."""
    page = f"最近二年度流動性分析 {COMPAT_YEAR}比較"
    assert "年度" in normalise(page)


def test_fullwidth_punctuation_is_left_alone() -> None:
    """NFKC would flatten all of these, and regexes elsewhere match them literally.

    tables.py's unit-exception class is [^，。、）)]; rewriting ， to , would change what that
    matches, in a module that has nothing to do with this one.
    """
    for char in "：（），、；！？":
        assert normalise(char) == char, f"{char!r} must survive normalisation"


def test_fullwidth_digits_and_latin_are_left_alone() -> None:
    """Also NFKC-only. Amount parsing does its own NFKC where that is wanted."""
    assert normalise("１２３ＡＢ") == "１２３ＡＢ"


def test_the_ideographic_full_stop_survives() -> None:
    assert normalise("。") == "。"


def test_ordinary_text_is_unchanged() -> None:
    text = "本公司民國112年度營業收入為 2,894,307,699 千元。"
    assert normalise(text) == text


def test_it_is_idempotent() -> None:
    """amounts.py normalises again downstream; applying twice must be harmless."""
    once = normalise(COMPAT_YEAR)
    assert normalise(once) == once


def test_empty_input_is_returned_unchanged() -> None:
    assert normalise("") == ""
