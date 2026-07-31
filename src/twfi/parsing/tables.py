"""Table extraction, and the two things that make a financial table answerable.

A cell value on its own is not an answer. ``2,894,308`` means nothing until you know
it is 新台幣千元 rather than 百萬元, and a table that continues onto the next page is
only usable if the continuation is linked to its header. Those are the two failures
the protocol scores separately as unit accuracy and cross-page evidence, so they are
handled here rather than left to the answering stage.

Measured decisions, not preferences:

* **pdfplumber, not PyMuPDF.** ``page.find_tables()`` returned 0 tables on 20 pages
  of a real 財務報告書; these filings use whitespace-aligned tables with few ruling
  lines. pdfplumber found 25 on the same pages. D-002 named pdfplumber and that was
  right.
* **The ``text`` strategy, not ``lines``.** On pages 100-115 of 2330-FY2023, ``lines``
  produced 12 degenerate tables totalling 24 cells; ``text`` produced 14 tables with
  2,783 cells, 1,128 of them numeric -- and ran twice as fast. On 2882-FY2024-FS,
  ``lines`` found nothing at all.
* **Acceptance thresholds are therefore mandatory.** The ``text`` strategy will
  happily read a table out of aligned prose, so a candidate must have real shape:
  two dimensions, some numbers, and enough filled cells.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

import pdfplumber

from twfi.errors import ParsingError
from twfi.parsing.normalise import normalise
from twfi.parsing.types import BBox, Block

__all__ = [
    "TableConfig",
    "UnitSpec",
    "Table",
    "detect_unit",
    "document_unit",
    "inherit_units",
    "is_table_like",
    "link_continuations",
    "extract_tables",
    "tables_to_blocks",
]

_CURRENCY_WORDS = "新台幣|新臺幣|美元|美金|人民幣|港幣|日圓|歐元"
_UNIT_WORDS = "仟元|千元|百萬元|十億元|億元|萬元|元|仟股|千股|股"

#: Filings state the unit in at least two registers, both observed in this corpus:
#:   2882-FY2024-FS p9   單位：新台幣仟元
#:   2330-FY2024-FS p16  （除另予註明者外，金額為新台幣仟元）
#: Matching only the first form found the unit on none of the sampled tables.
_UNIT_PATTERN = re.compile(
    r"(?:單位\s*[：:]\s*|金額(?:均)?為\s*|以\s*)"
    rf"(?P<currency>{_CURRENCY_WORDS})?\s*"
    rf"(?P<unit>{_UNIT_WORDS})"
)

#: The trap this exists for. One label, two units: applying 千元 to earnings per share
#: is wrong by a factor of a thousand, and wrong in a way that looks plausible.
#:
#: Two issuers write it two ways, and the first version of this pattern only read one::
#:
#:     2882-FY2024-FS p10   單位：新台幣仟元，惟每股盈餘為元
#:     2317-FY2024-FS p14   單位：新台幣仟元(除每股盈餘為新台幣元外)
#:
#: The second returned no exception at all, so the spec reported itself uniform and the
#: numeric route would have applied 千元 to 鴻海's EPS with full confidence. Matching
#: only the phrasing the first sampled filing happened to use is how a unit check passes
#: while being wrong.
#:
#: 除另予註明者外 does not match: ``what`` cannot span a comma, so there is no ``為``
#: for it to reach.
_UNIT_EXCEPTION = re.compile(
    rf"(?:惟|除)\s*(?P<what>[^，。、）)]{{1,24}}?)\s*為\s*"
    rf"(?:{_CURRENCY_WORDS})?\s*(?P<unit>{_UNIT_WORDS})"
)

#: 「除另予註明者外」 -- "unless otherwise noted". The unit holds by default but the
#: document reserves the right to override it per line.
_UNIT_QUALIFIER = re.compile(r"除另(?:予|行)?(?:註|注)明者?外")

#: Currency words to ISO codes. Absent means absent: the numeric route refuses to
#: compare figures whose currency was never stated rather than assuming TWD.
_CURRENCY_CODES = {
    "新台幣": "TWD",
    "新臺幣": "TWD",
    "美元": "USD",
    "美金": "USD",
    "人民幣": "CNY",
    "港幣": "HKD",
    "日圓": "JPY",
    "歐元": "EUR",
}

#: Canonical unit spellings. 仟 and 千 are the same unit written two ways, and a study
#: that treated them as different would report unit errors that are not errors.
_UNIT_CANONICAL = {"仟元": "千元", "仟股": "千股"}

_DIGIT = re.compile(r"\d")


@dataclass(frozen=True, slots=True)
class TableConfig:
    """Extraction settings and acceptance thresholds.

    Tunable on the development split only (protocol 1.3).
    """

    strategy: Literal["text", "lines"] = "text"
    min_rows: int = 2
    min_cols: int = 2
    min_numeric_cells: int = 1
    min_fill_ratio: float = 0.3
    #: How far back in the page text to look for a units row.
    unit_lookback_chars: int = 400
    #: A continuation starts in the top of the page and its predecessor ends near the
    #: bottom, expressed as fractions of page height.
    continuation_top_ratio: float = 0.25
    continuation_bottom_ratio: float = 0.6

    def __post_init__(self) -> None:
        if self.min_rows < 1 or self.min_cols < 1:
            raise ValueError("a table needs at least one row and one column")
        if not 0.0 <= self.min_fill_ratio <= 1.0:
            raise ValueError("min_fill_ratio must be in [0, 1]")

    def plumber_settings(self) -> dict[str, str]:
        return {
            "vertical_strategy": self.strategy,
            "horizontal_strategy": self.strategy,
        }


@dataclass(frozen=True, slots=True)
class UnitSpec:
    """What a filing says about the scale of its figures, including the caveats."""

    unit: str | None = None
    currency: str | None = None
    #: e.g. ``每股盈餘為元`` -- a line item that does not follow the stated unit.
    exception: str | None = None
    #: True when the filing said 除另予註明者外, reserving the right to override.
    qualified: bool = False
    #: Where the unit was declared. ``document`` means it was inherited from a
    #: declaration governing the whole notes section rather than stated at the table.
    scope: Literal["table", "document"] = "table"
    #: The page carrying the declaration, so an answer can cite what makes it readable.
    declared_on_page: int | None = None

    @property
    def is_stated(self) -> bool:
        return self.unit is not None

    @property
    def is_inherited(self) -> bool:
        """True when nothing near the table said this; a note 39 pages back did.

        Kept distinct from a table-local declaration because the two do not deserve
        equal confidence: an inherited unit is right only if the table is really inside
        the declaration's scope, which is an inference, not a reading.
        """
        return self.scope == "document"

    @property
    def is_uniform(self) -> bool:
        """False when some figures in the table do not use the stated unit.

        The numeric route must not apply a non-uniform unit blindly; it either resolves
        the exception or refuses.
        """
        return self.is_stated and self.exception is None and not self.qualified

    def describe(self) -> str:
        if not self.is_stated:
            return ""
        parts = [f"單位：{self.currency or ''}{self.unit or ''}".strip()]
        if self.qualified:
            parts.append("（除另予註明者外）")
        if self.exception:
            parts.append(f"（例外：{self.exception}）")
        if self.is_inherited and self.declared_on_page is not None:
            parts.append(f"（承第 {self.declared_on_page} 頁之宣告）")
        return "".join(parts)


@dataclass(frozen=True, slots=True)
class Table:
    """One extracted table, with the context needed to read its figures."""

    page: int
    bbox: BBox
    rows: tuple[tuple[str, ...], ...]
    units: UnitSpec = field(default_factory=UnitSpec)
    continues_from_page: int | None = None

    @property
    def unit(self) -> str | None:
        return self.units.unit

    @property
    def currency(self) -> str | None:
        return self.units.currency

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    @property
    def cells(self) -> int:
        return sum(1 for row in self.rows for cell in row if cell.strip())

    @property
    def numeric_cells(self) -> int:
        return sum(1 for row in self.rows for cell in row if _DIGIT.search(cell))

    @property
    def fill_ratio(self) -> float:
        total = self.n_rows * self.n_cols
        return self.cells / total if total else 0.0

    @property
    def is_continuation(self) -> bool:
        return self.continues_from_page is not None

    def cell(self, row: int, col: int) -> str:
        """Return one cell, or ``""`` if the coordinates fall outside the table."""
        if 0 <= row < self.n_rows and 0 <= col < len(self.rows[row]):
            return self.rows[row][col]
        return ""

    def cell_ref(self, row: int, col: int) -> str:
        """A citable coordinate for one cell, e.g. ``p102:r3:c1``."""
        return f"p{self.page}:r{row}:c{col}"

    def to_text(self) -> str:
        """A flat rendering for indexing, with the unit stated up front.

        The unit leads because a retrieved table chunk that does not say 千元 invites
        exactly the unit error the protocol scores.
        """
        header = []
        described = self.units.describe()
        if described:
            header.append(described)
        if self.is_continuation:
            header.append(f"（接續第 {self.continues_from_page} 頁）")
        body = "\n".join(" | ".join(cell for cell in row) for row in self.rows)
        return "\n".join([*header, body]) if header else body


def detect_unit(text: str) -> UnitSpec:
    """Read the unit, currency, and any exception to them out of surrounding text.

    An unstated unit is reported as unstated, never defaulted: assuming 千元 would turn
    an unanswerable question into a confidently wrong answer.
    """
    match = _UNIT_PATTERN.search(text)
    if match is None:
        return UnitSpec()

    raw_unit = match.group("unit")
    unit = _UNIT_CANONICAL.get(raw_unit, raw_unit)
    currency = _CURRENCY_CODES.get(match.group("currency") or "")

    exception: str | None = None
    tail = text[match.end() : match.end() + 60]
    exception_match = _UNIT_EXCEPTION.search(tail)
    if exception_match:
        exception_unit = _UNIT_CANONICAL.get(
            exception_match.group("unit"), exception_match.group("unit")
        )
        exception = f"{exception_match.group('what')}為{exception_unit}"

    window_start = max(0, match.start() - 40)
    qualified = _UNIT_QUALIFIER.search(text[window_start : match.end()]) is not None

    return UnitSpec(unit=unit, currency=currency, exception=exception, qualified=qualified)


def document_unit(page_texts: Sequence[str], *, marker: str = "附註") -> UnitSpec:
    """The scale a filing declares once, for its whole notes section.

    Taiwanese financial reports state the scale in the notes header and then omit it
    from every note table that follows::

        合併財務報告附註 民國113及112年度（除另予註明者外，金額為新台幣仟元）

    ``2330-FY2024-FS`` declares it on page 16 and prints the FY2024 revenue table on
    page 55, thirty-nine pages later. Looking only near the table found nothing there,
    so 62 of that filing's 65 tables were recorded as having no stated unit and became
    unusable -- not because the document is silent, but because the window was too
    narrow. D-018.

    The qualifier is the signal. 除另予註明者外 means "unless otherwise noted", which is
    a claim about a whole section; a bare 單位：新台幣仟元 above a table is a claim about
    that table. So only a qualified declaration, on a page that also names 附註, is
    treated as governing the document.

    Returns an unstated spec when no such declaration exists. Nothing is assumed.
    """
    for index, text in enumerate(page_texts, start=1):
        if marker not in text or not _UNIT_QUALIFIER.search(text):
            continue
        spec = detect_unit(text)
        if spec.is_stated and spec.qualified:
            return replace(spec, scope="document", declared_on_page=index)
    return UnitSpec()


def inherit_units(tables: tuple[Table, ...], default: UnitSpec) -> tuple[Table, ...]:
    """Give tables with no unit of their own the document's declaration.

    Only forwards. A declaration governs what follows it, so a table printed before the
    notes header keeps its unstated unit rather than borrowing a scale that had not been
    announced yet.
    """
    if not default.is_stated or default.declared_on_page is None:
        return tables
    return tuple(
        replace(table, units=default)
        if not table.units.is_stated and table.page >= default.declared_on_page
        else table
        for table in tables
    )


def is_table_like(rows: tuple[tuple[str, ...], ...], config: TableConfig) -> bool:
    """Whether a candidate has the shape of a real table.

    The ``text`` strategy reads a grid out of any aligned text, so this is what keeps
    a page of justified prose from becoming a hundred spurious tables.
    """
    if len(rows) < config.min_rows:
        return False
    n_cols = max((len(row) for row in rows), default=0)
    if n_cols < config.min_cols:
        return False
    filled = sum(1 for row in rows for cell in row if cell.strip())
    if filled < config.min_rows * config.min_cols:
        return False
    if filled / (len(rows) * n_cols) < config.min_fill_ratio:
        return False
    numeric = sum(1 for row in rows for cell in row if _DIGIT.search(cell))
    return numeric >= config.min_numeric_cells


def link_continuations(
    tables: tuple[Table, ...],
    page_heights: dict[int, float],
    config: TableConfig | None = None,
) -> tuple[Table, ...]:
    """Mark tables that continue a table from the previous page.

    A continuation is recognised geometrically -- it starts high on its page and its
    predecessor ended low on the one before -- and structurally, by matching column
    count. It also inherits the unit, because the units row appears once, above the
    first part.
    """
    config = config or TableConfig()
    by_page: dict[int, list[Table]] = {}
    for table in tables:
        by_page.setdefault(table.page, []).append(table)

    linked: list[Table] = []
    for table in tables:
        previous_page = table.page - 1
        height = page_heights.get(table.page, 0.0)
        starts_high = height > 0 and table.bbox.y0 <= height * config.continuation_top_ratio

        predecessor = None
        if starts_high and previous_page in by_page:
            previous_height = page_heights.get(previous_page, 0.0)
            for candidate in by_page[previous_page]:
                ends_low = (
                    previous_height > 0
                    and candidate.bbox.y1 >= previous_height * config.continuation_bottom_ratio
                )
                if ends_low and candidate.n_cols == table.n_cols:
                    predecessor = candidate
                    break

        if predecessor is None:
            linked.append(table)
            continue

        linked.append(
            Table(
                page=table.page,
                bbox=table.bbox,
                rows=table.rows,
                units=table.units if table.units.is_stated else predecessor.units,
                continues_from_page=predecessor.page,
            )
        )
    return tuple(linked)


def extract_tables(
    pdf_path: Path, config: TableConfig | None = None, *, pages: range | None = None
) -> tuple[Table, ...]:
    """Extract every accepted table from a PDF, with units and continuations resolved.

    Raises:
        ParsingError: If the file cannot be opened as a PDF.
    """
    config = config or TableConfig()
    found: list[Table] = []
    page_heights: dict[int, float] = {}
    page_texts: list[str] = []

    try:
        document = pdfplumber.open(pdf_path)
    except Exception as exc:
        raise ParsingError(f"cannot open {pdf_path} for table extraction: {exc}") from exc

    with document:
        indices = pages if pages is not None else range(len(document.pages))
        for index in indices:
            if index >= len(document.pages):
                break
            page = document.pages[index]
            number = index + 1
            page_heights[number] = float(page.height)
            # Normalised like the two text parsers: the unit-declaration regexes match
            # Chinese, and 除另予註明者外 set as compatibility characters would not fire.
            page_text = normalise(page.extract_text() or "")
            # Keep every page's text, including pages holding no table: the declaration
            # that governs a note table usually sits on a page of prose (D-018).
            while len(page_texts) < number - 1:
                page_texts.append("")
            page_texts.append(page_text)

            for candidate in page.find_tables(config.plumber_settings()):
                rows = tuple(
                    tuple(normalise(cell or "").strip() for cell in row)
                    for row in candidate.extract()
                )
                if not is_table_like(rows, config):
                    continue
                bbox = BBox.from_tuple(
                    (
                        float(candidate.bbox[0]),
                        float(candidate.bbox[1]),
                        float(candidate.bbox[2]),
                        float(candidate.bbox[3]),
                    )
                )
                # The units row sits above the table, so look back through the page
                # text rather than inside the grid.
                lookback = page_text[: config.unit_lookback_chars]
                units = detect_unit(lookback)
                if not units.is_stated:
                    units = detect_unit(page_text)
                found.append(Table(page=number, bbox=bbox, rows=rows, units=units))

    inherited = inherit_units(tuple(found), document_unit(page_texts))
    return link_continuations(inherited, page_heights, config)


def tables_to_blocks(tables: tuple[Table, ...]) -> tuple[Block, ...]:
    """Render tables as atomic blocks the chunker will never split."""
    return tuple(
        Block(
            page=table.page,
            kind="table",
            text=table.to_text(),
            bbox=table.bbox,
            order=index,
        )
        for index, table in enumerate(tables)
    )
