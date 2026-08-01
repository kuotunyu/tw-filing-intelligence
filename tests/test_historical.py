"""Loading historical figures must not let gold decide which ones count.

The property under test throughout: a value that disagrees with gold is still loaded, and the
disagreement is reported. If agreement were a condition for loading, the store would contain
exactly the right answers and F4 would score perfectly by construction -- the circularity
D-016 forbids, arriving from the other side.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from twfi.numeric.historical import (
    STATEMENT_BY_ACCOUNT,
    Loaded,
    Target,
    find_in_tables,
    find_in_text,
    outcome_of,
    parse_row_key,
    period_of_column,
    schema_of,
    statement_of,
    to_decimal,
)
from twfi.parsing.tables import Table, UnitSpec
from twfi.parsing.types import BBox


def table(
    rows: tuple[tuple[str, ...], ...], *, page: int = 188, unit: str | None = "千元"
) -> Table:
    return Table(
        page=page,
        bbox=BBox(0.0, 0.0, 100.0, 100.0),
        rows=rows,
        units=UnitSpec(unit=unit, currency="TWD"),
    )


BALANCE = (
    ("年度\n項目", "112 年度", "111 年度", "差 異"),
    ("資產總計", "530,738,356", "511,254,407", "19,483,949"),
    ("負債總計", "183,378,211", "153,569,544", "29,808,667"),
    ("保留盈餘", "210,804,324", "230,270,354", "(19,466,030)"),
)


def target(**overrides: object) -> Target:
    base: dict[str, object] = {
        "question_id": "DEV-0001",
        "doc_id": "1301-FY2023-AR",
        "company_code": "1301",
        "page": 188,
        "row_label": "資產總計",
        "column_label": "112年度",
        "basis": "consolidated",
        "gold_answer": "530,738,356",
    }
    base.update(overrides)
    return Target(**base)  # type: ignore[arg-type]


# --------------------------------------------------- the property that matters most


def test_a_figure_disagreeing_with_gold_is_still_loaded() -> None:
    """Agreement is reported, never required. This is the whole point of the module."""
    wrong = (
        BALANCE[0],
        ("資產總計", "999,999,999", "511,254,407", "-"),
    )
    result = find_in_tables(target(), [table(wrong)])
    assert result.item is not None, "a disagreeing value must still enter the store"
    assert result.item.value == Decimal("999999999")
    assert result.agrees_with_gold is False
    assert outcome_of(result) == "disagrees"


def test_missing_and_disagreeing_are_different_outcomes() -> None:
    """Collapsing them would hide which of two very different failures happened."""
    absent = find_in_tables(target(row_label="不存在的科目"), [table(BALANCE)])
    assert outcome_of(absent) == "missing"
    assert absent.agrees_with_gold is None, "nothing to compare is not disagreement"


def test_a_correct_figure_loads_and_agrees() -> None:
    result = find_in_tables(target(), [table(BALANCE)])
    assert result.item is not None
    assert result.item.value == Decimal("530738356")
    assert result.agrees_with_gold is True
    assert result.item.source_kind == "extracted_table"


def test_the_source_ref_names_the_cell_it_came_from() -> None:
    """A stored figure that cannot be traced back to a cell is not evidence."""
    result = find_in_tables(target(), [table(BALANCE)])
    assert result.item is not None
    assert result.item.source_ref == "1301-FY2023-AR|p188|資產總計|112年度"
    assert result.cell_text == "530,738,356"


# ------------------------------------------------------------------- row key parsing


def test_a_four_part_key_parses() -> None:
    assert parse_row_key("1301-FY2023-AR|p188|資產總計|112年度") == (
        "1301-FY2023-AR",
        188,
        "資產總計",
        "112年度",
    )


@pytest.mark.parametrize(
    "key",
    [
        "1301-FY2023-AR|p188",
        "1301-FY2023-AR|188|資產總計|112年度",
        "1301-FY2023-AR|pXX|資產總計|112年度",
        "",
    ],
)
def test_a_key_naming_no_cell_is_refused(key: str) -> None:
    """Two-part keys exist in gold for derived answers; they name no cell to load."""
    assert parse_row_key(key) is None


# ------------------------------------------------------------------ period mapping


@pytest.mark.parametrize(
    ("column", "period"),
    [
        ("112年度", "FY2023"),
        ("民國112年", "FY2023"),
        ("112年12月31日", "FY2023"),
        ("113 年 度", "FY2024"),
        ("民國111年", "FY2022"),
    ],
)
def test_a_single_year_heading_maps_to_a_fiscal_year(column: str, period: str) -> None:
    assert period_of_column(column) == period


@pytest.mark.parametrize("column", ["111年度及112年度", "111年至113年", "111年、112年", "金額"])
def test_a_heading_spanning_years_is_refused(column: str) -> None:
    """Picking the first year would file a figure under a period it may not belong to."""
    assert period_of_column(column) is None


def test_a_balance_date_and_a_year_end_map_to_the_same_period() -> None:
    """They differ in balance-versus-flow, which `statement` records, not in fiscal year."""
    assert period_of_column("112年12月31日") == period_of_column("112年度")


# --------------------------------------------------------------- statement mapping


def test_an_unknown_account_is_refused_rather_than_guessed() -> None:
    """`statement` decides which figures are comparable, so a guess would corrupt the store."""
    assert statement_of("某個沒見過的科目") is None
    result = find_in_tables(target(row_label="某個沒見過的科目"), [table(BALANCE)])
    assert result.item is None
    assert "no statement known" in result.problem


def test_the_two_wordings_of_total_assets_agree() -> None:
    """資產總額 and 資產總計 are the same line under two issuers' wording."""
    assert statement_of("資產總額") == statement_of("資產總計") == "balance"


