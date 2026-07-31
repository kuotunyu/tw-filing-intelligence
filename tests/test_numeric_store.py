"""The numeric route must refuse clearly rather than answer plausibly."""

from __future__ import annotations

from decimal import Decimal

import pytest

from twfi.errors import NumericRouteError, TemplateMissError, UnitMismatchError
from twfi.numeric.calculator import Operand, difference, growth_rate, ratio
from twfi.numeric.sql_tools import (
    TEMPLATES,
    account_ratio,
    cross_source_check,
    lookup,
    period_delta,
    period_growth,
    resolve_template,
)
from twfi.numeric.store import CompanyRow, LineItem, NumericStore

TSMC = CompanyRow(code="2330", name="台積電", industry_schema="general")
CATHAY = CompanyRow(code="2882", name="國泰金", industry_schema="financial_holding")


def item(
    *,
    company: str = "2330",
    account: str = "營業收入",
    period: str = "FY2024",
    value: str | None = "2894308",
    unit: str | None = "千元",
    currency: str | None = "TWD",
    statement: str = "income",
    basis: str = "consolidated",
    schema: str = "general",
    source_kind: str = "openapi_current",
    uniform: bool = True,
    note: str | None = None,
) -> LineItem:
    return LineItem(
        company_code=company,
        period=period,
        statement=statement,  # type: ignore[arg-type]
        basis=basis,  # type: ignore[arg-type]
        industry_schema=schema,  # type: ignore[arg-type]
        account=account,
        value=None if value is None else Decimal(value),
        unit=unit,
        currency=currency,
        source_kind=source_kind,  # type: ignore[arg-type]
        source_ref=f"{source_kind}:{company}:{period}:{account}",
        unit_is_uniform=uniform,
        unit_note=note,
    )


@pytest.fixture()
def store():
    with NumericStore() as opened:
        opened.add_companies([TSMC, CATHAY])
        yield opened


# ------------------------------------------------------------------- storage


def test_a_figure_cannot_be_stored_without_provenance() -> None:
    with pytest.raises(ValueError, match="where it came from"):
        LineItem(
            company_code="2330",
            period="FY2024",
            statement="income",
            basis="consolidated",
            industry_schema="general",
            account="營業收入",
            value=Decimal(1),
            unit="千元",
            currency="TWD",
            source_kind="openapi_current",
            source_ref="",
        )


def test_round_trip_preserves_value_unit_and_source(store) -> None:
    store.add_line_items([item()])
    found = store.require("2330", "營業收入", "FY2024")
    assert found.value == Decimal(2894308)
    assert found.unit == "千元"
    assert found.currency == "TWD"
    assert found.source_kind == "openapi_current"
    assert "2330" in found.citation()


def test_the_two_spellings_of_thousand_read_back_as_one(store) -> None:
    store.add_line_items([item(unit="仟元")])
    assert store.require("2330", "營業收入", "FY2024").unit == "千元"


def test_two_sources_for_one_figure_both_survive(store) -> None:
    """Cross-document questions depend on being able to see both."""
    store.add_line_items(
        [item(source_kind="openapi_current"), item(value="2894000", source_kind="extracted_table")]
    )
    assert len(store.find("2330", "營業收入", "FY2024")) == 2


def test_reloading_the_same_source_replaces_rather_than_duplicates(store) -> None:
    store.add_line_items([item()])
    store.add_line_items([item(value="9999")])
    found = store.find("2330", "營業收入", "FY2024")
    assert len(found) == 1
    assert found[0].value == Decimal(9999)


def test_sources_are_recorded(store) -> None:
    store.record_source(
        "openapi_current", "twse-openapi-t187ap06_L_ci", loaded_at="2026-07-31", rows_loaded=33
    )
    assert store.sources() == [("openapi_current", "twse-openapi-t187ap06_L_ci", 33)]


# ------------------------------------------------------------------ refusals


def test_a_financial_holding_company_has_no_revenue_line(store) -> None:
    """2882 files 利息淨收益, not 營業收入. The refusal must say so."""
    store.add_line_items(
        [
            item(company="2882", account="利息淨收益", schema="financial_holding", value="123456"),
            item(company="2882", account="本期稅後淨利", schema="financial_holding", value="98765"),
        ]
    )
    with pytest.raises(NumericRouteError) as caught:
        store.require("2882", "營業收入", "FY2024")
    message = str(caught.value)
    assert "does not file an account named '營業收入'" in message
    assert "financial holding" in message
    assert "利息淨收益" in message


def test_a_missing_period_names_the_periods_that_exist(store) -> None:
    store.add_line_items([item(period="FY2023")])
    with pytest.raises(NumericRouteError, match=r"available periods: \['FY2023'\]"):
        store.require("2330", "營業收入", "FY2024")


