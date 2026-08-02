"""Ingest every recognisable line item from every page, not only the cells gold names.

    uv run python scripts/load_all_rows.py --dry-run
    uv run python scripts/load_all_rows.py

**This exists to remove a caveat, not to raise a score.** `load_historical.py` loads the cells
named by gold's ``structured_source_key``, which is defensible for a feasibility study and is why
:mod:`twfi.numeric.historical` warns in its own docstring that "the numeric route had the figure
it needed" must never be reported as a coverage finding: it was arranged. F4's measured advantage
(D-042) inherits that caveat.

This script does what a real ingestion would: walk every page of every usable filing, reconstruct
its tables from the line stream (:mod:`twfi.numeric.rows`), and store every row whose account name
the study already recognises. Nothing here consults gold -- not the answers, not the pages, not
the row keys -- so whatever F4 scores against this store is a property of the pipeline rather than
of the question set.

**It writes to a different database, and that is the point.** The primary key includes
``source_kind`` and ``source_ref``, and
:func:`twfi.numeric.historical.find_in_text` already files its rows under
``extracted_text_row``. Loading these into ``numeric.duckdb`` would overwrite some gold-loaded
figures outright and, for the rest, stand up a second candidate beside every ``extracted_table``
row -- which is precisely when :meth:`NumericStore.require` refuses to choose. Different pages
within one source also survive for the same reason: subsidiary notes must not silently replace
the consolidated statement. F4 would *drop*,
and the recorded D-042 number would silently stop describing the store it was measured on. So
this builds ``numeric_broad.duckdb`` alongside, and the two are compared rather than merged.

Three filters, all deliberate, none derived from gold:

* **The account must be one the study can classify.** ``statement_of`` maps a name to income,
  balance, ratio or monthly revenue; a figure filed under an unknown statement cannot be compared
  with anything. That vocabulary is a financial-reporting fact, not an answer key.
* **The period column must name a single fiscal year.** A column headed 「111年度及112年度」 spans
  two, and filing one figure under one of them would put a number in the wrong year.
* **The page must state a unit, or inherit one from an earlier page of the same filing.**
  :func:`twfi.numeric.calculator.check_comparable` refuses a figure whose unit was never stated,
  so a unitless row would load and then be unusable in every ratio and every delta.

**The basis is read off the page, not assumed.** A ROC annual report prints the five-year summary
twice, back to back and under identical account names: `1301-FY2023-AR` p176 is
「簡明資產負債表-合併財務報告」 and p177 is 「簡明資產負債表-個體財務報告」. Filing both as
consolidated -- which the first version of this script did -- put 非流動負債 FY2023 at both
80,276,535 and 76,380,920 under one key, and the store kept whichever page was written last.
DEV-0009 scored correct off that store only because p188 happens to follow p177 in page order.
``basis`` is part of the primary key, so reading the heading separates the two rather than letting
page order decide.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated

import typer

from twfi.console import use_utf8_output
from twfi.errors import ParsingError
from twfi.io.manifest import load_acquisition_lock
from twfi.numeric.historical import period_of_column, schema_of, statement_of, to_decimal
from twfi.numeric.rows import read_page
from twfi.numeric.store import CompanyRow, LineItem, NumericStore
from twfi.parsing.baseline import parse_baseline
from twfi.parsing.normalise import normalise
from twfi.parsing.tables import UnitSpec, detect_basis, detect_unit
from twfi.paths import repo_paths
from twfi.protocol import COMPANIES, USABLE_DOCUMENTS

app = typer.Typer(add_completion=False, help=__doc__)

#: Never the store `load_historical.py` writes. See the module docstring.
PRIMARY_DATABASE = "numeric.duckdb"
BROAD_DATABASE = "numeric_broad.duckdb"


@app.command()
def main(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would load without writing.")
    ] = False,
    database: Annotated[
        str, typer.Option(help="Database filename under data/duckdb/.")
    ] = BROAD_DATABASE,
) -> None:
    """Walk every usable filing and store every line item it can classify."""
    if database == PRIMARY_DATABASE:
        raise typer.BadParameter(
            f"{PRIMARY_DATABASE} is the gold-keyed store that D-042's F4 number was measured on. "
            "Loading broad rows into it would overwrite figures and make require() ambiguous. "
            f"Use {BROAD_DATABASE} and compare the two."
        )

    paths = repo_paths()
    lock = load_acquisition_lock(paths.acquisition_lock)
    items: list[LineItem] = []
    reasons: Counter[str] = Counter()
    per_document: Counter[str] = Counter()

    for document in USABLE_DOCUMENTS:
        acquired = lock.get(document.doc_id)
        if acquired is None or not acquired.local_path(paths.root).is_file():
            typer.echo(f"  {document.doc_id}: not acquired")
            continue
        try:
            parsed = parse_baseline(acquired.local_path(paths.root), document.doc_id)
        except ParsingError as exc:
            typer.echo(f"  {document.doc_id}: {exc}")
            continue

        pages: dict[int, list[str]] = {}
        for block in parsed.blocks:
            pages.setdefault(block.page, []).append(block.text)

        # A filing declares 單位：新台幣仟元 once, above the first statement, and the pages after
        # it inherit silently. Carrying the last declaration forward is an inference, so it is
        # recorded in `unit_note` rather than presented as something the page said.
        carried: UnitSpec | None = None
        carried_page: int | None = None

        for page, texts in sorted(pages.items()):
            text = normalise("\n".join(texts))
            spec = detect_unit(text)
            if spec.is_stated:
                carried, carried_page = spec, page
                note: str | None = None
            elif carried is not None:
                spec, note = carried, f"unit inherited from p{carried_page}, not stated on p{page}"
            else:
                reasons["no unit declared on or before this page"] += 1
                continue

            basis = detect_basis(text)
            for table in read_page(text):
                for row in table.rows:
                    if not row.labelled or len(row.figures) != table.width:
                        continue
                    statement = statement_of(row.label)
                    if statement is None:
                        reasons["account not classifiable"] += 1
                        continue
                    for index, column in enumerate(table.periods):
                        if index >= len(row.figures):
                            break
                        period = period_of_column(column)
                        if period is None:
                            reasons["column names no single year"] += 1
                            continue
                        value = to_decimal(row.figures[index])
                        if value is None:
                            reasons["cell holds no figure"] += 1
                            continue
                        items.append(
                            LineItem(
                                company_code=document.company_code,
                                period=period,
                                statement=statement,
                                basis=basis,
                                industry_schema=schema_of(document.company_code),
                                account=normalise(row.label).strip(),
                                value=value,
                                unit=spec.unit,
                                currency=spec.currency,
                                source_kind="extracted_text_row",
                                source_ref=f"{document.doc_id}|p{page}|{row.label}|{column}",
                                unit_is_uniform=spec.exception is None,
                                unit_note=spec.exception or note,
                            )
                        )
                        per_document[document.doc_id] += 1
        typer.echo(f"  {document.doc_id}: {per_document[document.doc_id]:>6,} line item(s)")

    typer.echo("")
    typer.echo(f"{len(items):,} line item(s) from {len(per_document)} document(s)")
    for reason, count in reasons.most_common():
        typer.echo(f"  skipped {count:>7,}: {reason}")

    if dry_run:
        typer.echo("")
        typer.echo("--dry-run: nothing written")
        return

    target = paths.duckdb / database
    target.parent.mkdir(parents=True, exist_ok=True)
    with NumericStore(target) as store:
        store.add_companies(CompanyRow(c.code, c.name, schema_of(c.code)) for c in COMPANIES)
        added = store.add_line_items(items)
        typer.echo("")
        typer.echo(f"loaded {added:,} row(s) into {database}; it now holds {store.count():,}")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
