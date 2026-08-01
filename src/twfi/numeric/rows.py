"""Read a filing page's tables out of its *text stream* rather than out of grid geometry.

The grid route (:mod:`twfi.parsing.tables`) recovers a page's cells when ruling lines or
consistent column gaps exist. On the locked financial reports it frequently does not, and the
failure is structural rather than a matter of tuning:

* **Several notes land in one table object.** `2330-FY2024-FS` p41 carries the inventory note,
  a paragraph of prose, a second small table, and the whole equity-method note as a single
  pdfplumber table, so the first note's row labels become the merged object's header.
* **The label and its figures sit on different rows.** PyMuPDF emits one cell per line, so the
  page reads `存 貨` then `35,177,009` then `34,511,032` -- a layout no grid reconstructs
  without knowing which lines belong together.
* **A total row often has no label at all.** On p41 the inventory total 287,868,810 follows the
  last sub-item with nothing but a rule above it, so its figures join the preceding label's row.

This module reads the page the way a person does, in three rules:

1. **A period header starts a table.** A line naming periods (`113年12月31日`, `112年度`) and
   carrying no figure is a header; the periods give both the column order and the column count.
   A second header on the same page therefore *splits* the page into two tables -- which is what
   makes the merged-note case tractable at all.
2. **A row is a label plus the figures that follow it.** Figures on the label's own line count,
   and so do figures on following lines that carry no label of their own.
3. **A row wider than the table is several rows.** A run of ``k * width`` figures under one
   label is ``k`` rows, of which only the first is labelled; the rest are the unlabelled
   continuation and total rows that filings leave bare.

What this module deliberately does *not* do is decide whether a figure is the right one. It
returns rows and the route it took to find them; :mod:`twfi.numeric.historical` compares against
gold and reports agreement. That split is the same one the rest of the numeric path keeps:
agreement with gold is measured, never a condition for loading.

Why a figure must carry a thousands separator or a decimal point, and a bare integer is neither a
figure nor a label: these filings observe the convention strictly. Money is always grouped
(`33,823,884`), so anything ungrouped is one of the things printed *beside* the money -- the
statement code column (`1100`), the ％ column (`6`), a note reference, or the digits inside
`113年12月31日`. Reading those as figures fills rows with junk; reading them as labels is worse,
because on `2412-FY2023-AR` p137 the ％ column's `6` then became the label of a row holding the
previous account's prior-year figure. An earlier version of the numeric loader took "the first
numeric cell in the row" and loaded ``88`` against a gold answer of 287,868,810.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from twfi.parsing.normalise import normalise

__all__ = [
    "PageTable",
    "Row",
    "column_index",
    "matches_label",
    "read_page",
    "resolve_page",
    "resolve_row",
]

#: A money figure or a ratio: comma-grouped, or a decimal. Nothing else -- see the module note.
FIGURE = re.compile(r"[(（]?-?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+)[)）]?")

#: A row label names something. Statement codes (`1100`), percentages (`6`) and note references
#: printed alone on a line do not, and treating them as labels is what corrupts a table: on
#: `2412-FY2023-AR` p137 the ％ column's `6` became the label of a row holding the *previous*
#: account's prior-year figure. A label must carry at least one CJK or Latin character.
_NAME = re.compile(r"[㐀-鿿豈-﫿A-Za-z]")

#: A period heading: 「113年12月31日」, 「民國112年度」, 「111年」.
PERIOD = re.compile(r"(?:民國)?\d{2,3}\s*年(?:\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\s*度)?")

#: Text a header line may carry besides its period labels without ceasing to be a header.
_HEADER_NOISE = re.compile(r"[金額佔比%％\s（）()、及與元仟千百萬單位：:$＄]")


@dataclass(frozen=True, slots=True)
class Row:
    """One line item: a label and the figures under each period column."""

    label: str
    figures: tuple[str, ...]
    #: ``False`` for a continuation or total row that the filing left unlabelled.
    labelled: bool = True


@dataclass(frozen=True, slots=True)
class PageTable:
    """One table on a page: its period columns in order, its rows, and how wide a row is.

    ``width`` is not always ``len(periods)`` -- see :func:`_table_width`. The period columns are
    the first ``len(periods)`` of the ``width``; any remainder is derived columns this route does
    not address.
    """

    periods: tuple[str, ...]
    rows: tuple[Row, ...]
    width: int


def _flatten(line: str) -> str:
    return re.sub(r"[\s$＄]+", "", normalise(line))


def _is_header(flat: str) -> bool:
    """A line naming at least one period and no figure, once period text is removed.

    The order matters: `113年12月31日` contains digits that a figure pattern must not claim, so
    the periods come out first and the remainder is what decides whether anything else is on
    the line. `單位：新台幣仟元` alone is not a header -- it names no period.
    """
    if not PERIOD.search(flat):
        return False
    remainder = _HEADER_NOISE.sub("", PERIOD.sub("", flat))
    return not remainder


def split_line(flat: str) -> tuple[str, tuple[str, ...]]:
    """Leading label text and every figure on one already-flattened line."""
    figures = tuple(match.group() for match in FIGURE.finditer(flat))
    first = FIGURE.search(flat)
    label = flat[: first.start()] if first else flat
    return label, figures


def _table_width(rows: list[Row], periods: int) -> int:
    """How many figures a row of this table holds, which is not always one per period.

    A filing's comparison table often carries the periods *and* derived columns: `1301-FY2023-AR`
    p188 has 112年度, 111年度, 差異金額 and 差異％, but only the first two appear in the period
    header -- 差異／金額／％ are printed as their own lines and name no period. Taking the width
    from the header alone would call that table 2 wide and fold every row into two, inventing an
    unlabelled row for what is really the same line item's difference columns.

    So the width is the commonest run length among labelled rows, provided it is a whole multiple
    of the period count; otherwise the period count. The multiple condition is what keeps this
    from becoming the earlier data-driven estimate, which returned 1 on any page where the text
    stream puts one figure per line and made "the gold figure is in some row" trivially true.

    Only the first ``periods`` columns are addressable as periods -- ROC filings print the period
    columns before the derived ones. A derived quantity is computed from the period cells by the
    numeric route rather than read out of a column, so nothing here needs to name 差異％.
    """
    if periods <= 0:
        return 0
    lengths = [len(row.figures) for row in rows if row.labelled and row.figures]
    if not lengths:
        return periods
    commonest = max(set(lengths), key=lengths.count)
    if commonest >= periods and commonest % periods == 0:
        return commonest
    return periods


def _fold(rows: list[Row], width: int) -> tuple[Row, ...]:
    """Split any row carrying a whole multiple of ``width`` figures into that many rows."""
    if width <= 0:
        return tuple(rows)
    folded: list[Row] = []
    for row in rows:
        count = len(row.figures)
        if count > width and count % width == 0:
            for start in range(0, count, width):
                folded.append(
                    Row(
                        label=row.label if start == 0 else "",
                        figures=row.figures[start : start + width],
                        labelled=start == 0 and row.labelled,
                    )
                )
        else:
            folded.append(row)
    return tuple(folded)


def read_page(text: str) -> list[PageTable]:
    """Every table on one page's text, split wherever a new period header appears.

    Content before the page's first period header is dropped: without column labels there is
    nothing to say which period a figure belongs to, and guessing is how a figure ends up filed
    under the wrong year.
    """
    tables: list[PageTable] = []
    periods: tuple[str, ...] | None = None
    rows: list[Row] = []
    pending_header: list[str] = []

    def close() -> None:
        nonlocal rows
        if periods is not None and rows:
            width = _table_width(rows, len(periods))
            tables.append(PageTable(periods=periods, rows=_fold(rows, width), width=width))
        rows = []

    for raw in text.splitlines():
        flat = _flatten(raw)
        if not flat:
            continue
        if _is_header(flat):
            # Consecutive header lines are one header: filings put each period on its own line.
            # The first of them ends whatever table was open -- that is the split.
            if not pending_header:
                close()
            pending_header.extend(match.group() for match in PERIOD.finditer(flat))
            continue
        if pending_header:
            periods = tuple(pending_header)
            pending_header = []
        if periods is None:
            continue
        label, figures = split_line(flat)
        if label and not _NAME.search(label):
            # A statement code or a percentage standing where a label would be. It names no
            # account, so its figures belong to the row already open.
            label = ""
        if label:
            rows.append(Row(label=label, figures=figures))
        elif figures and rows:
            last = rows[-1]
            rows[-1] = Row(label=last.label, figures=last.figures + figures, labelled=last.labelled)
        elif figures:
            rows.append(Row(label="", figures=figures, labelled=False))

    if pending_header:
        periods = tuple(pending_header)
    close()
    return tables


def matches_label(candidate: str, wanted: str) -> bool:
    """Containment either way on normalised, whitespace-free text.

    Either direction because a gold key writes 「存貨」 while a cell may read 「存 貨」 or
    「存貨淨額」 -- which of the two is longer depends on the filing, and demanding equality
    matched almost nothing on real pages.
    """
    left = re.sub(r"\s+", "", normalise(candidate))
    right = re.sub(r"\s+", "", normalise(wanted))
    if not left or not right:
        return False
    return left in right or right in left


def column_index(periods: tuple[str, ...], column_label: str) -> int | None:
    """Which column a period heading names, or ``None``.

    The longest matching heading wins: a bare 「112年」 matches several columns of a balance
    sheet, and the most specific one is the column that meant it.
    """
    best: tuple[int, int] | None = None
    for index, period in enumerate(periods):
        if matches_label(period, column_label):
            score = len(re.sub(r"\s+", "", normalise(period)))
            if best is None or score > best[1]:
                best = (index, score)
    return best[0] if best is not None else None


#: Shortest label that may match by containment from the candidate's side. A page's header often
#: arrives one character per line, and 「資」 is inside 「資產總計」 -- so a fragment would match
#: the account, win on position, and short-circuit resolution. Two characters is the shortest
#: real line item in these filings (「存貨」, 「股本」).
_MIN_CONTAINED = 2

#: How many characters longer than the account name a *heading* may be. 「十二、存貨」 is three
#: longer than 存貨 and 「存貨（附註十二）」 six; the prose paragraph on `2330-FY2024-FS` p41 that
#: mentions 存貨 is over forty. Eight admits the numbering and note references filings put around
#: a heading, and excludes a sentence.
_HEADING_SLACK = 8


def _best_row(table: PageTable, row_label: str) -> int | None:
    """The row that best names ``row_label``, or ``None``.

    Closest rather than first, which is a correctness requirement and not a refinement. Taking
    the first containment match let 「流動資產總計」 answer for 「資產總計」 -- printed earlier on a
    balance sheet, and containing it -- and let a one-character header fragment beat both. An
    exact match wins outright; among inexact ones the smallest difference in length wins.
    """
    wanted = re.sub(r"\s+", "", normalise(row_label))
    if not wanted:
        return None
    best: tuple[int, int] | None = None
    for position, row in enumerate(table.rows):
        if not row.labelled:
            continue
        label = re.sub(r"\s+", "", normalise(row.label))
        if not label:
            continue
        if label == wanted:
            return position
        if len(label) < _MIN_CONTAINED:
            continue
        if wanted in label or label in wanted:
            penalty = abs(len(label) - len(wanted))
            if best is None or penalty < best[1]:
                best = (position, penalty)
    return best[0] if best is not None else None


def resolve_row(table: PageTable, row_label: str, column_label: str) -> tuple[str, str] | None:
    """The cell text for one row and column, with the route that found it, or ``None``.

    Two routes, reported separately because they do not deserve equal confidence:

    ``row``
        A labelled row matched, and it carries one figure per period column. This is the
        ordinary case and the figure is where the filing put it.

    ``breakdown_total``
        The label matched a row with no figures of its own -- a breakdown heading such as 存貨
        above 製成品／在製品／原料 -- and the value taken is the unlabelled row that closes the
        breakdown. That is how filings write a total, but it *is* an inference about layout, so
        the route is returned and the caller records it. On `2330-FY2024-FS` p41 this is the
        only route to the inventory total, because the total row has no label.
    """
    index = column_index(table.periods, column_label)
    if index is None:
        return None

    matched = _best_row(table, row_label)
    if matched is None:
        return None

    row = table.rows[matched]
    if len(row.figures) == table.width:
        return row.figures[index], "row"
    if row.figures:
        # A labelled row of the wrong width is not something to index into: which column its
        # figures belong to is exactly what is unknown.
        return None

    return _breakdown_total(table, matched + 1, index)


def _breakdown_total(table: PageTable, start: int, index: int) -> tuple[str, str] | None:
    """The unlabelled row closing a breakdown, searched from ``start``.

    The last unlabelled full-width row before the next heading: filings print the total under a
    rule with no label of its own, and any later unlabelled row belongs to the next note.
    """
    total: Row | None = None
    for row in table.rows[start:]:
        if row.labelled and not row.figures:
            break
        if not row.labelled and len(row.figures) == table.width:
            total = row
    if total is None or index >= len(total.figures):
        return None
    return total.figures[index], "breakdown_total"


def resolve_page(
    tables: Sequence[PageTable], row_label: str, column_label: str
) -> tuple[str, str] | None:
    """Resolve one cell against every table on a page, following a heading across the split.

    Needed because a note heading and its breakdown end up in *different* tables: on
    `2330-FY2024-FS` p41 the heading 「十二、存貨」 is the last line before the breakdown's own
    period header, so :func:`read_page` closes one table on that header and the heading stays
    behind in the previous one. Searching each table in isolation therefore finds the heading
    with nothing under it and the total with no name above it.

    A heading only reaches forward into the table that immediately follows it, and only when that
    table has no row of its own bearing the name. Reaching further would let any heading claim
    any later total on the page.

    And it must actually look like a heading. `matches_label` is containment, so the sentence
    「本公司與存貨相關之營業成本中，包含將存貨成本沖減至淨變現價值⋯」 *contains* 存貨 and carries no
    figures -- a prose paragraph would otherwise qualify as the heading of whatever table follows
    it and hand back that table's total. :data:`_HEADING_SLACK` separates 「十二、存貨」 from a
    paragraph that happens to mention it.
    """
    for table in tables:
        found = resolve_row(table, row_label, column_label)
        if found is not None:
            return found
    wanted = len(re.sub(r"\s+", "", normalise(row_label)))
    for position, table in enumerate(tables[:-1]):
        candidate = _best_row(table, row_label)
        if candidate is None or table.rows[candidate].figures:
            continue
        if len(table.rows[candidate].label) > wanted + _HEADING_SLACK:
            continue
        following = tables[position + 1]
        index = column_index(following.periods, column_label)
        if index is None:
            continue
        found = _breakdown_total(following, 0, index)
        if found is not None:
            return found
    return None