def test_disagreeing_sources_are_not_silently_resolved(store) -> None:
    """Picking a winner here is how a conflict becomes a confident wrong answer."""
    store.add_line_items(
        [item(source_kind="openapi_current"), item(value="1", source_kind="extracted_table")]
    )
    with pytest.raises(NumericRouteError, match="will not choose between them"):
        store.require("2330", "營業收入", "FY2024")


def test_the_refusal_names_what_actually_differs(store) -> None:
    """ "Two sources disagree" is useless to a caller who cannot see which is which.

    The realistic case is the one this corpus contains: 營業收入 in the income statement
    in 千元, and 營業收入 in an aggregate in 百萬元.
    """
    store.add_line_items(
        [
            item(statement="income", unit="千元", value="1134103440"),
            item(
                statement="ratio",
                unit="百萬元",
                value="1134103",
                source_kind="extracted_table",
            ),
        ]
    )
    with pytest.raises(NumericRouteError) as caught:
        store.require("2330", "營業收入", "FY2024")
    message = str(caught.value)
    assert "income/千元" in message
    assert "ratio/百萬元" in message
    assert "statement=" in message, "the message must say how to disambiguate"


def test_a_statement_filter_resolves_the_ambiguity(store) -> None:
    store.add_line_items(
        [
            item(statement="income", unit="千元", value="1134103440"),
            item(statement="ratio", unit="百萬元", value="1134103"),
        ]
    )
    found = store.require("2330", "營業收入", "FY2024", statement="income")
    assert found.unit == "千元"


def test_a_source_filter_resolves_the_ambiguity(store) -> None:
    store.add_line_items(
        [item(source_kind="openapi_current"), item(value="1", source_kind="extracted_table")]
    )
    assert store.require(
        "2330", "營業收入", "FY2024", source_kind="extracted_table"
    ).value == Decimal(1)


def test_an_unknown_company_is_named(store) -> None:
    with pytest.raises(NumericRouteError, match="9999 is not in the numeric store"):
        store.industry_schema_of("9999")


# ---------------------------------------------------------------- comparability


def test_figures_in_different_units_are_not_combined() -> None:
    """t187ap17_L reports 營業收入 in 百萬元, t187ap06_L_ci in 千元."""
    thousands = Operand("營業收入", item(unit="千元"))
    millions = Operand("營業收入", item(unit="百萬元", value="2894"))
    with pytest.raises(UnitMismatchError, match="unit mismatch"):
        difference(thousands, millions)


def test_consolidated_and_parent_only_are_different_quantities() -> None:
    consolidated = Operand("營業收入", item(basis="consolidated"))
    parent = Operand("營業收入", item(basis="parent_only"))
    with pytest.raises(UnitMismatchError, match="consolidated with parent_only"):
        difference(consolidated, parent)


def test_currencies_must_match() -> None:
    twd = Operand("營業收入", item(currency="TWD"))
    usd = Operand("營業收入", item(currency="USD"))
    with pytest.raises(UnitMismatchError, match="currency mismatch"):
        difference(twd, usd)


def test_a_figure_whose_source_qualified_its_unit_is_refused() -> None:
    """The 2882 case: 「單位：新台幣仟元，惟每股盈餘為元」."""
    eps = Operand(
        "基本每股盈餘",
        item(account="基本每股盈餘", value="4.5", uniform=False, note="每股盈餘為元"),
    )
    other = Operand("基本每股盈餘", item(account="基本每股盈餘", value="4.0"))
    with pytest.raises(UnitMismatchError, match="每股盈餘為元"):
        difference(eps, other)


def test_a_figure_without_a_unit_is_refused() -> None:
    with pytest.raises(UnitMismatchError, match="no unit was stated"):
        difference(Operand("x", item(unit=None)), Operand("x", item()))


def test_an_empty_value_is_refused() -> None:
    with pytest.raises(UnitMismatchError, match="recorded no value"):
        difference(Operand("x", item(value=None)), Operand("x", item()))


# ------------------------------------------------------------------ arithmetic


def test_a_difference_shows_its_formula() -> None:
    later = Operand("營業收入", item(period="FY2024", value="2894308"))
    earlier = Operand("營業收入", item(period="FY2023", value="2161736"))
    result = difference(later, earlier)
    assert result.value == Decimal(732572)
    assert result.unit == "千元"
    assert "2,894,308 − 2,161,736 = 732,572" in result.formula
    assert len(result.citations()) == 2