def test_ratios_are_not_filed_as_balances() -> None:
    assert STATEMENT_BY_ACCOUNT["現金流量比率"] == "ratio"


def test_the_financial_holding_files_a_different_schema() -> None:
    assert schema_of("2882") == "financial_holding"
    assert schema_of("1301") == "general"


# ------------------------------------------------------------------- figure parsing


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("530,738,356", Decimal("530738356")),
        ("(19,466,030)", Decimal("-19466030")),
        ("（1,181,998）", Decimal("-1181998")),
        ("-0.73", Decimal("-0.73")),
        ("5.97%", Decimal("5.97")),
        ("39.11", Decimal("39.11")),
    ],
)
def test_figures_parse_as_filings_print_them(text: str, expected: Decimal) -> None:
    assert to_decimal(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "-", "不適用", "無"])
def test_a_cell_holding_no_figure_yields_none(text: str) -> None:
    assert to_decimal(text) is None


def test_a_bracketed_negative_keeps_its_sign() -> None:
    """Reading (1,181,998) as positive would flip a real figure, so this is pinned."""
    assert to_decimal("(1,181,998)") == Decimal("-1181998")


def test_decimals_not_floats() -> None:
    """These are money and the numeric route compares them for equality."""
    assert isinstance(to_decimal("530,738,356"), Decimal)


# ------------------------------------------------------------------ column matching


def test_a_verbose_header_still_matches_a_terse_key() -> None:
    """Real headers read 「112 年 12 月 31 日」 or add 金額; equality found nothing."""
    rows = (
        ("項目", "112 年 12 月 31 日 金額", "111 年 12 月 31 日 金額"),
        ("存貨", "287,868,810", "250,997,088"),
    )
    result = find_in_tables(
        target(row_label="存貨", column_label="112年12月31日", gold_answer="287,868,810"),
        [table(rows)],
    )
    assert result.item is not None
    assert result.item.value == Decimal("287868810")


def test_a_note_reference_in_the_row_label_is_stripped() -> None:
    rows = (("項目", "112 年度"), ("存貨（附註十二）", "287,868,810"))
    result = find_in_tables(
        target(row_label="存貨（附註十二）", column_label="112年度"), [table(rows)]
    )
    assert result.target.account == "存貨"
    assert result.item is not None


def test_a_table_on_another_page_is_not_used() -> None:
    """A figure loaded from the wrong page would cite a page that does not hold it."""
    result = find_in_tables(target(), [table(BALANCE, page=999)])
    assert result.item is None
    assert "no table on" in result.problem


# ------------------------------------------------------------------------ unit carry


def test_the_table_unit_is_carried_onto_the_figure() -> None:
    result = find_in_tables(target(), [table(BALANCE)])
    assert result.item is not None
    assert result.item.unit == "千元"
    assert result.item.currency == "TWD"


def test_a_unit_exception_marks_the_figure_non_uniform() -> None:
    """D-018: 單位：新台幣仟元，惟每股盈餘為元 -- the store must know the unit is not uniform."""
    spec = UnitSpec(unit="千元", currency="TWD", exception="每股盈餘為元")
    marked = Table(page=188, bbox=BBox(0, 0, 1, 1), rows=BALANCE, units=spec)
    result = find_in_tables(target(), [marked])
    assert result.item is not None
    assert result.item.unit_is_uniform is False
    assert result.item.unit_note == "每股盈餘為元"


def test_a_loaded_with_no_item_reports_no_agreement() -> None:
    assert Loaded(target(), None).agrees_with_gold is None


# ------------------------------------- the column must be identified, never guessed


