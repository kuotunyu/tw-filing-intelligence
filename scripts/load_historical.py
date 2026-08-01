"""Load the historical figures the gold questions need into the numeric store.

    uv run python scripts/load_historical.py
    uv run python scripts/load_historical.py --dry-run --set dev

TWSE's OpenAPI carries only the current period, so FY2023 and FY2024 figures come from tables
this repository extracted (`source_kind="extracted_table"`). R7 therefore requires the report
to say "verified structured data" rather than "official structured data".

Two properties, both enforced in `twfi.numeric.historical` and both worth repeating where a
reader will see them:

* **A figure that disagrees with gold is still loaded.** Agreement is reported, not required.
  Filtering on it would make F4 correct by construction.
* **Coverage is arranged, not achieved.** The store holds the accounts gold asks about,
  because loading every table in 2,895 pages is out of scope. "The numeric route had the
  figure it needed" is not a finding about coverage.

Writes `results/runs/historical_load.json` and, unless `--dry-run`, the DuckDB file. Exits
non-zero if any figure disagrees with gold -- that is a data-quality finding that should stop
a pipeline rather than scroll past.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Any

import typer

from twfi.console import use_utf8_output
from twfi.errors import ParsingError
from twfi.eval.gold import GoldRecord, load_gold
from twfi.io.manifest import load_acquisition_lock
from twfi.numeric.historical import Target, find_in_tables, outcome_of, parse_row_key
from twfi.numeric.store import LineItem, NumericStore
from twfi.parsing.tables import extract_tables
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


def targets_from(records: list[GoldRecord]) -> list[Target]:
    """Every gold record whose structured key names a single cell."""
    out: list[Target] = []
    for record in records:
        key = record.structured_source_key
        if key is None:
            continue
        parsed = parse_row_key(key.row_key)
        if parsed is None:
            continue
        doc_id, page, row_label, column_label = parsed
        if doc_id not in record.source_document:
            # A key naming a document the record does not cite is a bug in the key, not a
            # figure to go looking for.
            continue
        out.append(
            Target(
                question_id=record.question_id,
                doc_id=doc_id,
                company_code=record.company.code,
                page=page,
                row_label=row_label,
                column_label=column_label,
                basis=record.statement_basis or "consolidated",
                gold_answer=record.answer,
            )
        )
    return out


@app.command()
def main(
    gold_set: Annotated[str, typer.Option("--set", help="dev, locked, or all.")] = "all",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report without writing the database.")
    ] = False,
) -> None:
    """Extract the cells gold names and load them, agreement or not."""
    paths = repo_paths()
    paths.ensure_generated_dirs()
    lock = load_acquisition_lock(paths.acquisition_lock)

    if gold_set not in {"dev", "locked", "all"}:
        typer.echo(f"unknown set {gold_set!r}; choose dev, locked or all")
        raise typer.Exit(code=2)
    sources = {
        "dev": (paths.dev_gold,),
        "locked": (paths.locked_gold,),
        "all": (paths.dev_gold, paths.locked_gold),
    }[gold_set]

    records: list[GoldRecord] = []
    for path in sources:
        if path.is_file():
            records.extend(load_gold(path.read_text(encoding="utf-8").splitlines()))
    wanted = targets_from(records)
    typer.echo(f"{len(wanted)} target(s) named by {len(records)} gold record(s) in {gold_set}")
    if not wanted:
        typer.echo("nothing to load")
        raise typer.Exit(code=2)

    by_document: dict[str, list[Target]] = {}
    for target in wanted:
        by_document.setdefault(target.doc_id, []).append(target)

    rows: list[dict[str, Any]] = []
    items: list[LineItem] = []
    counts: Counter[str] = Counter()
    for doc_id, group in sorted(by_document.items()):
        acquired = lock.get(doc_id)
        if acquired is None or not acquired.local_path(paths.root).is_file():
            typer.echo(f"  {doc_id}: not acquired")
            counts["not_acquired"] += len(group)
            continue
        pages = sorted({target.page - 1 for target in group})
        try:
            tables = extract_tables(acquired.local_path(paths.root), pages=pages)
        except ParsingError as exc:
            typer.echo(f"  {doc_id}: {exc}")
            counts["unreadable"] += len(group)
            continue
        typer.echo(f"  {doc_id}: {len(tables)} table(s) over {len(pages)} cited page(s)")
        for target in group:
            loaded = find_in_tables(target, tables)
            state: str = outcome_of(loaded)
            counts[state] += 1
            if loaded.item is not None:
                items.append(loaded.item)
            rows.append(
                {
                    "question_id": target.question_id,
                    "doc_id": target.doc_id,
                    "page": target.page,
                    "account": target.account,
                    "column": target.column_label,
                    "outcome": state,
                    "extracted": str(loaded.item.value) if loaded.item else None,
                    "gold": target.gold_answer,
                    "cell_text": loaded.cell_text,
                    "period": loaded.item.period if loaded.item else None,
                    "unit": loaded.item.unit if loaded.item else None,
                    "problem": loaded.problem,
                }
            )
            mark = {"loaded": "ok  ", "missing": "?   ", "disagrees": "!!  "}[state]
            typer.echo(
                f"    {mark}{target.question_id}  {target.account:<12} {target.column_label:<16}"
                f" {str(loaded.item.value) if loaded.item else loaded.problem[:60]}"
            )

    if not dry_run and items:
        # Not `target`: that name belongs to the loop variable above, and shadowing it
        # here silently handed a Target to NumericStore.
        db_path = paths.duckdb / "numeric.duckdb"
        with NumericStore(db_path) as store:
            added = store.add_line_items(items)
            store.record_source(
                "extracted_table",
                f"gold-named cells ({gold_set})",
                loaded_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
                rows_loaded=added,
            )
        typer.echo("")
        typer.echo(f"loaded {added} figure(s) into {db_path.relative_to(paths.root)}")

    payload = {
        "gold_set": gold_set,
        "targets": len(wanted),
        "counts": dict(counts),
        "rows": rows,
        "note": (
            "Coverage is arranged: the store holds the accounts gold asks about. Agreement "
            "with gold is reported, never a condition for loading -- filtering on it would "
            "make F4 correct by construction."
        ),
    }
    destination = paths.runs / "historical_load.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    typer.echo("")
    for state in ("loaded", "disagrees", "missing", "not_acquired", "unreadable"):
        if counts[state]:
            typer.echo(f"  {state:<14} {counts[state]}")
    typer.echo(f"wrote: {destination.relative_to(paths.root)}")

    if counts["disagrees"]:
        typer.echo("")
        typer.echo(
            f"{counts['disagrees']} extracted figure(s) disagree with a gold answer read from "
            "pixels.\nOne of the two is wrong and the extractor is the more likely candidate, "
            "but that\nis a thing to look at rather than assume. The figures were loaded anyway: "
            "the store\nholds what the pipeline produces, and hiding a wrong one would flatter "
            "the result."
        )
        raise typer.Exit(code=1)


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