def test_a_growth_rate_shows_its_formula() -> None:
    later = Operand("營業收入", item(period="FY2024", value="2894308"))
    earlier = Operand("營業收入", item(period="FY2023", value="2161736"))
    result = growth_rate(later, earlier)
    # 732,572 / 2,161,736 = 0.338877…, half-up to two places.
    assert result.value == Decimal("33.89")
    assert result.unit == "%"
    assert "÷" in result.formula and "× 100" in result.formula


def test_growth_from_zero_is_undefined_not_infinite() -> None:
    with pytest.raises(UnitMismatchError, match="undefined"):
        growth_rate(Operand("x", item(value="10")), Operand("x", item(value="0")))


def test_a_ratio_cancels_its_units() -> None:
    gross = Operand("營業毛利", item(account="營業毛利", value="1628157"))
    revenue = Operand("營業收入", item(value="2894308"))
    result = ratio(gross, revenue)
    assert result.value == Decimal("56.25")
    assert result.unit == "%"
    assert result.currency is None


def test_a_ratio_by_zero_is_refused() -> None:
    with pytest.raises(UnitMismatchError, match="is zero"):
        ratio(Operand("a", item(value="1")), Operand("b", item(value="0")))


def test_a_comparison_needs_two_operands() -> None:
    with pytest.raises(UnitMismatchError, match="at least two operands"):
        from twfi.numeric.calculator import require_comparable

        require_comparable((Operand("x", item()),))


# ------------------------------------------------------------------- templates


def test_the_template_set_is_closed() -> None:
    """The router picks a template and fills parameters; it cannot express more."""
    assert set(TEMPLATES) == {"lookup", "difference", "growth_rate", "ratio", "cross_source_check"}
    for name in TEMPLATES:
        assert resolve_template(name) == name


def test_an_unknown_template_is_a_capability_limit_not_a_crash() -> None:
    with pytest.raises(TemplateMissError, match="no numeric template named"):
        resolve_template("median_over_five_years")


def test_lookup_template(store) -> None:
    store.add_line_items([item()])
    assert lookup(store, "2330", "營業收入", "FY2024").item.value == Decimal(2894308)


def test_period_delta_template(store) -> None:
    store.add_line_items(
        [item(period="FY2024", value="2894308"), item(period="FY2023", value="2161736")]
    )
    result = period_delta(store, "2330", "營業收入", "FY2024", "FY2023")
    assert result.value == Decimal(732572)
    assert result.template == "difference"


def test_period_growth_template(store) -> None:
    store.add_line_items(
        [item(period="FY2024", value="2894308"), item(period="FY2023", value="2161736")]
    )
    assert period_growth(store, "2330", "營業收入", "FY2024", "FY2023").unit == "%"


def test_account_ratio_template(store) -> None:
    store.add_line_items([item(), item(account="營業毛利", value="1628157")])
    result = account_ratio(store, "2330", "營業毛利", "營業收入", "FY2024")
    assert result.value == Decimal("56.25")
    assert result.operands[0].label == "營業毛利"


# ------------------------------------------------------------- cross-source


def test_agreeing_sources_are_reported_as_agreeing(store) -> None:
    store.add_line_items(
        [item(source_kind="openapi_current"), item(value="2894300", source_kind="extracted_table")]
    )
    result = cross_source_check(store, "2330", "營業收入", "FY2024")
    assert result.agree is True
    assert len(result.citations()) == 2


def test_disagreeing_sources_are_reported_not_resolved(store) -> None:
    store.add_line_items(
        [item(source_kind="openapi_current"), item(value="1000000", source_kind="extracted_table")]
    )
    result = cross_source_check(store, "2330", "營業收入", "FY2024")
    assert result.agree is False
    assert "differ by" in result.note


def test_sources_reporting_different_units_never_agree(store) -> None:
    """One account name, two scales -- the 千元 versus 百萬元 trap."""
    store.add_line_items(
        [
            item(source_kind="openapi_current", unit="千元", value="1134103440"),
            item(source_kind="extracted_table", unit="百萬元", value="1134103"),
        ]
    )
    result = cross_source_check(store, "2330", "營業收入", "FY2024")
    assert result.agree is False
    assert "different units" in result.note


def test_a_single_source_is_a_template_miss(store) -> None:
    store.add_line_items([item()])
    with pytest.raises(TemplateMissError, match="needs two sources"):
        cross_source_check(store, "2330", "營業收入", "FY2024")


def test_an_empty_source_blocks_agreement(store) -> None:
    store.add_line_items(
        [item(source_kind="openapi_current"), item(value=None, source_kind="extracted_table")]
    )
    assert cross_source_check(store, "2330", "營業收入", "FY2024").agree is False
