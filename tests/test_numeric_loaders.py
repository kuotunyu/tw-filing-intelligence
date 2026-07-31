"""Loading must preserve the unit each column reports in, not a dataset-wide guess."""

from __future__ import annotations

from decimal import Decimal

import pytest

from twfi.numeric.loaders import (
    OPENAPI_DATASETS,
    load_openapi_rows,
    period_of,
    split_account_unit,
)
from twfi.numeric.store import NumericStore

#: Shaped like the real response, including the empty cells the endpoint returns.
INCOME_ROW = {
    "出表日期": "1150728",
    "年度": "115",
    "季別": "1",
    "公司代號": "2330",
    "公司名稱": "台積電",
    "營業收入": "1134103440.00",
    "營業成本": "382808019.00",
    "營業毛利（毛損）": "751295421.00",
    "基本每股盈餘（元）": "22.08",
    "停業單位損益": "",
    "未實現銷貨（損）益": "",
}

FH_ROW = {
    "出表日期": "1150728",
    "年度": "115",
    "季別": "1",
    "公司代號": "2882",
    "公司名稱": "國泰金",
    "利息淨收益": "12345678.00",
    "保險負債準備淨變動": "-2345678.00",
    "本期稅後淨利（淨損）": "9876543.00",
}

RATIO_ROW = {
    "出表日期": "1150728",
    "年度": "115",
    "季別": "1",
    "公司代號": "2330",
    "公司名稱": "台積電",
    "營業收入(百萬元)": "1134103",
    "毛利率(%)(營業毛利)/(營業收入)": "66.25",
}

#: t187ap14_L, verbatim from the real response for 2882. Note the unit-less 營業收入,
#: the '--' placeholders, and the free-text 每股面額.
EPS_ROW = {
    "出表日期": "1150728",
    "年度": "115",
    "季別": "1",
    "公司代號": "2882",
    "公司名稱": "國泰金融控股股份有限公司",
    "產業別": "金融保險業",
    "基本每股盈餘(元)": "2.15",
    "普通股每股面額": "新台幣                 10.0000元",
    "營業收入": "72538053.00",
    "營業利益": "--",
    "營業外收入及支出": "--",
    "稅後淨利": "31655932.00",
}


# ------------------------------------------------------------ column parsing


@pytest.mark.parametrize(
    ("column", "account", "unit"),
    [
        ("營業收入(百萬元)", "營業收入", "百萬元"),
        ("基本每股盈餘（元）", "基本每股盈餘", "元"),
        ("營業收入(千元)", "營業收入", "千元"),
        ("營業收入(仟元)", "營業收入", "千元"),
        ("營業收入-上月比較增減(%)", "營業收入-上月比較增減", "%"),
    ],
)
def test_a_column_name_carrying_a_unit_is_split(column: str, account: str, unit: str) -> None:
    assert split_account_unit(column, "千元") == (account, unit)


def test_a_column_without_a_unit_takes_the_dataset_default() -> None:
    assert split_account_unit("營業收入", "千元") == ("營業收入", "千元")


def test_a_column_without_a_unit_and_no_default_stays_unstated() -> None:
    assert split_account_unit("毛利率", None) == ("毛利率", None)


def test_the_account_name_stays_verbatim_apart_from_the_unit() -> None:
    """Inventing a tidier name would break the link back to the source column."""
    account, unit = split_account_unit("毛利率(%)(營業毛利)/(營業收入)", None)
    assert unit == "%"
    assert account == "毛利率(營業毛利)/(營業收入)"


# ------------------------------------------------------------------- periods


def test_the_period_comes_from_the_row_not_the_request() -> None:
    """These endpoints are single-period snapshots; the row states which period."""
    assert period_of({"年度": "115", "季別": "1"}) == "FY2026Q1"


def test_a_row_without_a_quarter_is_annual() -> None:
    assert period_of({"年度": "113"}) == "FY2024"


def test_a_row_without_a_year_has_no_period() -> None:
    """A figure without a period cannot be compared with anything."""
    assert period_of({"季別": "1"}) is None
    assert period_of({"年度": "  "}) is None


# ------------------------------------------------------------------- loading


def test_income_rows_load_with_units_and_provenance() -> None:
    companies, items = load_openapi_rows(
        "twse-openapi-t187ap06_L_ci",
        [INCOME_ROW],
        source_url="https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
    )
    assert companies[0].code == "2330"
    assert companies[0].industry_schema == "general"

    by_account = {entry.account: entry for entry in items}
    revenue = by_account["營業收入"]
    assert revenue.value == Decimal("1134103440.00")
    assert revenue.unit == "千元"
    assert revenue.currency == "TWD"
    assert revenue.statement == "income"
    assert revenue.period == "FY2026Q1"
    assert revenue.source_kind == "openapi_current"
    assert "t187ap06_L_ci" in revenue.source_ref
    assert revenue.source_url is not None


def test_a_per_column_unit_overrides_the_dataset_default() -> None:
    _companies, items = load_openapi_rows("twse-openapi-t187ap06_L_ci", [INCOME_ROW])
    eps = next(entry for entry in items if entry.account == "基本每股盈餘")
    assert eps.unit == "元", "EPS is in 元 even though the statement is in 千元"
    assert eps.value == Decimal("22.08")


