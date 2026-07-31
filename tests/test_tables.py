"""A cell value without its unit is not an answer, and half a table is worse than none."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.synthetic_pdf import build_filing
from twfi.errors import ParsingError
from twfi.parsing.tables import (
    Table,
    TableConfig,
    UnitSpec,
    detect_unit,
    document_unit,
    extract_tables,
    inherit_units,
    is_table_like,
    link_continuations,
    tables_to_blocks,
)
from twfi.parsing.types import BBox

STATEMENT_ROWS = (
    ("項目", "113年度", "112年度"),
    ("營業收入", "2,894,308", "2,161,736"),
    ("營業成本", "1,266,151", "1,053,405"),
)


def table(
    *,
    page: int = 1,
    rows: tuple[tuple[str, ...], ...] = STATEMENT_ROWS,
    y0: float = 100.0,
    y1: float = 200.0,
    units: UnitSpec | None = None,
) -> Table:
    return Table(
        page=page,
        bbox=BBox(60, y0, 500, y1),
        rows=rows,
        units=units or UnitSpec(),
    )


# ------------------------------------------------------------------------ units


@pytest.mark.parametrize(
    ("text", "unit", "currency"),
    [
        ("單位：新台幣千元", "千元", "TWD"),
        ("單位:新臺幣仟元", "千元", "TWD"),
        ("單位： 新台幣百萬元", "百萬元", "TWD"),
        ("單位：美元千元", "千元", "USD"),
        ("單位：人民幣元", "元", "CNY"),
        ("單位：千股", "千股", None),
        ("單位：仟股", "千股", None),
        ("單位：元", "元", None),
    ],
)
def test_units_rows_are_parsed(text: str, unit: str, currency: str | None) -> None:
    spec = detect_unit(text)
    assert (spec.unit, spec.currency) == (unit, currency)
    assert spec.is_stated is True


def test_the_sentence_form_is_parsed_too() -> None:
    """Observed on 2330-FY2024-FS p16; matching only 單位： found the unit nowhere."""
    spec = detect_unit("合併財務報告附註 民國113 及112 年度 （除另予註明者外，金額為新台幣仟元）")
    assert spec.unit == "千元"
    assert spec.currency == "TWD"


def test_the_two_spellings_of_thousand_are_one_unit() -> None:
    """仟元 and 千元 are the same unit; treating them apart would invent unit errors."""
    assert detect_unit("單位：新台幣仟元") == detect_unit("單位：新台幣千元")


def test_a_missing_unit_is_reported_not_defaulted() -> None:
    """Assuming 千元 would turn an unanswerable question into a wrong answer."""
    spec = detect_unit("合併資產負債表\n項目 113年度")
    assert spec.is_stated is False
    assert spec.unit is None and spec.currency is None
    assert spec.describe() == ""


def test_a_unit_without_a_currency_leaves_currency_unset() -> None:
    spec = detect_unit("單位：千元")
    assert (spec.unit, spec.currency) == ("千元", None)


def test_the_unit_is_found_inside_surrounding_text() -> None:
    page = "台灣積體電路製造股份有限公司\n合併綜合損益表\n單位：新台幣千元\n項目"
    assert detect_unit(page).unit == "千元"


# ------------------------------------------------------- non-uniform unit labels


def test_an_exception_clause_is_captured() -> None:
    """Observed verbatim on 2882-FY2024-FS p10.

    One label, two units. Applying 千元 to earnings per share would be wrong by a
    factor of a thousand, and wrong in a way that looks entirely plausible.
    """
    spec = detect_unit("合併綜合損益表 民國113 年度 單位：新台幣仟元，惟每股盈餘為元")
    assert spec.unit == "千元"
    assert spec.currency == "TWD"
    assert spec.exception == "每股盈餘為元"
    assert spec.is_uniform is False
    assert "例外：每股盈餘為元" in spec.describe()


def test_a_qualifier_makes_the_unit_non_uniform() -> None:
    """除另予註明者外 means the document reserves the right to override per line."""
    spec = detect_unit("（除另予註明者外，金額為新台幣仟元）")
    assert spec.unit == "千元"
    assert spec.qualified is True
    assert spec.is_uniform is False
    assert "除另予註明者外" in spec.describe()


def test_a_plain_unit_label_is_uniform() -> None:
    spec = detect_unit("單位：新台幣仟元")
    assert spec.is_uniform is True
    assert spec.exception is None
    assert spec.qualified is False


def test_an_unstated_unit_is_not_uniform_either() -> None:
    """`is_uniform` must not read as "safe to use" when nothing was stated."""
    assert UnitSpec().is_uniform is False


def test_an_exception_far_from_the_label_is_not_attached() -> None:
    """A 惟 clause a paragraph later is not a unit exception."""
    spec = detect_unit("單位：新台幣仟元" + "。" * 80 + "惟每股盈餘為元")
    assert spec.exception is None


# ------------------------------------------------------------------- acceptance


def test_a_statement_table_is_accepted() -> None:
    assert is_table_like(STATEMENT_ROWS, TableConfig()) is True


@pytest.mark.parametrize(
    ("rows", "why"),
    [
        ((("only one row", "x"),), "too few rows"),
        ((("a",), ("b",)), "too few columns"),
        ((("風險因素", "說明"), ("市場波動", "可能影響獲利")), "no numeric cell"),
        (((("", ""), ("", ""))), "empty"),
    ],
)
def test_candidates_without_table_shape_are_refused(
    rows: tuple[tuple[str, ...], ...], why: str
) -> None:
    """The text strategy reads a grid out of any aligned text; this is the guard."""
    assert is_table_like(rows, TableConfig()) is False, why


def test_a_sparse_grid_is_refused() -> None:
    sparse = (
        ("標題", "", "", ""),
        ("", "", "1,234", ""),
        ("", "", "", ""),
        ("", "", "", ""),
    )
    assert is_table_like(sparse, TableConfig()) is False


def test_thresholds_are_configurable_for_dev_tuning() -> None:
    prose = (("風險因素", "說明"), ("市場波動", "可能影響獲利"))
    assert is_table_like(prose, TableConfig(min_numeric_cells=0)) is True


def test_config_rejects_impossible_thresholds() -> None:
    with pytest.raises(ValueError, match="at least one row"):
        TableConfig(min_rows=0)
    with pytest.raises(ValueError, match="min_fill_ratio"):
        TableConfig(min_fill_ratio=1.5)


def test_config_maps_to_plumber_settings() -> None:
    assert TableConfig(strategy="text").plumber_settings() == {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
    }


# ------------------------------------------------------------------- table shape


def test_shape_and_counts() -> None:
    item = table()
    assert item.n_rows == 3
    assert item.n_cols == 3
    assert item.cells == 9
    assert item.numeric_cells == 6
    assert item.fill_ratio == 1.0


def test_cell_access_is_bounds_safe() -> None:
    item = table()
    assert item.cell(1, 1) == "2,894,308"
    assert item.cell(99, 0) == ""
    assert item.cell(0, 99) == ""
    assert item.cell(-5, 0) == ""


def test_cell_ref_is_citable() -> None:
    """The citation contract needs a coordinate that resolves to one cell."""
    assert table(page=102).cell_ref(1, 1) == "p102:r1:c1"


def test_ragged_rows_do_not_break_shape() -> None:
    ragged = (("a", "b", "c"), ("d",))
    item = table(rows=ragged)
    assert item.n_cols == 3
    assert item.cell(1, 2) == ""


# ---------------------------------------------------------------------- to_text


def test_rendering_states_the_unit_first() -> None:
    """A retrieved table chunk that does not say 千元 invites a unit error."""
    text = table(units=UnitSpec(unit="千元", currency="TWD")).to_text()
    assert text.startswith("單位：TWD千元")
    assert "營業收入 | 2,894,308 | 2,161,736" in text


def test_rendering_without_a_unit_omits_the_header() -> None:
    assert table().to_text().startswith("項目 |")


def test_rendering_marks_a_continuation() -> None:
    item = Table(
        page=103,
        bbox=BBox(60, 50, 500, 400),
        rows=STATEMENT_ROWS,
        units=UnitSpec(unit="千元", currency="TWD"),
        continues_from_page=102,
    )
    assert "（接續第 102 頁）" in item.to_text()
    assert item.is_continuation is True


# ------------------------------------------------------------------ continuations


HEIGHTS = {102: 842.0, 103: 842.0, 104: 842.0}


def test_a_table_continuing_across_a_page_break_is_linked() -> None:
    first = table(page=102, y0=500, y1=800)
    second = table(page=103, y0=60, y1=300)
    linked = link_continuations((first, second), HEIGHTS)
    assert linked[0].continues_from_page is None
    assert linked[1].continues_from_page == 102


def test_a_continuation_inherits_the_unit_from_its_first_part() -> None:
    """The units row appears once, above the first part."""
    first = table(page=102, y0=500, y1=800, units=UnitSpec(unit="千元", currency="TWD"))
    second = table(page=103, y0=60, y1=300)
    linked = link_continuations((first, second), HEIGHTS)
    assert linked[1].unit == "千元"
    assert linked[1].currency == "TWD"


def test_a_table_starting_low_on_the_page_is_not_a_continuation() -> None:
    first = table(page=102, y0=500, y1=800)
    second = table(page=103, y0=600, y1=800)
    assert link_continuations((first, second), HEIGHTS)[1].continues_from_page is None


def test_a_predecessor_ending_high_does_not_continue() -> None:
    first = table(page=102, y0=60, y1=200)
    second = table(page=103, y0=60, y1=300)
    assert link_continuations((first, second), HEIGHTS)[1].continues_from_page is None


def test_a_different_column_count_is_not_a_continuation() -> None:
    """A new table that happens to start high is not the previous one continuing."""
    first = table(page=102, y0=500, y1=800)
    second = table(page=103, rows=(("a", "b"), ("1", "2")), y0=60, y1=300)
    assert link_continuations((first, second), HEIGHTS)[1].continues_from_page is None


def test_a_gap_of_more_than_one_page_is_not_a_continuation() -> None:
    first = table(page=102, y0=500, y1=800)
    third = table(page=104, y0=60, y1=300)
    assert link_continuations((first, third), HEIGHTS)[1].continues_from_page is None


def test_a_continuation_keeps_its_own_unit_when_it_states_one() -> None:
    first = table(page=102, y0=500, y1=800, units=UnitSpec(unit="千元", currency="TWD"))
    second = table(page=103, y0=60, y1=300, units=UnitSpec(unit="百萬元", currency="TWD"))
    assert link_continuations((first, second), HEIGHTS)[1].unit == "百萬元"


def test_linking_without_page_heights_changes_nothing() -> None:
    first = table(page=102, y0=500, y1=800)
    second = table(page=103, y0=60, y1=300)
    assert link_continuations((first, second), {})[1].continues_from_page is None


# --------------------------------------------------------------------- as blocks


def test_tables_become_atomic_blocks() -> None:
    blocks = tables_to_blocks(
        (table(page=1, units=UnitSpec(unit="千元", currency="TWD")), table(page=2))
    )
    assert [block.kind for block in blocks] == ["table", "table"]
    assert [block.page for block in blocks] == [1, 2]
    assert "單位" in blocks[0].text
    assert blocks[0].order == 0 and blocks[1].order == 1


def test_no_tables_yields_no_blocks() -> None:
    assert tables_to_blocks(()) == ()


# ------------------------------------------------------------------ end to end


def test_extraction_finds_the_statement_table_in_a_real_pdf(tmp_path: Path) -> None:
    """The synthetic filing's page 2 is a units row plus four statement rows."""
    filing = build_filing(tmp_path / "filing.pdf")
    tables = extract_tables(filing.path)

    assert tables, "no table found in the synthetic statement page"
    with_unit = [item for item in tables if item.units.is_stated]
    assert with_unit, "the 單位：新台幣千元 row was not picked up"
    assert with_unit[0].units.unit == "千元"
    assert with_unit[0].units.currency == "TWD"
    assert any("營業收入" in cell for item in tables for row in item.rows for cell in row)


