"""Reading a page's tables out of its line stream.

The fixtures reproduce layouts observed on real filings -- one cell per line, unlabelled total
rows, two notes sharing one page, derived columns absent from the period header -- with invented
figures. Invented on purpose: a test carrying a locked gold answer would put the locked set in
the test suite, and the suite is not where locked answers belong.
"""

from __future__ import annotations

from twfi.numeric.rows import (
    PageTable,
    Row,
    column_index,
    matches_label,
    read_page,
    resolve_page,
    resolve_row,
)

# A note whose label and figures sit on separate lines, as PyMuPDF emits them.
SIMPLE = """
單位：新台幣仟元
113年12月31日
112年12月31日
製成品
11,111,111
22,222,222
在製品
33,333,333
44,444,444
"""

# The same, closed by a total row the filing left unlabelled.
WITH_TOTAL = (
    SIMPLE
    + """
55,555,555
66,666,666
"""
)

# Two notes on one page: the second period header is the split.
TWO_NOTES = (
    SIMPLE
    + """
本公司與存貨相關之營業成本中，包含將存貨成本沖減至淨變現價值而認列之損失。
113年度
112年度
淨存貨損失
77,777,777
88,888,888
"""
)

# A comparison table: two periods in the header, but four figures a row -- the difference
# columns are printed as their own lines and name no period.
WITH_DERIVED = """
112年度
111年度
差異
金額
％
流動資產
159,000,000
160,000,000
-1,000,000
-0.62
資產總計
530,000,000
511,000,000
19,000,000
3.72
"""


def test_label_and_figures_on_separate_lines_form_one_row() -> None:
    tables = read_page(SIMPLE)
    assert len(tables) == 1
    table = tables[0]
    assert table.periods == ("113年12月31日", "112年12月31日")
    assert table.width == 2
    assert table.rows[0] == Row(label="製成品", figures=("11,111,111", "22,222,222"))


def test_unit_declaration_alone_is_not_a_period_header() -> None:
    """`單位：新台幣仟元` names no period, so it must not open a table of its own."""
    assert len(read_page("單位：新台幣仟元\n製成品\n11,111,111\n")) == 0


def test_a_page_with_no_period_header_yields_no_table() -> None:
    """Without column labels nothing says which period a figure belongs to."""
    assert read_page("製成品\n11,111,111\n22,222,222\n") == []


def test_a_second_period_header_splits_the_page() -> None:
    tables = read_page(TWO_NOTES)
    assert [table.periods for table in tables] == [
        ("113年12月31日", "112年12月31日"),
        ("113年度", "112年度"),
    ]
    assert tables[1].rows[0].label == "淨存貨損失"


def test_prose_between_two_notes_does_not_become_a_row_of_figures() -> None:
    tables = read_page(TWO_NOTES)
    prose = [row for row in tables[0].rows if "營業成本" in row.label]
    assert prose and not prose[0].figures


def test_an_unlabelled_run_extends_the_previous_row_then_folds() -> None:
    table = read_page(WITH_TOTAL)[0]
    assert table.rows[-1] == Row(label="", figures=("55,555,555", "66,666,666"), labelled=False)
    assert table.rows[-2].label == "在製品"


def test_width_comes_from_the_rows_when_derived_columns_are_unheaded() -> None:
    """Four figures a row with two periods is one row of width 4, not two rows of width 2."""
    table = read_page(WITH_DERIVED)[0]
    assert table.width == 4
    current = next(row for row in table.rows if row.label == "流動資產")
    assert current.figures == ("159,000,000", "160,000,000", "-1,000,000", "-0.62")
    # No row was folded: a four-figure row here is one line item, not two.
    assert all(row.labelled for row in table.rows)


def test_period_columns_are_addressable_when_the_table_is_wider() -> None:
    table = read_page(WITH_DERIVED)[0]
    assert resolve_row(table, "資產總計", "112年度") == ("530,000,000", "row")
    assert resolve_row(table, "資產總計", "111年度") == ("511,000,000", "row")


#: The heading and its breakdown as filings actually print them: 「十二、存貨」 closes the previous
#: note, and the breakdown opens with its own period header -- so the two land in different tables.
HEADING_THEN_BREAKDOWN = (
    """
113年度
112年度
年底餘額
9,111,111
9,222,222
十二、存貨
"""
    + WITH_TOTAL
)


def test_a_breakdown_heading_resolves_across_the_split_to_the_unlabelled_total() -> None:
    """存貨 heads 製成品／在製品 and the total row carries no label, so neither table has both."""
    tables = read_page(HEADING_THEN_BREAKDOWN)
    assert len(tables) == 2
    assert resolve_row(tables[1], "存貨", "113年12月31日") is None
    assert resolve_page(tables, "存貨", "113年12月31日") == ("55,555,555", "breakdown_total")