def test_empty_cells_are_skipped_not_stored_as_zero() -> None:
    _companies, items = load_openapi_rows("twse-openapi-t187ap06_L_ci", [INCOME_ROW])
    assert "停業單位損益" not in {entry.account for entry in items}


def test_key_columns_are_not_stored_as_figures() -> None:
    _companies, items = load_openapi_rows("twse-openapi-t187ap06_L_ci", [INCOME_ROW])
    accounts = {entry.account for entry in items}
    assert accounts.isdisjoint({"公司代號", "公司名稱", "出表日期", "年度", "季別"})


def test_a_financial_holding_row_loads_under_its_own_schema() -> None:
    companies, items = load_openapi_rows("twse-openapi-t187ap06_L_fh", [FH_ROW])
    assert companies[0].industry_schema == "financial_holding"
    accounts = {entry.account for entry in items}
    assert "利息淨收益" in accounts
    assert "營業收入" not in accounts, "a 金控 files no revenue line at all"


def test_a_negative_figure_keeps_its_sign() -> None:
    _companies, items = load_openapi_rows("twse-openapi-t187ap06_L_fh", [FH_ROW])
    reserve = next(entry for entry in items if entry.account == "保險負債準備淨變動")
    assert reserve.value == Decimal("-2345678.00")


def test_a_percentage_column_carries_no_currency() -> None:
    _companies, items = load_openapi_rows("twse-openapi-t187ap17_L", [RATIO_ROW])
    margin = next(entry for entry in items if entry.account.startswith("毛利率"))
    assert margin.unit == "%"
    assert margin.currency is None


def test_company_filtering_keeps_the_store_to_the_study() -> None:
    """A 1,045-row endpoint would otherwise load every listed company."""
    other = dict(INCOME_ROW, 公司代號="1234", 公司名稱="別家")
    companies, items = load_openapi_rows(
        "twse-openapi-t187ap06_L_ci", [INCOME_ROW, other], company_codes={"2330"}
    )
    assert [entry.code for entry in companies] == ["2330"]
    assert {entry.company_code for entry in items} == {"2330"}


def test_a_column_with_no_unit_stays_unusable_rather_than_being_guessed() -> None:
    """t187ap14_L publishes 營業收入 with no unit marker at all.

    Loading it is right -- the source really does publish it -- but assuming 千元 would
    be how a figure ends up wrong by a factor of a thousand while looking correct. With
    no unit it is stored and rendered unusable, which the calculator then enforces.
    """
    row = dict(EPS_ROW)
    _companies, items = load_openapi_rows("twse-openapi-t187ap14_L", [row])
    revenue = next(entry for entry in items if entry.account == "營業收入")
    assert revenue.value == Decimal("72538053.00")
    assert revenue.unit is None
    assert revenue.is_usable is False


def test_the_eps_aggregate_reports_revenue_for_a_financial_holding_company() -> None:
    """The sharper version of the 金控 finding.

    Its income statement has no 營業收入 line, but this aggregate synthesises one. The
    two are not the same quantity, and no unit check would catch a comparison between
    them -- which is why the account vocabulary is kept per statement.
    """
    _companies, items = load_openapi_rows("twse-openapi-t187ap14_L", [EPS_ROW])
    accounts = {entry.account for entry in items}
    assert "營業收入" in accounts
    assert all(entry.statement == "ratio" for entry in items), "not an income-statement figure"


def test_a_double_dash_placeholder_is_absent_not_zero() -> None:
    """2882 reports 營業利益 as '--' in this endpoint."""
    _companies, items = load_openapi_rows("twse-openapi-t187ap14_L", [EPS_ROW])
    assert "營業利益" not in {entry.account for entry in items}


def test_an_undeclared_dataset_is_refused() -> None:
    with pytest.raises(KeyError):
        load_openapi_rows("twse-openapi-something-else", [INCOME_ROW])


def test_every_declared_dataset_has_a_spec() -> None:
    for spec in OPENAPI_DATASETS.values():
        assert spec.statement in {"income", "balance", "ratio", "monthly_revenue"}
        assert spec.industry_schema in {"general", "financial_holding"}


# --------------------------------------------------------------- the unit trap


def test_one_account_two_scales_survives_into_the_store() -> None:
    """t187ap17_L reports 營業收入 in 百萬元; t187ap06_L_ci reports it in 千元.

    Both are loaded on purpose. One account name reported at two scales by one issuer
    is precisely the conflict the cross-source template exists to surface, and it would
    be invisible if the loader silently normalised one of them.
    """
    with NumericStore() as store:
        for dataset, rows in (
            ("twse-openapi-t187ap06_L_ci", [INCOME_ROW]),
            ("twse-openapi-t187ap17_L", [RATIO_ROW]),
        ):
            companies, items = load_openapi_rows(dataset, rows)
            store.add_companies(companies)
            store.add_line_items(items)

        found = store.find("2330", "營業收入", "FY2026Q1")
        assert len(found) == 2
        assert {entry.unit for entry in found} == {"千元", "百萬元"}
        assert {entry.statement for entry in found} == {"income", "ratio"}