def test_extraction_rejects_a_non_pdf(tmp_path: Path) -> None:
    broken = tmp_path / "not.pdf"
    broken.write_bytes(b"nope")
    with pytest.raises(ParsingError, match="cannot open"):
        extract_tables(broken)


def test_extraction_can_be_limited_to_a_page_range(tmp_path: Path) -> None:
    """Full-corpus extraction costs ~0.16s/page, so callers can bound it."""
    filing = build_filing(tmp_path / "filing.pdf")
    assert extract_tables(filing.path, pages=range(0, 1)) == ()


def test_a_page_range_beyond_the_document_stops_cleanly(tmp_path: Path) -> None:
    filing = build_filing(tmp_path / "filing.pdf")
    assert extract_tables(filing.path, pages=range(50, 60)) == ()


# ------------------------------------------------- document-scoped units (D-018)


def test_a_unit_declared_once_for_the_notes_governs_the_whole_section() -> None:
    """2330-FY2024-FS declares the scale on p.16 and prints revenue on p.55.

    Looking only near each table found nothing on p.55, so 62 of 65 tables were recorded
    as having no stated unit -- not because the filing is silent, but because the window
    was 39 pages too narrow.
    """
    pages = [
        "封面",
        "會計師查核報告",
        "合併財務報告附註 民國113及112年度（除另予註明者外，金額為新台幣仟元）一、公司沿革",
        "二一、營業收入 產品別 晶圓 其他",
    ]
    spec = document_unit(pages)
    assert spec.unit == "千元"
    assert spec.currency == "TWD"
    assert spec.declared_on_page == 3
    assert spec.is_inherited is True