def test_an_unidentifiable_column_is_refused_rather_than_guessed() -> None:
    """The bug this pins: 存貨 loaded as 88 -- a note reference -- against gold 287,868,810.

    The first version fell back to "the first parseable number in the matching row" when it
    could not identify the period column. A figure taken from a column nobody identified is
    worse than no figure, because it is wrong in a way that looks like data. The gold
    comparison caught it; the loader should not have needed catching.
    """
    rows = (
        ("項目", "附註", "金額"),
        ("存貨", "88", "287,868,810"),
    )
    result = find_in_tables(
        target(row_label="存貨", column_label="113年12月31日", gold_answer="287,868,810"),
        [table(rows)],
    )
    assert result.item is None, "an unidentified column must yield nothing, not the first number"
    assert "refusing to guess" in result.problem
    assert outcome_of(result) == "missing"


def test_a_missing_row_and_an_unidentified_column_report_differently() -> None:
    """Different work follows: one is a table not seen, the other a header not understood."""
    rows = (("項目", "附註"), ("存貨", "88"))
    no_column = find_in_tables(
        target(row_label="存貨", column_label="113年12月31日"), [table(rows)]
    )
    # 存貨 is a known account and BALANCE does not carry it, so this reaches the row search
    # rather than stopping at the statement map -- which an unknown label would.
    no_row = find_in_tables(target(row_label="存貨", column_label="112年度"), [table(BALANCE)])
    assert "no header identifiable" in no_column.problem
    assert "no table on" in no_row.problem


def test_the_right_column_is_still_found_when_others_hold_numbers() -> None:
    """The refusal must not be so strict that a normal statement stops loading."""
    rows = (
        ("項目", "附註", "112 年度", "111 年度"),
        ("資產總計", "十二", "530,738,356", "511,254,407"),
    )
    result = find_in_tables(target(), [table(rows)])
    assert result.item is not None
    assert result.item.value == Decimal("530738356")


# --------------------------------------------------- the text-stream route (D-032)

#: A page as PyMuPDF emits it: one cell per line, so a label and its figures are on
#: separate lines and no grid can tell which belong together.
TEXT_PAGE = """
單位：新台幣仟元
112年度
111年度
資產總計
530,738,356
511,254,407
負債總計
183,378,211
153,569,544
"""


def test_the_text_route_loads_a_cell_the_grid_cannot_see() -> None:
    loaded = find_in_text(target(), TEXT_PAGE, unit_default="千元", currency_default="TWD")
    assert loaded.item is not None
    assert loaded.item.value == Decimal("530738356")
    assert loaded.agrees_with_gold is True
    assert loaded.item.source_kind == "extracted_text_row"


def test_the_text_route_records_which_route_found_the_figure() -> None:
    """A breakdown total was not printed on a row bearing the account name, and a reader
    checking the store against the filing has to know that is what they are looking for."""
    loaded = find_in_text(target(), TEXT_PAGE)
    assert loaded.item is not None
    assert loaded.item.source_ref.endswith("|row")


def test_the_text_route_still_loads_a_figure_that_disagrees_with_gold() -> None:
    """The same property as the grid route: agreement is reported, never required."""
    loaded = find_in_text(target(gold_answer="999,999,999"), TEXT_PAGE)
    assert loaded.item is not None
    assert loaded.item.value == Decimal("530738356")
    assert loaded.agrees_with_gold is False
    assert outcome_of(loaded) == "disagrees"


def test_the_text_route_refuses_a_column_spanning_two_periods() -> None:
    loaded = find_in_text(target(column_label="111年度及112年度"), TEXT_PAGE)
    assert loaded.item is None
    assert "no single fiscal year" in loaded.problem


def test_the_text_route_refuses_an_account_it_cannot_place_on_a_statement() -> None:
    loaded = find_in_text(target(row_label="資產總計增減", column_label="112年度"), TEXT_PAGE)
    assert loaded.item is None
    assert "no statement known" in loaded.problem


def test_the_text_route_refuses_a_page_with_no_period_header() -> None:
    """Without column labels nothing says which period a figure belongs to."""
    loaded = find_in_text(target(), "資產總計\n530,738,356\n511,254,407\n")
    assert loaded.item is None
    assert "no period header" in loaded.problem


def test_the_text_route_refuses_a_row_it_cannot_find() -> None:
    loaded = find_in_text(target(row_label="存貨"), TEXT_PAGE)
    assert loaded.item is None
    assert "no text row" in loaded.problem


def test_the_text_route_reports_missing_rather_than_a_wrong_period() -> None:
    """A column this page does not carry must not fall back to one it does."""
    loaded = find_in_text(target(column_label="108年度"), TEXT_PAGE)
    assert loaded.item is None
    assert outcome_of(loaded) == "missing"
