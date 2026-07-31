"""Loading TWSE OpenAPI datasets into the numeric store.

Three things this has to get right, all of them measured from the real responses:

* **Which statement vocabulary applies.** ``t187ap06_L_ci`` is the general-industry
  income statement and ``t187ap06_L_fh`` the financial-holding one. They are not
  variants of each other: 2882 has 25 columns and no 營業收入 at all. The dataset a
  row came from therefore decides the company's ``industry_schema``.
* **The unit each column reports in.** Column names carry it -- ``營業收入(百萬元)``,
  ``基本每股盈餘（元）``, ``毛利率(%)`` -- and where they do not, the dataset default
  applies. This matters because ``t187ap17_L`` reports 營業收入 in 百萬元 while
  ``t187ap06_L_ci`` reports the same account in 千元. Loading both is deliberate:
  one account name, two scales, from one issuer, is exactly the conflict the
  cross-source template exists to surface.
* **The period.** These endpoints are single-period snapshots (DATA_PROVENANCE 8.1),
  and the period comes from the row's own 年度/季別 in 民國 years -- never from the
  request.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from twfi.numeric.amounts import canonical_unit, parse_amount
from twfi.numeric.store import (
    Basis,
    CompanyRow,
    IndustrySchema,
    LineItem,
    Statement,
)

__all__ = [
    "ROC_EPOCH",
    "DatasetSpec",
    "OPENAPI_DATASETS",
    "split_account_unit",
    "period_of",
    "load_openapi_rows",
]

ROC_EPOCH = 1911

#: Columns that identify a row rather than report a figure.
_KEY_COLUMNS = frozenset({"公司代號", "公司名稱", "出表日期", "年度", "季別", "產業別", "備註"})

#: A parenthetical that is purely a unit, e.g. ``(百萬元)`` or ``（元）``.
_UNIT_PARENTHETICAL = re.compile(
    r"[（(]\s*(?P<unit>%|千元|仟元|百萬元|億元|萬元|元|千股|仟股|股|倍)\s*[)）]"
)


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """How to interpret one OpenAPI dataset."""

    dataset_id: str
    statement: Statement
    industry_schema: IndustrySchema
    default_unit: str | None
    currency: str | None = "TWD"
    basis: Basis = "consolidated"
    #: Whether this endpoint's industry split is authoritative. The ``_ci``/``_fh``
    #: statement endpoints are, because TWSE publishes one per industry family. The
    #: aggregate ones are not: ``t187ap17_L`` covers every listed company including
    #: the 金控, so taking its schema at face value would relabel 2882 as general
    #: industry and undo the very distinction that makes it a hard case.
    declares_industry: bool = True


#: The datasets declared in data/manifests/structured.yaml, and what each one means.
#: 財務報表 endpoints report in 千元 unless a column says otherwise.
OPENAPI_DATASETS: dict[str, DatasetSpec] = {
    "twse-openapi-t187ap06_L_ci": DatasetSpec(
        "twse-openapi-t187ap06_L_ci", "income", "general", "千元"
    ),
    "twse-openapi-t187ap06_L_fh": DatasetSpec(
        "twse-openapi-t187ap06_L_fh", "income", "financial_holding", "千元"
    ),
    "twse-openapi-t187ap07_L_ci": DatasetSpec(
        "twse-openapi-t187ap07_L_ci", "balance", "general", "千元"
    ),
    "twse-openapi-t187ap07_L_fh": DatasetSpec(
        "twse-openapi-t187ap07_L_fh", "balance", "financial_holding", "千元"
    ),
    # 營益分析. Its 營業收入 column is named 營業收入(百萬元) -- a thousand times the
    # scale the income-statement endpoint uses for the same account name. It excludes
    # the 金控, which makes sense: 毛利率 is not a quantity a holding company reports.
    "twse-openapi-t187ap17_L": DatasetSpec(
        "twse-openapi-t187ap17_L", "ratio", "general", None, declares_industry=False
    ),
    # 各產業EPS統計. Its 營業收入 column carries no unit at all, and the dataset default
    # stays None rather than assuming 千元. The figure is still loaded -- the source
    # really does publish it -- but with no unit it cannot enter a calculation, which
    # is the correct outcome: guessing a scale is how a number ends up wrong by a
    # factor of a thousand while looking right.
    #
    # This endpoint *does* cover 2882, and reports 營業收入 = 72,538,053 for it. So the
    # precise finding is not "a 金控 has no revenue figure anywhere" but the sharper
    # one: its income statement has no such line, an aggregate synthesises one, and the
    # two are not the same quantity. Comparing them across issuers would be wrong in a
    # way no unit check could catch.
    "twse-openapi-t187ap14_L": DatasetSpec(
        "twse-openapi-t187ap14_L", "ratio", "general", None, declares_industry=False
    ),
}


def split_account_unit(column: str, default_unit: str | None) -> tuple[str, str | None]:
    """Split a column name into its account and the unit it reports in.

    The account keeps everything except the unit parenthetical, verbatim. Inventing a
    tidier name would break the link back to the source column.
    """
    match = _UNIT_PARENTHETICAL.search(column)
    if match is None:
        return column.strip(), canonical_unit(default_unit)
    account = (column[: match.start()] + column[match.end() :]).strip()
    return account or column.strip(), canonical_unit(match.group("unit"))


def period_of(row: dict[str, Any]) -> str | None:
    """Build the period label from the row's own 民國 year and quarter.

    Returns ``None`` when the row does not state a year, because a figure without a
    period cannot be compared with anything.
    """
    raw_year = str(row.get("年度", "")).strip()
    if not raw_year.isdigit():
        return None
    year = int(raw_year) + ROC_EPOCH
    quarter = str(row.get("季別", "")).strip()
    return f"FY{year}Q{quarter}" if quarter.isdigit() else f"FY{year}"


def load_openapi_rows(
    dataset_id: str,
    rows: Sequence[dict[str, Any]],
    *,
    source_url: str | None = None,
    company_codes: Iterable[str] | None = None,
    industry_schema_by_company: Mapping[str, IndustrySchema] | None = None,
) -> tuple[list[CompanyRow], list[LineItem]]:
    """Turn one dataset's rows into companies and line items.

    ``company_codes`` restricts the load to the study's issuers; without it a
    1,045-row endpoint would put every listed company into the store.

    Raises:
        KeyError: If the dataset is not one this study declared.
    """
    spec = OPENAPI_DATASETS[dataset_id]
    wanted = set(company_codes) if company_codes is not None else None
    known_schemas = industry_schema_by_company or {}

    companies: list[CompanyRow] = []
    items: list[LineItem] = []

    for row in rows:
        code = str(row.get("公司代號", "")).strip()
        if not code or (wanted is not None and code not in wanted):
            continue
        period = period_of(row)
        if period is None:
            continue

        schema = known_schemas.get(code, spec.industry_schema)
        if spec.declares_industry:
            companies.append(
                CompanyRow(
                    code=code,
                    name=str(row.get("公司名稱", "")).strip(),
                    industry_schema=schema,
                )
            )

        for column, raw in row.items():
            if column in _KEY_COLUMNS:
                continue
            value = parse_amount(raw if raw is None else str(raw))
            if value is None:
                continue
            account, unit = split_account_unit(column, spec.default_unit)
            items.append(
                LineItem(
                    company_code=code,
                    period=period,
                    statement=spec.statement,
                    basis=spec.basis,
                    industry_schema=schema,
                    account=account,
                    value=value,
                    unit=unit,
                    currency=None if unit in {"%", "倍"} else spec.currency,
                    source_kind="openapi_current",
                    source_ref=f"{dataset_id}:{code}:{period}:{column}",
                    source_url=source_url,
                )
            )

    return companies, items