def test_a_declaration_without_the_qualifier_is_not_document_scoped() -> None:
    """單位：新台幣仟元 above a table is a claim about that table, not the section."""
    pages = ["合併財務報告附註 一、公司沿革", "單位：新台幣仟元 資產 負債"]
    assert document_unit(pages).is_stated is False


def test_a_qualifier_away_from_the_notes_is_not_document_scoped() -> None:
    pages = ["附表一 單位：除另予註明外，為新台幣仟元 資金貸與他人"]
    assert document_unit(pages).is_stated is False


def test_a_filing_that_never_declares_a_scale_yields_nothing() -> None:
    """Silence is reported as silence. Assuming 千元 is the thousand-fold error."""
    assert document_unit(["附註 一、公司沿革", "營業收入 $2,894,307,699"]).is_stated is False


def test_inheritance_runs_forwards_only() -> None:
    """A table printed before the declaration was not inside its scope yet."""
    default = UnitSpec(
        unit="千元", currency="TWD", qualified=True, scope="document", declared_on_page=16
    )
    before = Table(page=9, bbox=BBox(0, 0, 10, 10), rows=(("a", "1"), ("b", "2")))
    after = Table(page=55, bbox=BBox(0, 0, 10, 10), rows=(("a", "1"), ("b", "2")))
    kept, given = inherit_units((before, after), default)
    assert kept.unit is None
    assert given.unit == "千元"
    assert given.units.declared_on_page == 16


