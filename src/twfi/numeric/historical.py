"""Load FY2023/FY2024 figures out of extracted tables, without letting gold decide what counts.

TWSE's OpenAPI carries only the current period (§8 finding 1), so every historical figure the
numeric route needs has to come from a table this repository extracted itself. That is why
``source_kind`` has an ``extracted_table`` member, and why R7 requires the report to say
"verified structured data" rather than "official structured data".

**Gold names the targets and must not judge them.** A gold record's
``structured_source_key.row_key`` is written ``<doc>|p<page>|<row label>|<column label>``, which
is exactly the coordinates a loader needs, so the targets come from there. What must not
follow is filtering on agreement: if a figure enters the store only when it matches the gold
answer, then F4 answers those questions correctly *by construction* and the measured gain is
an artefact of the loader. So this loads whatever the extractor produced -- including a value
that disagrees with gold -- and reports the disagreement as a finding. A wrong figure in the
store is a real property of the pipeline under test, and hiding it would be the circularity
D-016 exists to prevent, arriving from the other direction.

**Coverage is a scope limit, not a result.** The store ends up holding the accounts the gold
set happens to ask about. That is deliberate -- loading every table in 2,895 pages is out of
scope for a feasibility study -- but it means "the numeric route had the figure it needed"
must never be reported as a finding about coverage. It was arranged.

Row labels in filings are not clean keys: 「資產總額」 and 「資產總計」 are the same line under two
issuers' wording, and a label may carry a note reference (「存貨（附註十二）」). Matching is
therefore on a normalised containment basis and every match records the literal cell text it
came from, so a wrong match can be seen rather than inferred.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from twfi.numeric.rows import read_page, resolve_page
from twfi.numeric.store import Basis, IndustrySchema, LineItem, Statement
from twfi.parsing.normalise import normalise
from twfi.parsing.tables import Table

__all__ = [
    "PERIOD_PATTERNS",
    "STATEMENT_BY_ACCOUNT",
    "Target",
    "Loaded",
    "parse_row_key",
    "period_of_column",
    "statement_of",
    "to_decimal",
    "find_in_tables",
    "find_in_text",
    "schema_of",
    "Outcome",
    "outcome_of",
]

#: A row label that is not a line item but a computation the annotator named. These appear in
#: gold because a derived answer still needs a key; they are not table rows and must not be
#: looked for as if they were.
_DERIVED_LABEL = re.compile(r"佔|增減|成長|→")

#: Era-year column headings, most specific first. 「112年12月31日」 and 「112年度」 name the same
#: fiscal year and differ only in whether the figure is a balance or a flow, which
#: ``Statement`` already records -- so both map to the same period.
PERIOD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:民國)?(\d{2,3})\s*年\s*12\s*月\s*31\s*日"), "FY{}"),
    (re.compile(r"(?:民國)?(\d{2,3})\s*年度"), "FY{}"),
    (re.compile(r"(?:民國)?(\d{2,3})\s*年"), "FY{}"),
)

#: Which statement a line belongs to. Absent means absent: an account this does not know is
#: refused rather than filed under a guess, because ``statement`` is part of how the numeric
#: route decides two figures are comparable.
STATEMENT_BY_ACCOUNT: dict[str, Statement] = {
    "資產總額": "balance",
    "資產總計": "balance",
    "負債總額": "balance",
    "負債總計": "balance",
    "權益總額": "balance",
    "權益總計": "balance",
    "流動資產": "balance",
    "非流動資產": "balance",
    "流動負債": "balance",
    "非流動負債": "balance",
    "股本": "balance",
    "資本公積": "balance",
    "保留盈餘": "balance",
    "其他權益": "balance",
    "存貨": "balance",
    "現金及約當現金": "balance",
    "營業收入": "income",
    "營業成本": "income",
    "營業毛利": "income",
    "營業利益": "income",
    "本期淨利": "income",
    "本年度淨利": "income",
    "利息淨收益": "income",
    "每股盈餘": "income",
    "現金流量比率": "ratio",
    "現金流量允當比率": "ratio",
    "現金再投資比率": "ratio",
}

#: 2882 files a 金控 schema; everyone else in this study files the general one.
_FINANCIAL_HOLDING = frozenset({"2882"})


@dataclass(frozen=True, slots=True)
class Target:
    """One figure to look for: where it is, and what it should be called."""

    question_id: str
    doc_id: str
    company_code: str
    page: int
    row_label: str
    column_label: str
    basis: Basis
    #: The gold answer, carried only so a disagreement can be reported. Never a filter.
    gold_answer: str | None = None

    @property
    def account(self) -> str:
        """The row label with any note reference stripped: 存貨（附註十二） -> 存貨."""
        return re.sub(r"[（(].*?[)）]", "", self.row_label).strip()


@dataclass(frozen=True, slots=True)
class Loaded:
    """What the extractor gave for one target, and whether gold agrees."""

    target: Target
    item: LineItem | None
    #: The literal cell text the value came from, so a wrong match is visible.
    cell_text: str = ""
    problem: str = ""

    @property
    def agrees_with_gold(self) -> bool | None:
        """``None`` when there is nothing to compare, not ``False``.

        The distinction matters: "the extractor produced nothing" and "the extractor produced
        the wrong number" are different findings and would be reported as one if a missing
        value collapsed into disagreement.
        """
        if self.item is None or self.item.value is None or self.target.gold_answer is None:
            return None
        expected = to_decimal(self.target.gold_answer)
        if expected is None:
            return None
        return self.item.value == expected


def parse_row_key(row_key: str) -> tuple[str, int, str, str] | None:
    """Split ``<doc>|p<page>|<row>|<column>``, or ``None`` if it is not a four-part key.

    Two-part keys (``<doc>|p<page>``) appear in gold for derived answers whose operands live
    on the page but whose row is not a single line. They name no cell, so there is nothing
    here to load.
    """
    parts = row_key.split("|")
    if len(parts) != 4:
        return None
    doc_id, page_part, row_label, column_label = (part.strip() for part in parts)
    if not page_part.startswith("p") or not page_part[1:].isdigit():
        return None
    return doc_id, int(page_part[1:]), row_label, column_label


def period_of_column(column_label: str) -> str | None:
    """``民國112年度`` -> ``FY2023``. ``None`` when the heading names no single year.

    A heading covering two years (``111年度及112年度``) returns ``None`` rather than the first
    one it can see: a two-year heading means the answer spans columns, and picking one would
    file a figure under a period it may not belong to.
    """
    flat = normalise(column_label)
    if re.search(r"及|至|~|→|、", flat):
        return None
    for pattern, template in PERIOD_PATTERNS:
        match = pattern.search(flat)
        if match:
            era = int(match.group(1))
            return template.format(era + 1911)
    return None


def statement_of(account: str) -> Statement | None:
    """Which statement an account belongs to, or ``None`` if this does not know."""
    return STATEMENT_BY_ACCOUNT.get(normalise(account).strip())


def schema_of(company_code: str) -> IndustrySchema:
    return "financial_holding" if company_code in _FINANCIAL_HOLDING else "general"


def to_decimal(text: str) -> Decimal | None:
    """A filing's figure as a Decimal. Parentheses mean negative; ``None`` if unparseable.

    Decimal rather than float throughout: these are money, and the numeric route compares
    them for equality.
    """
    flat = normalise(text).strip()
    if not flat:
        return None
    # Filings write a negative as (1,181,998) in either width of bracket. The closing bracket
    # is not required: a cell truncated mid-number still meant negative, and reading it as
    # positive would flip the sign of a real figure.
    negative = flat.startswith(("(", "（"))
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", flat)
    if match is None:
        return None
    try:
        value = Decimal(match.group().replace(",", ""))
    except InvalidOperation:
        return None
    if negative and value > 0:
        value = -value
    return value


def _column_index(header: Sequence[str], column_label: str) -> int | None:
    """Which column a heading names, matched on normalised containment either way.

    Either direction, because a gold key writes 「112年12月31日」 while the cell may read
    「112 年 12 月 31 日」 or 「112年12月31日金額」 -- one is inside the other depending on which
    is more verbose, and demanding equality found nothing on real headers.
    """
    wanted = re.sub(r"\s+", "", normalise(column_label))
    if not wanted:
        return None
    best: tuple[int, int] | None = None
    for index, cell in enumerate(header):
        flat = re.sub(r"\s+", "", normalise(cell))
        if not flat:
            continue
        if wanted in flat or flat in wanted:
            # Prefer the longest matching header: a bare 「112年」 matches several columns of a
            # balance sheet, and the most specific heading is the one that meant it.
            score = len(flat)
            if best is None or score > best[1]:
                best = (index, score)
    return best[0] if best is not None else None


def find_in_tables(
    target: Target,
    tables: Iterable[Table],
    *,
    unit_default: str | None = None,
    currency_default: str | None = None,
) -> Loaded:
    """Look for one target in the tables on its page and build a line item from it.

    Returns a :class:`Loaded` carrying a problem string rather than raising, because a
    corpus-wide load must report every failure at the end rather than stopping at the first
    account it cannot find.
    """
    statement = statement_of(target.account)
    if statement is None:
        return Loaded(target, None, problem=f"no statement known for account {target.account!r}")
    period = period_of_column(target.column_label)
    if period is None:
        return Loaded(
            target,
            None,
            problem=(
                f"column {target.column_label!r} names no single fiscal year, so this key "
                "spans columns and there is no one cell to load"
            ),
        )

    wanted_row = re.sub(r"\s+", "", normalise(target.account))
    unmatched_column = False
    for table in tables:
        if table.page != target.page or not table.rows:
            continue
        header = table.rows[0]
        column = _column_index(header, target.column_label)
        for row in table.rows[1:]:
            if not row:
                continue
            label = re.sub(r"\s+", "", normalise(row[0]))
            if not label or wanted_row not in label:
                continue
            # The column must be identified. The first version fell back to "the first
            # parseable number in the matching row", and on 2330-FY2024-FS p41 that loaded 88
            # -- a note reference -- as 存貨, against a gold answer of 287,868,810. A figure
            # taken from a column nobody identified is worse than no figure: it is wrong in a
            # way that looks like data. The gold comparison caught it, which is what that
            # comparison is for, but the loader should not have needed catching.
            if column is None or column >= len(row):
                unmatched_column = True
                continue
            cell = row[column]
            value = to_decimal(cell)
            if value is not None:
                return Loaded(
                    target,
                    LineItem(
                        company_code=target.company_code,
                        period=period,
                        statement=statement,
                        basis=target.basis,
                        industry_schema=schema_of(target.company_code),
                        account=target.account,
                        value=value,
                        unit=table.unit or unit_default,
                        currency=table.currency or currency_default,
                        source_kind="extracted_table",
                        source_ref=f"{target.doc_id}|p{target.page}|{target.row_label}"
                        f"|{target.column_label}",
                        unit_is_uniform=table.units.exception is None,
                        unit_note=table.units.exception,
                    ),
                    cell_text=cell,
                )
    if unmatched_column:
        # Distinguished from "no such row" because they call for different work: this one
        # found the account and could not identify the period column, which is a header the
        # matcher does not understand rather than a table it cannot see.
        return Loaded(
            target,
            None,
            problem=(
                f"{target.doc_id} p{target.page} has a row for {target.account!r} but no header "
                f"identifiable as {target.column_label!r}; refusing to guess which column"
            ),
        )
    return Loaded(
        target,
        None,
        problem=f"no table on {target.doc_id} p{target.page} has a row matching "
        f"{target.account!r} with a numeric cell",
    )


def find_in_text(
    target: Target,
    page_text: str,
    *,
    unit_default: str | None = None,
    currency_default: str | None = None,
) -> Loaded:
    """Look for one target in the page's *line stream* instead of its grid.

    The fallback for the structures :func:`find_in_tables` cannot see: notes with no ruling
    lines, labels and figures on separate lines, and several notes merged into one table object.
    :mod:`twfi.numeric.rows` explains how a page is read; this function is the part that turns a
    resolved cell into a :class:`LineItem` and records which route found it.

    The route travels in ``source_ref``. A ``breakdown_total`` figure was not printed on a row
    bearing the account's name -- it is the unlabelled total closing the breakdown that name
    heads -- and a reader checking the store against the filing needs to know that is what they
    are looking for.
    """
    statement = statement_of(target.account)
    if statement is None:
        return Loaded(target, None, problem=f"no statement known for account {target.account!r}")
    period = period_of_column(target.column_label)
    if period is None:
        return Loaded(
            target,
            None,
            problem=(
                f"column {target.column_label!r} names no single fiscal year, so this key "
                "spans columns and there is no one cell to load"
            ),
        )

    tables = read_page(normalise(page_text))
    if not tables:
        return Loaded(
            target,
            None,
            problem=f"{target.doc_id} p{target.page} has no period header, so no column on it "
            "can be identified",
        )
    resolved = resolve_page(tables, target.row_label, target.column_label)
    if resolved is not None:
        cell, route = resolved
        value = to_decimal(cell)
        if value is None:
            return Loaded(
                target,
                None,
                problem=f"{target.doc_id} p{target.page} row {target.row_label!r} resolved to "
                f"{cell!r}, which is not a number",
            )
        return Loaded(
            target,
            LineItem(
                company_code=target.company_code,
                period=period,
                statement=statement,
                basis=target.basis,
                industry_schema=schema_of(target.company_code),
                account=target.account,
                value=value,
                unit=unit_default,
                currency=currency_default,
                source_kind="extracted_text_row",
                source_ref=f"{target.doc_id}|p{target.page}|{target.row_label}"
                f"|{target.column_label}|{route}",
            ),
            cell_text=cell,
        )
    return Loaded(
        target,
        None,
        problem=f"no text row on {target.doc_id} p{target.page} matches {target.account!r} "
        f"under a column identifiable as {target.column_label!r}",
    )


Outcome = Literal["loaded", "missing", "disagrees"]


def outcome_of(loaded: Loaded) -> Outcome:
    """Three states, kept apart on purpose.

    ``missing`` and ``disagrees`` are different findings about the extractor and collapsing
    them would hide which one happened.
    """
    if loaded.item is None:
        return "missing"
    return "disagrees" if loaded.agrees_with_gold is False else "loaded"