def test_a_heading_does_not_reach_past_the_table_that_follows_it() -> None:
    """Otherwise any heading could claim any later total on the page."""
    tables = read_page(HEADING_THEN_BREAKDOWN + "\n113年度\n112年度\n\n7,777,777\n8,888,888\n")
    assert len(tables) == 3
    assert resolve_page(tables, "年底餘額", "113年度") == ("9,111,111", "row")
    assert resolve_page(tables, "存貨", "113年12月31日") == ("55,555,555", "breakdown_total")


def test_a_breakdown_heading_with_no_total_resolves_to_nothing() -> None:
    tables = read_page("113年度\n112年度\n年底餘額\n9,111,111\n9,222,222\n十二、存貨\n" + SIMPLE)
    assert resolve_page(tables, "存貨", "113年12月31日") is None


def test_an_unidentifiable_column_refuses_rather_than_guessing() -> None:
    table = read_page(SIMPLE)[0]
    assert resolve_row(table, "製成品", "108年12月31日") is None


def test_an_exact_row_label_beats_a_longer_one_printed_earlier() -> None:
    """流動資產總計 is printed first and contains 資產總計; it must not answer for it."""
    table = read_page(
        "112年12月31日\n111年12月31日\n"
        "流動資產總計\n103,000,000\n101,000,000\n"
        "非流動資產總計\n420,000,000\n421,000,000\n"
        "資產總計\n523,000,000\n522,000,000\n"
    )[0]
    assert resolve_row(table, "資產總計", "112年12月31日") == ("523,000,000", "row")


def test_a_one_character_header_fragment_cannot_match_an_account() -> None:
    """A page's header arrives one character per line, and 「資」 is inside 「資產總計」.

    Matching it short-circuited resolution entirely: the fragment is a labelled row with no
    figures, so the account looked like a breakdown heading with no total under it.
    """
    table = read_page(
        "112年12月31日\n111年12月31日\n資\n產\n金\n額\n資產總計\n523,000,000\n522,000,000\n"
    )[0]
    assert resolve_row(table, "資產總計", "112年12月31日") == ("523,000,000", "row")


def test_a_row_label_carrying_a_note_reference_still_matches() -> None:
    page = "112年12月31日\n111年12月31日\n存貨（附註三、四及十）\n11,000,000\n11,300,000\n"
    assert resolve_row(read_page(page)[0], "存貨", "112年12月31日") == ("11,000,000", "row")


def test_an_absent_row_resolves_to_nothing() -> None:
    table = read_page(SIMPLE)[0]
    assert resolve_row(table, "採用權益法之投資", "113年12月31日") is None


def test_a_labelled_row_of_the_wrong_width_is_not_indexed_into() -> None:
    """Which column such a row's figures belong to is exactly what is unknown."""
    table = PageTable(
        periods=("113年度", "112年度"),
        rows=(Row(label="營業成本", figures=("1,111,111",)),),
        width=2,
    )
    assert resolve_row(table, "營業成本", "113年度") is None


def test_an_uncommaed_integer_is_neither_a_figure_nor_a_label() -> None:
    """Money in these filings always carries thousands separators; statement codes never do.

    So `1100` is the code column and `6` is the ％ column, and neither may become a label -- on a
    real page the ％ column's `6` took the label position of a row holding the *previous*
    account's prior-year figure.
    """
    table = read_page("113年度\n112年度\n本期淨利\n1100\n6\n1,000,000\n2,000,000\n")[0]
    assert table.rows == (Row(label="本期淨利", figures=("1,000,000", "2,000,000")),)


def test_a_parenthesised_figure_keeps_its_brackets_for_the_caller_to_read() -> None:
    table = read_page("113年度\n112年度\n本期淨損\n(1,111,111)\n(2,222,222)\n")[0]
    assert table.rows[0].figures == ("(1,111,111)", "(2,222,222)")


def test_matches_label_holds_in_either_direction() -> None:
    assert matches_label("存 貨", "存貨")
    assert matches_label("存貨淨額", "存貨")
    assert matches_label("存貨", "存貨（附註十二）")
    assert not matches_label("存貨", "應付帳款")
    assert not matches_label("", "存貨")


def test_column_index_prefers_the_most_specific_heading() -> None:
    periods = ("112年", "112年12月31日")
    assert column_index(periods, "112年12月31日") == 1
    assert column_index(periods, "111年") is None