def test_a_local_declaration_is_never_overwritten() -> None:
    """What the page says beats what a note 39 pages back said."""
    default = UnitSpec(unit="千元", qualified=True, scope="document", declared_on_page=1)
    local = Table(
        page=50,
        bbox=BBox(0, 0, 10, 10),
        rows=(("a", "1"), ("b", "2")),
        units=UnitSpec(unit="百萬元", currency="TWD"),
    )
    (result,) = inherit_units((local,), default)
    assert result.unit == "百萬元"
    assert result.units.is_inherited is False


def test_nothing_is_inherited_from_an_unstated_default() -> None:
    table = Table(page=5, bbox=BBox(0, 0, 10, 10), rows=(("a", "1"), ("b", "2")))
    (result,) = inherit_units((table,), UnitSpec())
    assert result.unit is None


def test_an_inherited_unit_says_where_it_came_from() -> None:
    """So an answer can cite the page that makes its figure interpretable."""
    spec = UnitSpec(
        unit="千元", currency="TWD", qualified=True, scope="document", declared_on_page=16
    )
    assert "承第 16 頁之宣告" in spec.describe()


# --------------------------------------- the exception, written two ways


@pytest.mark.parametrize(
    "declaration",
    [
        "單位：新台幣仟元，惟每股盈餘為元",  # 2882-FY2024-FS p10
        "單位：新台幣仟元(除每股盈餘為新台幣元外)",  # 2317-FY2024-FS p14
    ],
)
def test_both_spellings_of_the_eps_exception_are_read(declaration: str) -> None:
    """The first version read only the 惟 form.

    On the other, it reported no exception and therefore a uniform spec, so the numeric
    route would have applied 千元 to earnings per share with full confidence -- wrong by
    a factor of a thousand, and wrong in a way that looks right.
    """
    spec = detect_unit(declaration)
    assert spec.unit == "千元"
    assert spec.exception == "每股盈餘為元"
    assert spec.is_uniform is False


def test_the_document_scope_qualifier_is_not_read_as_an_exception() -> None:
    """除另予註明者外 begins with 除 but names no substitute unit."""
    spec = detect_unit("（除另予註明者外，金額為新台幣仟元）")
    assert spec.exception is None
    assert spec.qualified is True
