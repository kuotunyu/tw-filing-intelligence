"""The DuckDB store behind the numeric route.

Two properties this file exists to guarantee:

* **Nothing is stored without provenance.** :class:`LineItem` requires
  ``source_kind`` and ``source_ref``, so a figure that cannot be cited cannot be
  inserted.
* **Lookups do not cross the industry boundary.** A financial holding company files
  a different statement structure -- 2882 has no 營業收入 row at all -- so asking for
  revenue on a 金控 raises :class:`~twfi.errors.NumericRouteError` naming the accounts
  that do exist, instead of returning nothing and letting the caller guess.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

import duckdb

from twfi.errors import NumericRouteError
from twfi.numeric.amounts import canonical_unit

__all__ = [
    "IndustrySchema",
    "SourceKind",
    "Statement",
    "Basis",
    "CompanyRow",
    "LineItem",
    "NumericStore",
]

IndustrySchema = Literal["general", "financial_holding"]
#: ``extracted_text_row`` is kept apart from ``extracted_table`` because the two are different
#: claims about the page. A table cell was located by grid geometry; a text row was located by
#: reading the page's line stream and deciding which lines belong to one line item
#: (:mod:`twfi.numeric.rows`). The second recovers figures the first cannot see on filings whose
#: notes carry no ruling lines, and it rests on more inference -- so "how much of the store came
#: from where" has to be answerable, and ``source_kind`` is in the primary key so both survive.
SourceKind = Literal["xbrl", "openapi_current", "extracted_table", "extracted_text_row"]
Statement = Literal["income", "balance", "ratio", "monthly_revenue"]
Basis = Literal["consolidated", "parent_only"]

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass(frozen=True, slots=True)
class CompanyRow:
    """A company and the statement vocabulary it files under."""

    code: str
    name: str
    industry_schema: IndustrySchema


@dataclass(frozen=True, slots=True)
class LineItem:
    """One figure, with everything needed to compare it and to cite it."""

    company_code: str
    period: str
    statement: Statement
    basis: Basis
    industry_schema: IndustrySchema
    account: str
    value: Decimal | None
    unit: str | None
    currency: str | None
    source_kind: SourceKind
    source_ref: str
    source_url: str | None = None
    unit_is_uniform: bool = True
    unit_note: str | None = None

    def __post_init__(self) -> None:
        if not self.source_ref:
            raise ValueError("a stored figure must say where it came from")

    @property
    def is_usable(self) -> bool:
        """Whether this figure can be used in a calculation without further work."""
        return self.value is not None and self.unit is not None and self.unit_is_uniform

    def citation(self) -> str:
        """A compact provenance string for the answer contract."""
        location = f"{self.source_kind}:{self.source_ref}"
        return f"{self.company_code} {self.period} {self.account} [{location}]"


class NumericStore:
    """A DuckDB database of filing figures.

    Defaults to in-memory, which is what tests use; pass a path to persist an index
    build. Usable as a context manager.
    """

    def __init__(self, database: Path | str = ":memory:") -> None:
        self._connection = duckdb.connect(str(database))
        self._connection.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    # ------------------------------------------------------------------ writing

    def add_companies(self, companies: Iterable[CompanyRow]) -> int:
        rows = [(item.code, item.name, item.industry_schema) for item in companies]
        if rows:
            self._connection.executemany("INSERT OR REPLACE INTO company VALUES (?, ?, ?)", rows)
        return len(rows)

    def add_line_items(self, items: Iterable[LineItem]) -> int:
        """Insert figures, replacing any with the same key from the same source.

        Two sources may legitimately disagree about the same account and period -- that
        is what the cross-document questions are about -- so the primary key includes
        ``source_kind`` and both rows survive.
        """
        rows = [
            (
                item.company_code,
                item.period,
                item.statement,
                item.basis,
                item.industry_schema,
                item.account,
                item.value,
                item.unit,
                item.currency,
                item.unit_is_uniform,
                item.unit_note,
                item.source_kind,
                item.source_url,
                item.source_ref,
            )
            for item in items
        ]
        if rows:
            self._connection.executemany(
                "INSERT OR REPLACE INTO fin_line_item VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def record_source(
        self,
        source_kind: SourceKind,
        source_id: str,
        *,
        loaded_at: str,
        rows_loaded: int,
        source_url: str | None = None,
        sha256: str | None = None,
    ) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO numeric_source VALUES (?, ?, ?, ?, ?, ?)",
            (source_kind, source_id, source_url, sha256, loaded_at, rows_loaded),
        )

    # ------------------------------------------------------------------ reading

    def industry_schema_of(self, company_code: str) -> IndustrySchema:
        """Return which statement vocabulary a company files under.

        Raises:
            NumericRouteError: If the company is not loaded.
        """
        row = self._connection.execute(
            "SELECT industry_schema FROM company WHERE code = ?", (company_code,)
        ).fetchone()
        if row is None:
            raise NumericRouteError(f"company {company_code} is not in the numeric store")
        return str(row[0])  # type: ignore[return-value]

    def accounts_for(self, company_code: str, *, statement: str | None = None) -> list[str]:
        """Every account this company actually files, for diagnostics and refusals."""
        sql = "SELECT DISTINCT account FROM fin_line_item WHERE company_code = ?"
        params: list[Any] = [company_code]
        if statement is not None:
            sql += " AND statement = ?"
            params.append(statement)
        return sorted(str(row[0]) for row in self._connection.execute(sql, params).fetchall())

    def periods_for(self, company_code: str, account: str) -> list[str]:
        rows = self._connection.execute(
            "SELECT DISTINCT period FROM fin_line_item WHERE company_code = ? AND account = ?",
            (company_code, account),
        ).fetchall()
        return sorted(str(row[0]) for row in rows)

    def find(
        self,
        company_code: str,
        account: str,
        period: str,
        *,
        basis: Basis = "consolidated",
        statement: str | None = None,
        source_kind: SourceKind | None = None,
    ) -> list[LineItem]:
        """Every stored figure matching the key, across sources.

        Returns a list rather than one row on purpose: when two sources disagree, the
        caller must see both. Silently preferring one is how a conflict becomes a
        confident wrong answer.
        """
        sql = (
            "SELECT company_code, period, statement, basis, industry_schema, account, "
            "value, unit, currency, unit_is_uniform, unit_note, source_kind, source_url, "
            "source_ref FROM fin_line_item "
            "WHERE company_code = ? AND account = ? AND period = ? AND basis = ?"
        )
        params: list[Any] = [company_code, account, period, basis]
        if statement is not None:
            sql += " AND statement = ?"
            params.append(statement)
        if source_kind is not None:
            sql += " AND source_kind = ?"
            params.append(source_kind)
        sql += " ORDER BY source_kind"
        return [_row_to_item(row) for row in self._connection.execute(sql, params).fetchall()]

    def require(
        self,
        company_code: str,
        account: str,
        period: str,
        *,
        basis: Basis = "consolidated",
        statement: str | None = None,
        source_kind: SourceKind | None = None,
    ) -> LineItem:
        """Return exactly one figure, or explain why that is not possible.

        Raises:
            NumericRouteError: When the account does not exist for this company, when
                the period is missing, or when sources disagree. Each message names
                what *is* available, because "I could not answer" is only useful if it
                says what would have been answerable.
        """
        found = self.find(
            company_code,
            account,
            period,
            basis=basis,
            statement=statement,
            source_kind=source_kind,
        )
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            # Name what actually differs. "two sources disagree" is useless to a caller
            # who cannot see which statement or scale each one came from -- and the
            # realistic case is exactly that: 營業收入 appears in the income statement
            # in 千元 and in the 營益分析 aggregate in 百萬元.
            candidates = "; ".join(
                f"{item.statement}/{item.unit or 'unit unstated'}"
                f"={item.value} ({item.source_kind}:{item.source_ref})"
                for item in found
            )
            raise NumericRouteError(
                f"{company_code} {period} {account}: {len(found)} candidates, and the store "
                f"will not choose between them -- {candidates}. Narrow the query with "
                "statement= or source_kind=, or use the cross-source template to report "
                "the disagreement."
            )

        available_accounts = self.accounts_for(company_code, statement=statement)
        if account not in available_accounts:
            schema = self.industry_schema_of(company_code)
            hint = ""
            if schema == "financial_holding":
                hint = (
                    " -- a financial holding company files a different statement "
                    "structure and has no such line"
                )
            raise NumericRouteError(
                f"{company_code} does not file an account named {account!r}{hint}; "
                f"it files {len(available_accounts)} accounts including "
                f"{available_accounts[:5]}"
            )
        periods = self.periods_for(company_code, account)
        raise NumericRouteError(
            f"{company_code} files {account!r} but not for period {period!r}; "
            f"available periods: {periods}"
        )

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM fin_line_item").fetchone()
        return int(row[0]) if row else 0

    def sources(self) -> list[tuple[str, str, int]]:
        rows = self._connection.execute(
            "SELECT source_kind, source_id, rows_loaded FROM numeric_source "
            "ORDER BY source_kind, source_id"
        ).fetchall()
        return [(str(row[0]), str(row[1]), int(row[2])) for row in rows]


def _row_to_item(row: Sequence[Any]) -> LineItem:
    return LineItem(
        company_code=str(row[0]),
        period=str(row[1]),
        statement=str(row[2]),  # type: ignore[arg-type]
        basis=str(row[3]),  # type: ignore[arg-type]
        industry_schema=str(row[4]),  # type: ignore[arg-type]
        account=str(row[5]),
        value=None if row[6] is None else Decimal(str(row[6])),
        unit=canonical_unit(row[7]),
        currency=None if row[8] is None else str(row[8]),
        unit_is_uniform=bool(row[9]),
        unit_note=None if row[10] is None else str(row[10]),
        source_kind=str(row[11]),  # type: ignore[arg-type]
        source_url=None if row[12] is None else str(row[12]),
        source_ref=str(row[13]),
    )
